"""MCP tools: a thin layer that validates input, calls the service and shapes output."""

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.tools import notes, search, write


def register_tools(mcp: MCPServer, service: VaultService, caller: Callable[[], str]) -> None:
    """`caller` returns the name of the client making the current request (for `captured_by`)."""
    search.register(mcp, service)
    notes.register(mcp, service)
    write.register(mcp, service, caller)
