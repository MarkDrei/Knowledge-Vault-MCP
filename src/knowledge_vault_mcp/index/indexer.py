"""Turns vault files into notes, tags, links, chunks and embeddings in `index.db`.

`update()` brings the index to the vault's current commit. It diffs the indexed commit
against HEAD and re-indexes only the changed files; it falls back to a full scan when there
is no usable previous commit (first start, rewritten history, changed embedding model).
Files whose content hash did not change are not re-embedded.
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field

from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.index.chunker import Chunk, chunk_markdown
from knowledge_vault_mcp.index.db import IndexDB
from knowledge_vault_mcp.index.embedder import Embedder, to_blob
from knowledge_vault_mcp.index.extract import extract
from knowledge_vault_mcp.vault.markdown import WikiLink, parse_note
from knowledge_vault_mcp.vault.paths import is_document, is_indexable, is_markdown, kind_of
from knowledge_vault_mcp.vault.vault import Vault

log = logging.getLogger(__name__)


@dataclass
class Document:
    """What the indexer stores for one file."""

    title: str
    frontmatter: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    links: list[WikiLink] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


@dataclass
class IndexStats:
    mode: str = "none"  # none | incremental | full
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    seconds: float = 0.0


class Indexer:
    def __init__(self, settings: Settings, db: IndexDB, vault: Vault, embedder: Embedder):
        self.settings = settings
        self.db = db
        self.vault = vault
        self.embedder = embedder

    # ---- entry points ----
    def update(self, force_full: bool = False) -> IndexStats:
        """Index everything that changed since the last run. Holds the vault lock throughout."""
        with self.vault.lock:
            started = time.monotonic()
            head = self.vault.repo.head()
            if self.db.get_meta("embedding_model") != self.embedder.name:
                force_full = True
                self.db.reset()
            indexed = self.db.get_meta("indexed_commit")
            changes = None if force_full else self.vault.changes(indexed, head)
            stats = self._full(head) if changes is None else self._incremental(changes, indexed, head)
            self.db.set_meta("indexed_commit", head)
            self.db.set_meta("embedding_model", self.embedder.name)
            self.db.set_meta("indexed_at", str(int(time.time())))
            stats.seconds = round(time.monotonic() - started, 3)
            if stats.mode != "none":
                log.info("index %s: %s", stats.mode, stats)
            return stats

    def index_paths(self, paths: list[str]) -> None:
        """Re-index specific files right after a write (deleted files are removed)."""
        with self.vault.lock:
            times = self._commit_times(None, paths)
            for path in paths:
                if not is_indexable(path):
                    continue
                if self.vault.exists(path):
                    self._index_file(path, times.get(path), IndexStats())
                else:
                    self.remove(path)
            self.db.set_meta("indexed_commit", self.vault.repo.head())

    # ---- full / incremental ----
    def _full(self, head: str | None) -> IndexStats:
        stats = IndexStats(mode="full")
        files = [p for p in self.vault.files() if is_indexable(p)]
        times = self.vault.repo.last_commit_times() if head else {}
        for path in files:
            self._index_file(path, times.get(path), stats)
        known = {row[0] for row in self.db.conn().execute("SELECT path FROM notes")}
        for path in known - set(files):
            self.remove(path)
            stats.removed += 1
        return stats

    def _incremental(self, changes, old: str | None, new: str | None) -> IndexStats:
        stats = IndexStats(mode="incremental" if changes else "none")
        relevant = [c for c in changes if is_indexable(c.path)]
        times = self._commit_times(f"{old}..{new}", [c.path for c in relevant])
        for change in relevant:
            if change.status == "D" or not self.vault.exists(change.path):
                self.remove(change.path)
                stats.removed += 1
            else:
                self._index_file(change.path, times.get(change.path), stats)
        return stats

    def _commit_times(self, rev_range: str | None, paths: list[str]) -> dict[str, int]:
        if not paths or self.vault.repo.head() is None:
            return {}
        if rev_range:
            return self.vault.repo.last_commit_times(rev_range)
        return {p: t for p, t in self.vault.repo.last_commit_times("-1").items() if p in paths}

    # ---- single file ----
    def _index_file(self, path: str, modified: int | None, stats: IndexStats) -> None:
        try:
            data = self.vault.read_bytes(path)
        except (OSError, PermissionError) as e:
            log.warning("cannot read %s: %s", path, e)
            stats.failed += 1
            return
        if modified is None:
            modified = int(os.path.getmtime(self.vault.abspath(path)))
        digest = hashlib.sha256(data).hexdigest()
        row = self.db.conn().execute("SELECT content_hash FROM notes WHERE path = ?", (path,)).fetchone()
        if row and row[0] == digest:
            self.db.conn().execute("UPDATE notes SET modified = ? WHERE path = ?", (modified, path))
            stats.unchanged += 1
            return
        try:
            doc = self.build_document(path, data)
            vectors = self.embedder.embed_passages([self._passage(doc, c) for c in doc.chunks])
        except Exception:  # one broken file must not stop the whole index run
            log.exception("failed to index %s", path)
            stats.failed += 1
            return
        with self.db.transaction() as conn:
            self._delete_rows(conn, path)
            status = doc.frontmatter.get("status")
            conn.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    path,
                    kind_of(path),
                    doc.title,
                    json.dumps(doc.frontmatter, ensure_ascii=False),
                    str(status) if status is not None else None,
                    modified,
                    digest,
                ),
            )
            conn.executemany("INSERT OR IGNORE INTO tags VALUES (?, ?)", [(path, t) for t in doc.tags])
            conn.executemany(
                "INSERT INTO links VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (path, ln.key, ln.target, ln.heading, ln.alias, int(ln.embed), ln.line, ln.context)
                    for ln in doc.links
                ],
            )
            for ord_, (chunk, vector) in enumerate(zip(doc.chunks, vectors, strict=True)):
                headings = json.dumps(chunk.headings, ensure_ascii=False)
                cur = conn.execute(
                    "INSERT INTO chunks (path, ord, headings, text, embedding) VALUES (?, ?, ?, ?, ?)",
                    (path, ord_, headings, chunk.text, to_blob(vector)),
                )
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, title, headings, text) VALUES (?, ?, ?, ?)",
                    (cur.lastrowid, doc.title, " ".join(chunk.headings), chunk.text),
                )
        stats.indexed += 1

    def build_document(self, path: str, data: bytes) -> Document:
        max_chars, overlap = self.settings.chunk_max_chars, self.settings.chunk_overlap
        if is_markdown(path):
            note = parse_note(path, data.decode("utf-8", errors="replace"))
            chunks = chunk_markdown(note.body, max_chars, overlap) or [Chunk([], note.title)]
            return Document(note.title, note.frontmatter, note.tags, note.links, chunks)
        if is_document(path):
            self._check_size(path, data)
            doc = extract(path, data)
            chunks = chunk_markdown(doc.text, max_chars, overlap) or [Chunk([], doc.title)]
            return Document(doc.title, chunks=chunks)
        raise ValueError(f"unsupported file type: {path}")

    def _check_size(self, path: str, data: bytes) -> None:
        limit = self.settings.max_document_mb * 1024 * 1024
        if len(data) > limit:
            raise ValueError(f"{path} is larger than MAX_DOCUMENT_MB={self.settings.max_document_mb}")

    def extract_text(self, path: str, data: bytes) -> str:
        """Plain text of a file as the indexer sees it (Markdown is returned unchanged)."""
        if is_markdown(path):
            return data.decode("utf-8", errors="replace")
        self._check_size(path, data)
        return extract(path, data).text

    @staticmethod
    def _passage(doc: Document, chunk: Chunk) -> str:
        context = " > ".join([doc.title, *chunk.headings])
        return f"{context}\n\n{chunk.text}"

    def remove(self, path: str) -> None:
        with self.db.transaction() as conn:
            self._delete_rows(conn, path)

    @staticmethod
    def _delete_rows(conn, path: str) -> None:
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
        conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
        conn.execute("DELETE FROM links WHERE src = ?", (path,))
        conn.execute("DELETE FROM tags WHERE path = ?", (path,))
        conn.execute("DELETE FROM notes WHERE path = ?", (path,))
