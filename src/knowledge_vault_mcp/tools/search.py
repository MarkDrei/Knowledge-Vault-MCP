"""The `search` tool."""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from knowledge_vault_mcp.retrieval.search import MAX_LIMIT, SearchFilters
from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.tools.common import READ_ONLY, iso_time, parse_time


def register(mcp: MCPServer, service: VaultService) -> None:
    settings = service.settings

    @mcp.tool(annotations=READ_ONLY)
    def search(
        query: Annotated[str, Field(description="What to look for, in natural language or keywords")],
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Maximum number of chunks")] = 8,
        tags: Annotated[
            list[str] | None, Field(description="Only notes with all of these tags (nested tags match)")
        ] = None,
        path_prefix: Annotated[str | None, Field(description="Only notes under this folder/path")] = None,
        status: Annotated[
            str | None, Field(description="Only notes whose frontmatter status matches")
        ] = None,
        include_inbox: Annotated[bool, Field(description="Include captured, not yet curated notes")] = True,
        modified_after: Annotated[str | None, Field(description="ISO date; last change on or after")] = None,
        modified_before: Annotated[str | None, Field(description="ISO date; last change before")] = None,
    ) -> dict:
        """Search the knowledge vault (hybrid keyword + semantic, German and English).

        Returns the best matching chunks (note sections) with their source path, heading path
        and text. Use get_note to read a whole note. Cite the paths when answering.
        """
        if not query.strip():
            raise ToolError("query is empty")
        filters = SearchFilters(
            tags=tags or [],
            path_prefix=path_prefix,
            status=status,
            include_inbox=include_inbox,
            modified_after=parse_time(settings, modified_after, "modified_after"),
            modified_before=parse_time(settings, modified_before, "modified_before"),
        )
        hits = service.retriever.search(query, limit, filters)
        result = {
            "results": [
                {
                    "path": h.path,
                    "title": h.title,
                    "headings": h.headings,
                    "text": h.text,
                    "score": h.score,
                    "match": h.match,
                    "kind": h.kind,
                    "status": h.status,
                    "tags": h.tags,
                    "modified": iso_time(settings, h.modified),
                }
                for h in hits
            ]
        }
        if not service.ready:
            result["note"] = "The index is still being built; results may be incomplete."
        return result
