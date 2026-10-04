"""Helpers to create a bare "remote" vault repository and edit it like Obsidian would."""

import subprocess
from pathlib import Path

ENV = {
    "GIT_AUTHOR_NAME": "Owner",
    "GIT_AUTHOR_EMAIL": "owner@example.com",
    "GIT_COMMITTER_NAME": "Owner",
    "GIT_COMMITTER_EMAIL": "owner@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


class Remote:
    """A bare repo plus a separate working clone that plays the role of Obsidian."""

    def __init__(self, tmp: Path, files: dict[str, str] | None = None):
        self.bare = tmp / "remote.git"
        self.work = tmp / "obsidian"
        git(tmp, "init", "--bare", "-b", "main", str(self.bare))
        git(tmp, "clone", str(self.bare), str(self.work))
        git(self.work, "checkout", "-B", "main")
        if files:
            self.commit(files, "initial")

    @property
    def url(self) -> str:
        return str(self.bare)

    def commit(self, files: dict[str, str | None], message: str = "edit") -> None:
        if self._has_main():
            git(self.work, "pull", "-q", "--rebase", "origin", "main")
        for rel, content in files.items():
            p = self.work / rel
            if content is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", message)
        git(self.work, "push", "-q", "origin", "HEAD:main")

    def read(self, rel: str) -> str:
        git(self.work, "pull", "-q", "--rebase", "origin", "main")
        return (self.work / rel).read_text(encoding="utf-8")

    def exists(self, rel: str) -> bool:
        git(self.work, "pull", "-q", "--rebase", "origin", "main")
        return (self.work / rel).exists()

    def log(self) -> list[str]:
        return git(self.bare, "log", "--format=%s", "main").splitlines()

    def _has_main(self) -> bool:
        return bool(git(self.bare, "branch", "--list", "main").strip())
