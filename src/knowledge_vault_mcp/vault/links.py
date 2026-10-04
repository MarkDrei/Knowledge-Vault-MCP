"""Resolving wikilink targets to vault files, the way Obsidian does.

`[[Note]]` matches any file whose name (without `.md`) is `Note`; `[[Folder/Note]]`
matches a file whose path ends in `Folder/Note`. Matching is case-insensitive. When
several files match, the exact full path wins, then the shortest path, then the
alphabetically first one.
"""

from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from knowledge_vault_mcp.vault.markdown import link_key, path_key


class LinkResolver:
    def __init__(self, paths: Iterable[str]):
        self._by_name: dict[str, list[str]] = defaultdict(list)
        self._by_key: dict[str, str] = {}
        for path in paths:
            key = path_key(path)
            self._by_key[key] = path
            self._by_name[key.rsplit("/", 1)[-1]].append(path)
        for candidates in self._by_name.values():
            candidates.sort(key=lambda p: (p.count("/"), len(p), p))

    def resolve(self, target: str) -> str | None:
        key = link_key(target)
        if not key:
            return None
        if key in self._by_key:
            return self._by_key[key]
        name = key.rsplit("/", 1)[-1]
        candidates = self._by_name.get(name, [])
        if "/" in key:
            candidates = [p for p in candidates if path_key(p).endswith("/" + key)]
        return candidates[0] if candidates else None

    def unique_name(self, path: str) -> bool:
        """True if `[[name]]` (without folders) resolves to `path`."""
        name = path_key(path).rsplit("/", 1)[-1]
        return self.resolve(name) == path


def candidate_keys(path: str) -> list[str]:
    """All link keys that could point to `path`: `c`, `b/c`, `a/b/c` for `a/b/c.md`."""
    parts = path_key(path).split("/")
    return ["/".join(parts[i:]) for i in range(len(parts))]


def link_target_for(path: str, resolver: LinkResolver, prefer_full_path: bool) -> str:
    """How to write a link to `path`: by name if that is unambiguous, else by path.

    Markdown targets drop the `.md` suffix, as Obsidian writes them.
    """
    p = PurePosixPath(path)
    full = str(p.with_suffix("")) if p.suffix.lower() == ".md" else path
    if prefer_full_path or not resolver.unique_name(path):
        return full
    return full.rsplit("/", 1)[-1]
