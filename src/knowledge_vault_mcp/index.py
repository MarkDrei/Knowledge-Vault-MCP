"""Persistent hybrid index: heading-based chunks in SQLite (FTS5 BM25 + sqlite-vec), fused with RRF.

The index is derived data. It is updated incrementally by comparing file signatures and content
hashes, and rebuilt completely if the embedding model changes. Without an embedder only the
keyword part is used.
"""

import hashlib
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import sqlite_vec

log = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 1200
CANDIDATES = 100
RRF_K = 60
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


class Embedder(Protocol):
    dim: int
    name: str

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """multilingual-e5 style model on CPU via fastembed (ONNX); adds the required prefixes."""

    def __init__(self, model: str, cache_dir: Path | None = None):
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType

        supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model not in supported:
            TextEmbedding.add_custom_model(
                model=model,
                pooling=PoolingType.MEAN,
                normalization=True,
                sources=ModelSource(hf=model),
                dim=384,
                model_file="onnx/model.onnx",
            )
        self.name = model
        self._model = TextEmbedding(model, cache_dir=str(cache_dir) if cache_dir else None)
        self.dim = len(next(iter(self._model.embed(["dim probe"]))))

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed([f"passage: {t}" for t in texts], batch_size=16)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.embed([f"query: {text}"]))).tolist()


