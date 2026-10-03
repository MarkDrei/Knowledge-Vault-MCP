"""SQLite persistence for OAuth clients, codes and tokens.

Kept in its own state database (not the search index): the index is derived and may be
deleted at any time, but losing this file would log out every connected client.
Codes and tokens are stored only as SHA-256 hashes.
"""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    data      TEXT NOT NULL,
    created   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_grants (
    token_hash TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('code', 'access', 'refresh')),
    client_id  TEXT NOT NULL,
    family     TEXT NOT NULL,          -- links an access token to its refresh token
    data       TEXT NOT NULL,
    expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS oauth_grants_family ON oauth_grants (family);
"""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class StateStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    # clients
    def put_client(self, client_id: str, data: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO oauth_clients VALUES (?, ?, ?)",
                (client_id, json.dumps(data), int(time.time())),
            )

    def get_client(self, client_id: str) -> dict | None:
        row = self._db.execute("SELECT data FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # codes and tokens
    def put_grant(
        self, token: str, kind: str, client_id: str, family: str, data: dict, expires_at: int | None
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO oauth_grants VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash(token), kind, client_id, family, json.dumps(data), expires_at),
            )

    def get_grant(self, token: str, kind: str) -> tuple[str, dict] | None:
        """Return (family, data) of an unexpired grant, or None."""
        row = self._db.execute(
            "SELECT family, data, expires_at FROM oauth_grants WHERE token_hash = ? AND kind = ?",
            (token_hash(token), kind),
        ).fetchone()
        if not row:
            return None
        family, data, expires_at = row
        if expires_at is not None and expires_at < time.time():
            return None
        return family, json.loads(data)

    def delete_grant(self, token: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM oauth_grants WHERE token_hash = ?", (token_hash(token),))
            return cur.rowcount > 0

    def delete_family(self, family: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM oauth_grants WHERE family = ?", (family,))

    def purge_expired(self) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM oauth_grants WHERE expires_at IS NOT NULL AND expires_at < ?",
                (int(time.time()),),
            )
