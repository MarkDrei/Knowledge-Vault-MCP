"""The local vault clone. The only component that runs git; holds the write lock."""

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.vault.paths import IGNORED_DIRS, normalize_path
from knowledge_vault_mcp.vault.repo import FileChange, GitRepo


@dataclass
class SyncResult:
    old: str | None
    new: str | None

    @property
    def changed(self) -> bool:
        return self.old != self.new


class Vault:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = Path(settings.vault_path).resolve()
        self.repo = GitRepo(
            self.root,
            settings.vault_branch,
            remote_url=settings.vault_repo_url,
            ssh_key=settings.vault_ssh_key,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email,
        )
        # Serializes every git operation and every write to the work tree (ADR-0006).
        self.lock = threading.RLock()

    def open(self) -> None:
        with self.lock:
            self.repo.ensure_clone()

    @property
    def git_sync(self) -> bool:
        return self.settings.vault_git_sync

    def sync(self) -> SyncResult:
        """Pull remote changes. Raises GitError if the pull fails (the clone stays unchanged).

        Skipped when VAULT_GIT_SYNC is off or the work tree has uncommitted changes (e.g. a
        bind-mounted checkout that is also edited on the host): a rebase would refuse anyway.
        """
        with self.lock:
            old = self.repo.head()
            if self.git_sync and not self.repo.dirty_paths():
                self.repo.pull_rebase()
            return SyncResult(old, self.repo.head())

    # ---- reading ----
    def abspath(self, path: str) -> Path:
        full = (self.root / normalize_path(path)).resolve()
        if not full.is_relative_to(self.root):  # symlinks pointing outside the vault
            raise PermissionError(f"path leaves the vault: {path}")
        return full

    def exists(self, path: str) -> bool:
        return self.abspath(path).is_file()

    def read_bytes(self, path: str) -> bytes:
        return self.abspath(path).read_bytes()

    def read_text(self, path: str) -> str:
        return self.read_bytes(path).decode("utf-8", errors="replace")

    def files(self) -> list[str]:
        """All content files in the work tree (tracked or not), as vault-relative paths."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            rel_dir = Path(dirpath).relative_to(self.root)
            out.extend((rel_dir / name).as_posix() for name in filenames)
        return sorted(out)

    def changes(self, old: str | None, new: str | None) -> list[FileChange] | None:
        """Files changed between two commits, or None if a full rescan is needed."""
        if not old or not new:
            return None
        if old == new:
            return []
        if not self.repo.is_ancestor(old, new):  # history was rewritten; do not trust the diff
            return None
        return self.repo.changes(old, new)
