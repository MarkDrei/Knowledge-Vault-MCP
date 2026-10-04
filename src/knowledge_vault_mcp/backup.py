"""Online backup of `state.db` (`kvault backup`).

Uses SQLite's backup API, so it is consistent while the server is running (copying the
file could catch a half-written WAL). `index.db`, the vault clone and the model cache are
not backed up: they are rebuilt from the vault remote.
"""

import datetime as dt
import sqlite3
from pathlib import Path


def backup_state(state_db: Path, dest_dir: Path, keep: int = 14) -> Path:
    state_db = Path(state_db)
    if not state_db.exists():
        raise FileNotFoundError(f"state database not found: {state_db}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"state-{dt.datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(state_db)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    target.chmod(0o600)  # contains client registrations and token hashes
    if keep > 0:
        old = sorted(dest_dir.glob("state-*.db"))[:-keep]
        for path in old:
            path.unlink()
    return target
