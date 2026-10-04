"""Vault-relative paths: validation and classification.

A note is identified by its vault-relative POSIX path (e.g. `Projects/Backup.md`).
Every path that comes from a client goes through `normalize_path` before it touches
the file system, so nothing can escape the vault or reach `.git`.
"""

from pathlib import PurePosixPath

# Folders that never hold vault content.
IGNORED_DIRS = (".git", ".obsidian", ".trash")

MARKDOWN_SUFFIXES = (".md",)
DOCUMENT_SUFFIXES = (".pdf", ".docx", ".html", ".htm")


class PathError(ValueError):
    """A client supplied a path that is not a valid vault-relative path."""


def normalize_path(path: str) -> str:
    """Return a clean vault-relative POSIX path or raise `PathError`."""
    if not path or not path.strip():
        raise PathError("path is empty")
    p = path.strip().replace("\\", "/")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise PathError(f"path must be relative to the vault: {path}")
    parts = [part for part in PurePosixPath(p).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise PathError(f"invalid path: {path}")
    if parts[0] in IGNORED_DIRS:
        raise PathError(f"path is not part of the vault content: {path}")
    if any("\x00" in part for part in parts):
        raise PathError("path contains a NUL byte")
    return "/".join(parts)


def is_markdown(path: str) -> bool:
    return path.lower().endswith(MARKDOWN_SUFFIXES)


def is_document(path: str) -> bool:
    return path.lower().endswith(DOCUMENT_SUFFIXES)


def is_indexable(path: str) -> bool:
    """True for files the indexer turns into notes and chunks."""
    if any(part in IGNORED_DIRS for part in PurePosixPath(path).parts):
        return False
    return is_markdown(path) or is_document(path)


def kind_of(path: str) -> str:
    """Short file kind stored with each note: md, pdf, docx or html."""
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    return "html" if suffix == "htm" else suffix
