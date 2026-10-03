"""Read/search/write access to a git-backed Markdown vault.

Search is keyword-only (SQLite FTS5, BM25) for now; semantic search is a later roadmap step.
All writes run under one lock and follow ADR-0006: pull --rebase, change, commit, push; on any
failure the local branch is reset to its previous state.
"""

import re
import sqlite3
import subprocess  # noqa: S404
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HIDDEN_DIRS = {".git", ".obsidian", ".trash"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
INLINE_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z][\w/-]*)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


class VaultError(Exception):
    """Raised for invalid requests and failed git operations; the message is shown to the client."""


@dataclass
class Note:
    path: str
    title: str
    body: str
    raw: str
    tags: list[str]
    links: list[str]


def split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end == -1:
        return {}, raw
    block = raw[4:end]
    body = raw[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    key = None
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key = m.group(1)
            meta[key] = m.group(2).strip()
        elif key and line.lstrip().startswith("-"):
            meta[key] = (meta[key] + "," + line.lstrip()[1:].strip()).strip(",")
    return meta, body


def parse_tags(meta: dict[str, str], body: str) -> list[str]:
    tags: set[str] = set()
    raw = meta.get("tags", "")
    for t in re.split(r"[,\s\[\]]+", raw):
        t = t.strip().strip("'\"").lstrip("#")
        if t:
            tags.add(t)
    tags.update(INLINE_TAG_RE.findall(body))
    return sorted(tags)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "note"


def fts_query(query: str) -> str:
    words = re.findall(r"\w+", query, flags=re.UNICODE)
    return " OR ".join(f'"{w}"*' for w in words)


class Vault:
    def __init__(
        self,
        path: Path,
        inbox_dir: str = "_inbox",
        timezone: str = "Europe/Berlin",
        git_sync: bool = True,
        branch: str = "main",
    ):
        self.root = Path(path).resolve()
        self.inbox_dir = inbox_dir.strip("/")
        self.tz = ZoneInfo(timezone)
        self.git_sync = git_sync
        self.branch = branch
        self._lock = threading.RLock()
        self._index: sqlite3.Connection | None = None
        self._signature: tuple | None = None

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    # ---- paths -------------------------------------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        rel = rel.strip().lstrip("/")
        if not rel.lower().endswith(".md"):
            rel += ".md"
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root) or HIDDEN_DIRS & set(p.relative_to(self.root).parts):
            raise VaultError(f"Invalid path: {rel}")
        return p

    def _files(self) -> list[Path]:
        return sorted(
            p
            for p in self.root.rglob("*.md")
            if p.is_file() and not (HIDDEN_DIRS & set(p.relative_to(self.root).parts))
        )

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ---- reading -----------------------------------------------------------------------------
    def _load(self, p: Path) -> Note:
        raw = p.read_text(encoding="utf-8", errors="replace")
        meta, body = split_frontmatter(raw)
        title = meta.get("title", "").strip("'\"")
        if not title:
            m = re.search(r"^#\s+(.*)$", body, re.MULTILINE)
            title = m.group(1).strip() if m else p.stem
        links = sorted({m.strip() for m in WIKILINK_RE.findall(body)})
        return Note(self._rel(p), title, body, raw, parse_tags(meta, body), links)

    def get_note(self, path: str) -> Note:
        p = self._resolve(path)
        if not p.is_file():
            raise VaultError(f"Note not found: {path}")
        return self._load(p)

    def list_notes(self, path_prefix: str = "", limit: int = 200) -> list[dict]:
        out = []
        for p in self._files():
            rel = self._rel(p)
            if path_prefix and not rel.startswith(path_prefix.lstrip("/")):
                continue
            out.append({"path": rel, "title": self._load(p).title})
            if len(out) >= limit:
                break
        return out

    def get_backlinks(self, path: str) -> list[dict]:
        target = self._resolve(path)
        names = {target.stem.lower(), self._rel(target)[:-3].lower()}
        result = []
        for p in self._files():
            if p == target:
                continue
            note = self._load(p)
            if any(link.lower().removesuffix(".md") in names for link in note.links):
                result.append({"path": note.path, "title": note.title})
        return result

    # ---- search ------------------------------------------------------------------------------
    def _ensure_index(self) -> sqlite3.Connection:
        files = self._files()
        sig = tuple((str(p), p.stat().st_mtime_ns) for p in files)
        if self._index is not None and sig == self._signature:
            return self._index
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.execute(
            "CREATE VIRTUAL TABLE notes USING fts5(path UNINDEXED, title, tags, body, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        for p in files:
            n = self._load(p)
            db.execute("INSERT INTO notes VALUES (?, ?, ?, ?)", (n.path, n.title, " ".join(n.tags), n.body))
        db.commit()
        if self._index is not None:
            self._index.close()
        self._index, self._signature = db, sig
        return db

    def search(self, query: str, limit: int = 10, path_prefix: str = "", tag: str = "") -> list[dict]:
        q = fts_query(query)
        if not q:
            return []
        with self._lock:
            db = self._ensure_index()
            rows = db.execute(
                "SELECT path, title, tags, snippet(notes, 3, '[', ']', ' … ', 24), bm25(notes, 0, 5, 3, 1) "
                "FROM notes WHERE notes MATCH ? ORDER BY 5 LIMIT 200",
                (q,),
            ).fetchall()
        hits = []
        for path, title, tags, snippet, score in rows:
            if path_prefix and not path.startswith(path_prefix.lstrip("/")):
                continue
            if tag and tag.lstrip("#") not in tags.split():
                continue
            hits.append(
                {"path": path, "title": title, "tags": tags.split(), "snippet": snippet, "score": -score}
            )
            if len(hits) >= limit:
                break
        return hits

    # ---- writing -----------------------------------------------------------------------------
    def _git(self, *args: str) -> str:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise VaultError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout.strip()

    def _write(self, files: dict[str, str], message: str) -> None:
        with self._lock:
            if self._git("status", "--porcelain"):
                raise VaultError("Vault working tree has uncommitted changes; refusing to write.")
            before = self._git("rev-parse", "HEAD")
            try:
                if self.git_sync:
                    self._git("pull", "--rebase", "origin", self.branch)
                for rel, content in files.items():
                    p = self._resolve(rel)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")
                self._git("add", "--", *files)
                self._git(
                    "-c", "user.name=Knowledge Vault MCP", "-c", "user.email=kvault@localhost",
                    "commit", "-m", message,
                )  # fmt: skip
                if self.git_sync:
                    self._git("push", "origin", f"HEAD:{self.branch}")
            except VaultError:
                subprocess.run(
                    ["git", "-C", str(self.root), "rebase", "--abort"], check=False, capture_output=True
                )  # noqa: S603, S607
                self._git("reset", "--hard", before)
                raise
            self._signature = None

    def add_note(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        source_url: str = "",
        captured_by: str = "claude",
    ) -> str:
        now = datetime.now(self.tz).replace(microsecond=0)
        stem = f"{now:%Y-%m-%d-%H%M%S}-{slugify(title)}"
        rel, n = f"{self.inbox_dir}/{stem}.md", 2
        while self._resolve(rel).exists():
            rel = f"{self.inbox_dir}/{stem}-{n}.md"
            n += 1
        stem = rel.rsplit("/", 1)[-1][:-3]
        safe_title = title.replace('"', "'").replace("\n", " ")
        meta = (
            f'---\nid: {stem}\ntitle: "{safe_title}"\ncreated: {now.isoformat()}\n'
            f"updated: {now.isoformat()}\n"
            f"status: inbox\nsource: mcp\ncaptured_by: {captured_by}\nsource_url: {source_url}\n"
            f"tags: [{', '.join(t.lstrip('#') for t in tags or [])}]\n---\n\n"
        )
        self._write({rel: meta + body.strip() + "\n"}, f"kvault: add note {stem}")
        return rel

    def append_note(self, path: str, text: str) -> str:
        note = self.get_note(path)
        self._write(
            {note.path: note.raw.rstrip("\n") + "\n\n" + text.strip() + "\n"},
            f"kvault: append to {note.path}",
        )
        return note.path

    def update_note(self, path: str, content: str) -> str:
        note = self.get_note(path)
        self._write({note.path: content}, f"kvault: update {note.path}")
        return note.path
