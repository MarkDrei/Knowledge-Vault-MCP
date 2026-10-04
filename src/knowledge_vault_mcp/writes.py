"""Write operations on the vault (ADR-0006).

Every operation runs under the vault lock as one transaction:

    pull --rebase -> bring index up to date -> change files -> commit -> push -> reindex

Any failure resets the clone to the commit it had before the operation (removing files
the operation created) and raises `WriteError`. Nothing is merged or resolved automatically.
"""

import datetime as dt
import hashlib
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.vault import GitError, PathError, normalize_path
from knowledge_vault_mcp.vault.links import LinkResolver, candidate_keys, link_target_for
from knowledge_vault_mcp.vault.markdown import (
    WikiLink,
    render_frontmatter,
    rewrite_links,
    set_frontmatter_fields,
    split_frontmatter,
)
from knowledge_vault_mcp.vault.paths import is_markdown

log = logging.getLogger(__name__)

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


class WriteError(Exception):
    """The operation was rejected or aborted; the vault is unchanged."""


@dataclass
class WriteResult:
    path: str
    commit: str
    sha: str | None = None
    details: dict = field(default_factory=dict)


def slugify(title: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", title.translate(_TRANSLIT)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    return slug or "note"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class NoteWriter:
    def __init__(self, service: VaultService):
        self.service = service
        self.vault = service.vault
        self.settings = service.settings
        self.tz = ZoneInfo(self.settings.timezone)

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz).replace(microsecond=0)

    # ---- transaction ----
    def _transaction(self, message: str, change: Callable[[], tuple[list[str], WriteResult]]) -> WriteResult:
        repo = self.vault.repo
        with self.vault.lock:
            dirty = repo.dirty_paths()
            if dirty:  # a rollback (reset --hard) would destroy these edits
                shown = ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else "")
                raise WriteError(f"the vault has uncommitted changes, commit them first: {shown}")
            before = repo.head()
            if self.vault.git_sync:
                try:
                    repo.pull_rebase()
                except GitError as e:
                    raise WriteError(f"could not pull from the vault remote, nothing was changed: {e}") from e
            self.service.indexer.update()  # so link rewriting and checks see the latest state
            touched: list[str] = []
            try:
                touched, result = change()
                if not repo.has_changes(touched):
                    result.commit = before or ""
                    result.details["unchanged"] = True
                    return result
                result.commit = repo.commit(touched, message)
                if self.vault.git_sync:
                    repo.push()
            except Exception as e:
                try:
                    repo.reset_hard(before, touched)
                except GitError:
                    log.exception("reset after failed write failed")
                if isinstance(e, WriteError):
                    raise
                if isinstance(e, GitError):
                    raise WriteError(
                        f"aborted, the vault was left unchanged (probably edited elsewhere; retry): {e}"
                    ) from e
                raise
            self.service.indexer.update()
            return result

    # ---- helpers ----
    def _existing(self, path: str, markdown_only: bool) -> str:
        try:
            path = normalize_path(path)
        except PathError as e:
            raise WriteError(str(e)) from e
        if not self.vault.exists(path):
            raise WriteError(f"note not found: {path}")
        if markdown_only and not is_markdown(path):
            raise WriteError(f"only Markdown notes can be edited: {path}")
        return path

    def _check_exists(self, path: str) -> None:
        """Re-check inside the transaction: the pull may just have removed the file."""
        if not self.vault.exists(path):
            raise WriteError(f"note not found (removed in the vault remote?): {path}")

    def _write(self, path: str, text: str) -> bytes:
        full = self.vault.abspath(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        full.write_bytes(data)
        return data

    def _touch_updated(self, text: str) -> str:
        """Refresh `updated` if the note keeps one in its frontmatter; never adds frontmatter."""
        fm, _, _ = split_frontmatter(text)
        if "updated" in fm:
            return set_frontmatter_fields(text, {"updated": self.now().isoformat()})
        return text

    # ---- operations ----
    def add_note(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        source: str = "mcp",
        source_url: str | None = None,
        captured_by: str = "unknown",
    ) -> WriteResult:
        title = " ".join(title.split())
        if not title:
            raise WriteError("title is empty")
        inbox = normalize_path(self.settings.inbox_dir)

        def change():
            now = self.now()
            stem = f"{now:%Y-%m-%d-%H%M%S}-{slugify(title)}"
            path, n = f"{inbox}/{stem}.md", 1
            while self.vault.abspath(path).exists():
                n += 1
                path = f"{inbox}/{stem}-{n}.md"
            meta = {
                "id": PurePosixPath(path).stem,
                "title": title,
                "created": now.isoformat(),
                "updated": now.isoformat(),
                "status": "inbox",
                "source": source,
                "captured_by": captured_by,
            }
            if source_url:
                meta["source_url"] = source_url
            meta["tags"] = list(dict.fromkeys(t.strip().lstrip("#") for t in tags or [] if t.strip()))
            content = body.strip("\n")
            if not content.lstrip().startswith("# "):
                content = f"# {title}\n\n{content}" if content else f"# {title}"
            data = self._write(path, render_frontmatter(meta) + "\n" + content + "\n")
            return [path], WriteResult(path, "", _sha(data), {"id": meta["id"]})

        return self._transaction(f"Add inbox note: {title}", change)

    def update_note(self, path: str, content: str, expected_sha: str | None = None) -> WriteResult:
        path = self._existing(path, markdown_only=True)

        def change():
            self._check_exists(path)
            current = self.vault.read_bytes(path)
            if expected_sha and _sha(current) != expected_sha:
                raise WriteError(f"{path} changed since it was read (sha mismatch); read it again")
            data = self._write(path, self._touch_updated(content))
            return [path], WriteResult(path, "", _sha(data))

        return self._transaction(f"Update {path}", change)

    def append_note(self, path: str, text: str) -> WriteResult:
        path = self._existing(path, markdown_only=True)
        if not text.strip():
            raise WriteError("text is empty")

        def change():
            self._check_exists(path)
            current = self.vault.read_text(path).rstrip("\n")
            separator = "\n\n" if current else ""
            data = self._write(path, self._touch_updated(current + separator + text.strip("\n") + "\n"))
            return [path], WriteResult(path, "", _sha(data))

        return self._transaction(f"Append to {path}", change)

    def delete_note(self, path: str) -> WriteResult:
        path = self._existing(path, markdown_only=False)

        def change():
            self._check_exists(path)
            backlinks = self._linking_notes([path], LinkResolver(self.vault.files()), {path})
            self.vault.abspath(path).unlink()
            result = WriteResult(path, "", None, {"dangling_backlinks": sorted(backlinks)})
            return [path], result

        return self._transaction(f"Delete {path}", change)

    def move_note(self, path: str, new_path: str) -> WriteResult:
        path = self._existing(path, markdown_only=False)
        try:
            new_path = normalize_path(new_path)
        except PathError as e:
            raise WriteError(str(e)) from e
        if PurePosixPath(new_path).suffix.lower() != PurePosixPath(path).suffix.lower():
            raise WriteError("the file extension cannot change")
        if new_path == path:
            raise WriteError("source and destination are the same")

        def change():
            self._check_exists(path)
            if self.vault.abspath(new_path).exists():
                raise WriteError(f"destination exists: {new_path}")
            files = self.vault.files()
            before = LinkResolver(files)
            after = LinkResolver([new_path if f == path else f for f in files])
            sources = self._linking_notes([path, new_path], before, set())
            target = self.vault.abspath(new_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self.vault.abspath(path).rename(target)
            touched, rewritten = [path, new_path], {}
            for src in sorted(sources):
                current_src = new_path if src == path else src
                count = self._rewrite_links_in(current_src, before, after, path, new_path)
                if count:
                    rewritten[current_src] = count
                    touched.append(current_src)
            return touched, WriteResult(new_path, "", None, {"from": path, "links_updated": rewritten})

        return self._transaction(f"Move {path} -> {new_path}", change)

    # ---- link maintenance ----
    def _linking_notes(self, paths: list[str], resolver: LinkResolver, exclude: set[str]) -> set[str]:
        """Markdown files with a link whose key could match one of `paths` (from the index)."""
        keys = sorted({k for p in paths for k in candidate_keys(p)})
        marks = ",".join("?" * len(keys))
        rows = self.service.db.conn().execute(
            f"SELECT DISTINCT src, target FROM links WHERE target_key IN ({marks})",  # noqa: S608
            keys,
        )
        if len(paths) == 1:  # only links that really resolve to the path
            return {src for src, t in rows if resolver.resolve(t) == paths[0] and src not in exclude}
        return {src for src, _ in rows if src not in exclude}

    def _rewrite_links_in(
        self, src: str, before: LinkResolver, after: LinkResolver, old: str, new: str
    ) -> int:
        """Point every link in `src` at the file it meant before the move. Returns the count."""
        if not is_markdown(src):
            return 0
        text = self.vault.read_text(src)
        count = 0

        def replace(link: WikiLink) -> str | None:
            nonlocal count
            meant = before.resolve(link.target)
            if meant is None:
                return None
            meant = new if meant == old else meant
            if after.resolve(link.target) == meant:
                return None
            count += 1
            return link_target_for(meant, after, prefer_full_path="/" in link.target)

        new_text = rewrite_links(text, replace)
        if count:
            self._write(src, new_text)
        return count
