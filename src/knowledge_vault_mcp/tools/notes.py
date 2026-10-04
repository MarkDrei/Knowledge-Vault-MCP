"""Read tools: `get_note` and `get_backlinks`."""

import hashlib
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.tools.common import READ_ONLY, iso_time
from knowledge_vault_mcp.vault import PathError, normalize_path
from knowledge_vault_mcp.vault.links import candidate_keys
from knowledge_vault_mcp.vault.markdown import parse_note
from knowledge_vault_mcp.vault.paths import is_markdown

MAX_CONTENT_CHARS = 200_000

PathParam = Annotated[
    str,
    Field(description="Vault-relative path like 'Projects/Backup.md', or a note name as in a [[wikilink]]"),
]


def resolve_note(service: VaultService, path: str) -> str:
    """Accept an exact vault path or anything a wikilink could contain; return the vault path."""
    try:
        clean = normalize_path(path)
    except PathError as e:
        raise ToolError(str(e)) from e
    if service.vault.exists(clean):
        return clean
    resolved = service.resolver().resolve(clean)
    if resolved is None and not clean.lower().endswith(".md"):
        resolved = service.resolver().resolve(clean + ".md")
    if resolved is None or not service.vault.exists(resolved):
        raise ToolError(f"note not found: {path}")
    return resolved


def content_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def register(mcp: MCPServer, service: VaultService) -> None:
    settings = service.settings

    @mcp.tool(annotations=READ_ONLY)
    def get_note(path: PathParam) -> dict:
        """Return a note's full content and metadata.

        `sha` identifies this version; pass it as `expected_sha` to update_note to make sure
        nobody changed the note in between.
        """
        path = resolve_note(service, path)
        data = service.vault.read_bytes(path)
        row = (
            service.db.conn()
            .execute("SELECT title, kind, frontmatter, modified FROM notes WHERE path = ?", (path,))
            .fetchone()
        )
        tags = [r[0] for r in service.db.conn().execute("SELECT tag FROM tags WHERE path = ?", (path,))]
        result: dict = {"path": path, "sha": content_sha(data)}
        if is_markdown(path):
            text = data.decode("utf-8", errors="replace")
            note = parse_note(path, text)
            resolver = service.resolver()
            result.update(
                title=note.title,
                kind="md",
                frontmatter=note.frontmatter,
                tags=note.tags,
                links=[
                    {
                        "target": link.target,
                        "path": resolver.resolve(link.target),
                        "heading": link.heading,
                        "alias": link.alias,
                        "embed": link.embed,
                    }
                    for link in note.links
                ],
            )
        else:
            text = service.indexer.extract_text(path, data)
            result.update(
                title=row[0] if row else path, kind=row[1] if row else None, frontmatter={}, tags=tags
            )
        if row is None:
            result["note"] = "Not indexed yet."
        result["modified"] = iso_time(settings, row[3]) if row else None
        result["truncated"] = len(text) > MAX_CONTENT_CHARS
        result["content"] = text[:MAX_CONTENT_CHARS]
        return result

    @mcp.tool(annotations=READ_ONLY)
    def get_backlinks(path: PathParam) -> dict:
        """Return the notes that link to a note, with the line that contains each link."""
        path = resolve_note(service, path)
        resolver = service.resolver()
        keys = candidate_keys(path)
        marks = ",".join("?" * len(keys))
        rows = service.db.conn().execute(
            "SELECT l.src, n.title, l.target, l.heading, l.alias, l.embed, l.line, l.context "  # noqa: S608
            f"FROM links l JOIN notes n ON n.path = l.src WHERE l.target_key IN ({marks}) "
            "ORDER BY l.src, l.line",
            keys,
        )
        backlinks = [
            {
                "path": src,
                "title": title,
                "line": line,
                "context": context,
                "heading": heading,
                "alias": alias,
                "embed": bool(embed),
            }
            for src, title, target, heading, alias, embed, line, context in rows
            if resolver.resolve(target) == path and src != path
        ]
        return {"path": path, "count": len(backlinks), "backlinks": backlinks}