def chunk_markdown(title: str, body: str, max_chars: int = MAX_CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split by heading structure; returns (heading_path, text). Long sections split on paragraphs."""
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    path = title
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            sections.append((path, current))
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
            parts = [title, *(h for _, h in stack)]
            path = " > ".join(x for i, x in enumerate(parts) if i == 0 or x != parts[i - 1])
            current = []
        else:
            current.append(line)
    sections.append((path, current))

    chunks: list[tuple[str, str]] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        buf = ""
        for para in re.split(r"\n\s*\n", text):
            while len(para) > max_chars:
                cut = para.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                if buf:
                    chunks.append((heading, buf))
                    buf = ""
                chunks.append((heading, para[:cut].strip()))
                para = para[cut:].strip()
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append((heading, buf))
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            chunks.append((heading, buf))
    return chunks


def fts_query(query: str) -> str:
    words = re.findall(r"\w+", query, flags=re.UNICODE)
    return " OR ".join(f'"{w}"*' for w in words)


class Indexer:
    def __init__(self, db_path: Path | str = ":memory:", embedder: Embedder | None = None):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS notes (
                path TEXT PRIMARY KEY, sig TEXT, hash TEXT, title TEXT, tags TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY, path TEXT, heading TEXT, text TEXT);
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title, tags, heading, text, tokenize='unicode61 remove_diacritics 2');
            """
        )
        self._check_model()

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _check_model(self) -> None:
        wanted = f"{self.embedder.name}:{self.embedder.dim}" if self.embedder else "none"
        if self._meta("model") != wanted:
            log.info("Embedding model changed to %s; rebuilding index", wanted)
            self.reset()
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('model', ?)", (wanted,))
            self.db.commit()

    def reset(self) -> None:
        with self.lock:
            self.db.execute("DROP TABLE IF EXISTS chunks_vec")
            for t in ("notes", "chunks", "chunks_fts"):
                self.db.execute(f"DELETE FROM {t}")  # noqa: S608
            if self.embedder:
                dim = self.embedder.dim
                self.db.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])"
                )
            self.db.commit()

    # ---- indexing ----------------------------------------------------------------------------
    def sync(
        self, entries: Sequence[tuple[str, str, Callable[[], tuple[str, str, list[str], str, str]]]]
    ) -> dict:
        """entries: (path, signature, loader -> (raw_hash_source, title, tags, status, body)).

        Returns counts of added/updated, unchanged and removed notes.
        """
        stats = {"indexed": 0, "unchanged": 0, "removed": 0}
        with self.lock:
            known = {p: (s, h) for p, s, h in self.db.execute("SELECT path, sig, hash FROM notes")}
            seen = set()
            for path, sig, loader in entries:
                seen.add(path)
                if path in known and known[path][0] == sig:
                    stats["unchanged"] += 1
                    continue
                raw, title, tags, status, body = loader()
                digest = hashlib.sha256(raw.encode()).hexdigest()
                if path in known and known[path][1] == digest:
                    self.db.execute("UPDATE notes SET sig = ? WHERE path = ?", (sig, path))
                    stats["unchanged"] += 1
                    continue
                self._remove(path)
                self._add(path, sig, digest, title, tags, status, body)
                stats["indexed"] += 1
            for path in set(known) - seen:
                self._remove(path)
                stats["removed"] += 1
            self.db.commit()
        return stats

    def _remove(self, path: str) -> None:
        ids = [r[0] for r in self.db.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        for cid in ids:
            self.db.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            if self.embedder:
                self.db.execute("DELETE FROM chunks_vec WHERE rowid = ?", (cid,))
        self.db.execute("DELETE FROM chunks WHERE path = ?", (path,))
        self.db.execute("DELETE FROM notes WHERE path = ?", (path,))

    def _add(
        self, path: str, sig: str, digest: str, title: str, tags: list[str], status: str, body: str
    ) -> None:
        tag_str = " ".join(tags)
        self.db.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)", (path, sig, digest, title, tag_str, status)
        )
        chunks = chunk_markdown(title, body) or [(title, title)]
        vectors = self.embedder.embed_passages([f"{h}\n{t}" for h, t in chunks]) if self.embedder else []
        for i, (heading, text) in enumerate(chunks):
            cur = self.db.execute(
                "INSERT INTO chunks (path, heading, text) VALUES (?, ?, ?)", (path, heading, text)
            )
            cid = cur.lastrowid
            self.db.execute(
                "INSERT INTO chunks_fts (rowid, title, tags, heading, text) VALUES (?, ?, ?, ?, ?)",
                (cid, title, tag_str, heading, text),
            )
            if self.embedder:
                self.db.execute(
                    "INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)",
                    (cid, sqlite_vec.serialize_float32(vectors[i])),
                )

    # ---- searching ---------------------------------------------------------------------------
    def search(
        self, query: str, limit: int = 10, path_prefix: str = "", tag: str = "", status: str = ""
    ) -> list[dict]:
        prefix = path_prefix.lstrip("/")
        tag = tag.lstrip("#")
        with self.lock:
            bm25_ids: list[int] = []
            q = fts_query(query)
            if q:
                bm25_ids = [
                    r[0]
                    for r in self.db.execute(
                        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                        "ORDER BY bm25(chunks_fts, 5, 3, 3, 1) LIMIT ?",
                        (q, CANDIDATES),
                    )
                ]
            vec_ids: list[int] = []
            if self.embedder and query.strip():
                blob = sqlite_vec.serialize_float32(self.embedder.embed_query(query))
                vec_ids = [
                    r[0]
                    for r in self.db.execute(
                        "SELECT rowid FROM chunks_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                        (blob, CANDIDATES),
                    )
                ]
            scores: dict[int, float] = {}
            for ids in (bm25_ids, vec_ids):
                for rank, cid in enumerate(ids):
                    scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            hits = []
            for cid, score in sorted(scores.items(), key=lambda kv: -kv[1]):
                row = self.db.execute(
                    "SELECT c.path, c.heading, c.text, n.title, n.tags, n.status FROM chunks c "
                    "JOIN notes n ON n.path = c.path WHERE c.id = ?",
                    (cid,),
                ).fetchone()
                if row is None:
                    continue
                path, heading, text, title, tags, st = row
                if prefix and not path.startswith(prefix):
                    continue
                if tag and tag not in tags.split():
                    continue
                if status and st != status:
                    continue
                hits.append(
                    {
                        "path": path,
                        "title": title,
                        "heading": heading,
                        "tags": tags.split(),
                        "status": st,
                        "snippet": text if len(text) <= 500 else text[:500] + " …",
                        "score": round(score, 5),
                        "matched_by": [
                            n for n, ids in (("keyword", bm25_ids), ("semantic", vec_ids)) if cid in ids
                        ],
                    }
                )
                if len(hits) >= limit:
                    break
        return hits
