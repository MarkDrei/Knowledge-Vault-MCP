"""Write tools: add_note (inbox), update_note, append_note, move_note, delete_note."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.writes import NoteWriter, WriteError, WriteResult

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

NotePath = Annotated[
    str, Field(description="Vault-relative path of an existing note, e.g. 'Projects/Backup.md'")
]


def _run(op: Callable[[], WriteResult]) -> dict:
    try:
        return asdict(op())
    except WriteError as e:
        raise ToolError(str(e)) from e


def register(mcp: MCPServer, service: VaultService, caller: Callable[[], str]) -> None:
    writer = NoteWriter(service)

    @mcp.tool(annotations=WRITE)
    def add_note(
        title: Annotated[str, Field(description="Short descriptive title; also used for the file name")],
        body: Annotated[str, Field(description="Markdown content. [[Wikilinks]] and #tags are allowed")],
        tags: Annotated[list[str] | None, Field(description="Tags for the frontmatter, without '#'")] = None,
        source: Annotated[str, Field(description="Origin of the knowledge: mcp, web, manual, ...")] = "mcp",
        source_url: Annotated[str | None, Field(description="URL the knowledge comes from, if any")] = None,
    ) -> dict:
        """Capture new knowledge as a note in the vault inbox (it is curated later, outside this tool).

        The note gets a timestamped file name and frontmatter (id, created, status: inbox,
        source, captured_by, tags). The change is committed and pushed to the vault repository.
        """
        return _run(lambda: writer.add_note(title, body, tags, source, source_url, captured_by=caller()))

    @mcp.tool(annotations=DESTRUCTIVE)
    def update_note(
        path: NotePath,
        content: Annotated[str, Field(description="The complete new file content, including frontmatter")],
        expected_sha: Annotated[
            str | None,
            Field(description="The `sha` from get_note; the update fails if the note changed since"),
        ] = None,
    ) -> dict:
        """Replace the full content of an existing Markdown note (read it with get_note first).

        A frontmatter `updated` field, if present, is set to the current time. Committed and pushed.
        """
        return _run(lambda: writer.update_note(path, content, expected_sha))

    @mcp.tool(annotations=WRITE)
    def append_note(
        path: NotePath,
        text: Annotated[str, Field(description="Markdown to add at the end of the note")],
    ) -> dict:
        """Append text to the end of an existing Markdown note. Committed and pushed."""
        return _run(lambda: writer.append_note(path, text))

    @mcp.tool(annotations=DESTRUCTIVE)
    def move_note(
        path: NotePath,
        new_path: Annotated[str, Field(description="New vault-relative path, same file extension")],
    ) -> dict:
        """Move or rename a note. [[Wikilinks]] to it in other notes are updated in the same commit."""
        return _run(lambda: writer.move_note(path, new_path))

    @mcp.tool(annotations=DESTRUCTIVE)
    def delete_note(path: NotePath) -> dict:
        """Delete a note. Links to it are left as they are (reported as dangling_backlinks).

        The deletion is a git commit and can be reverted in the vault repository.
        """
        return _run(lambda: writer.delete_note(path))
