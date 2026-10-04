"""Thin wrapper around the system `git` binary for the local vault clone.

No locking here: callers (`Vault`) serialize all git operations.
"""

import contextlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileChange:
    status: str  # A(dded), M(odified), D(eleted), T(ype change)
    path: str


class GitRepo:
    def __init__(
        self,
        path: Path,
        branch: str,
        remote_url: str | None = None,
        ssh_key: Path | None = None,
        author_name: str = "Knowledge Vault MCP",
        author_email: str = "kvault@localhost",
    ):
        self.path = Path(path)
        self.branch = branch
        self.remote_url = remote_url
        self._env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
        if ssh_key:
            self._env["GIT_SSH_COMMAND"] = (
                f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
            )

    def run(self, *args: str, cwd: Path | None = None) -> str:
        cmd = ["git", "-c", "core.quotePath=false", *args]
        proc = subprocess.run(  # noqa: S603 - fixed binary, no shell
            cmd,
            cwd=cwd or self.path,
            env=self._env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout).strip()
            raise GitError(f"git {args[0]} failed: {msg}")
        return proc.stdout

    # ---- setup ----
    def ensure_clone(self) -> None:
        """Clone the remote on first start; with no remote, use (or init) a local repository."""
        if (self.path / ".git").exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.remote_url:
            self.run("clone", "--no-checkout", self.remote_url, str(self.path), cwd=self.path.parent)
            if self.remote_ref_exists():
                self.run("checkout", "-B", self.branch, f"origin/{self.branch}")
            else:  # empty remote: start the branch locally
                self.run("checkout", "--orphan", self.branch)
        else:
            self.path.mkdir(parents=True, exist_ok=True)
            self.run("init", "-b", self.branch)

    def has_remote(self) -> bool:
        return "origin" in self.run("remote").split()

    def remote_ref_exists(self) -> bool:
        try:
            self.run("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{self.branch}")
            return True
        except GitError:
            return False

    # ---- state ----
    def head(self) -> str | None:
        try:
            return self.run("rev-parse", "--verify", "--quiet", "HEAD").strip() or None
        except GitError:
            return None

    def is_ancestor(self, old: str, new: str) -> bool:
        try:
            self.run("merge-base", "--is-ancestor", old, new)
            return True
        except GitError:
            return False

    def dirty_paths(self, untracked: bool = False) -> list[str]:
        """Tracked paths with uncommitted changes; with `untracked`, also new files (not ignored)."""
        mode = "--untracked-files=all" if untracked else "--untracked-files=no"
        out = self.run("status", "--porcelain", "-z", mode, "--no-renames")
        return [entry[3:] for entry in out.split("\0") if len(entry) > 3]

    def ls_files(self) -> list[str]:
        return [p for p in self.run("ls-files", "-z").split("\0") if p]

    def changes(self, old: str, new: str) -> list[FileChange]:
        out = self.run("diff", "--name-status", "--no-renames", "-z", old, new)
        fields = [f for f in out.split("\0") if f]
        return [FileChange(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]

    def last_commit_times(self, rev_range: str | None = None) -> dict[str, int]:
        """Map each path to the unix time of the latest commit that touched it."""
        if self.head() is None:
            return {}
        args = ["log", "--format=@@%ct", "--name-only", "--no-renames"]
        out = self.run(*args, *([rev_range] if rev_range else []))
        times: dict[str, int] = {}
        current = 0
        for line in out.splitlines():
            if line.startswith("@@"):
                current = int(line[2:])
            elif line and line not in times:
                times[line] = current
        return times

    # ---- network and history ----
    def pull_rebase(self) -> None:
        """`git pull --rebase`; on a conflict the rebase is aborted and GitError raised."""
        if not self.has_remote():
            return
        self.run("fetch", "--prune", "origin")
        if not self.remote_ref_exists():
            return
        try:
            self.run("rebase", f"origin/{self.branch}")
        except GitError:
            with contextlib.suppress(GitError):
                self.run("rebase", "--abort")
            raise

    def has_changes(self, paths: list[str]) -> bool:
        return bool(paths) and bool(self.run("status", "--porcelain", "--", *paths).strip())

    def commit(self, paths: list[str], message: str) -> str:
        self.run("add", "-A", "--", *paths)
        self.run("commit", "-m", message, "--", *paths)
        return self.head() or ""

    def push(self) -> None:
        if self.has_remote():
            self.run("push", "origin", f"HEAD:refs/heads/{self.branch}")

    def reset_hard(self, rev: str | None, paths: list[str]) -> None:
        """Return the work tree to `rev` and remove untracked files among `paths`."""
        if rev:
            self.run("reset", "--hard", rev)
        elif paths:  # no commit yet: unstage what we added
            self.run("rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", *paths)
        if paths:
            self.run("clean", "-fdq", "--", *paths)
