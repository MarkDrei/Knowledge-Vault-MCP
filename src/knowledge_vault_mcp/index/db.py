"""The search index database (`index.db`): schema and connections.

Derived data only (ADR-0002): on a schema or embedding model change the tables are
dropped and rebuilt from the vault. Every thread gets its own connection; writes are
serialized by the vault lock, reads run concurrently thanks to WAL mode.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    path         TEXT PRIMARY KEY,   -- vault-relative POSIX path
    kind         TEXT NOT NULL,      -- md | pdf | docx | html
    title        TEXT NOT NULL,
    frontmatter  TEXT NOT NULL,      -- JSON object
    status       TEXT,               -- frontmatter `status`, e.g. inbox
    modified     INTEGER,            -- unix time of the last commit touching the file
    content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_status ON notes (status);
CREATE TABLE IF NOT EXISTS tags (
    path TEXT NOT NULL,
    tag  TEXT NOT NULL,
    PRIMARY KEY (path, tag)
);
CREATE INDEX IF NOT EXISTS tags_tag ON tags (tag);
CREATE TABLE IF NOT EXISTS links (
    src        TEXT NOT NULL,        -- linking note
    target_key TEXT NOT NULL,        -- normalized target, see vault.markdown.link_key
    target     TEXT NOT NULL,        -- target as written
    heading    TEXT,
    alias      TEXT,
    embed      INTEGER NOT NULL,
    line       INTEGER NOT NULL,
    context    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS links_src ON links (src);
CREATE INDEX IF NOT EXISTS links_target ON links (target_key);
CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY,
    path      TEXT NOT NULL,
    ord       INTEGER NOT NULL,      -- position within the note
    headings  TEXT NOT NULL,         -- JSON list: heading path of the section
    text      TEXT NOT NULL,
    embedding BLOB                   -- float32 vector (sqlite-vec format)
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks (path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    title, headings, text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_TABLES = ("meta", "notes", "tags", "links", "chunks", "chunks_fts")


class IndexDB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        conn = self.conn()
        if self.get_meta("schema_version") != SCHEMA_VERSION:
            self._drop(conn)
        conn.executescript(_SCHEMA)
        self.set_meta("schema_version", SCHEMA_VERSION)

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self):
        conn = self.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _drop(self, conn: sqlite3.Connection) -> None:
        for table in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")  # noqa: S608 - fixed names

    def reset(self) -> None:
        """Delete all indexed data (used before a full rebuild)."""
        conn = self.conn()
        self._drop(conn)
        conn.executescript(_SCHEMA)
        self.set_meta("schema_version", SCHEMA_VERSION)

    def get_meta(self, key: str) -> str | None:
        try:
            row = self.conn().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:  # table missing
            return None
        return row[0] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        self.conn().execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def note_count(self) -> int:
        return self.conn().execute("SELECT count(*) FROM notes").fetchone()[0]

    def chunk_count(self) -> int:
        return self.conn().execute("SELECT count(*) FROM chunks").fetchone()[0]
