"""MCP tools: a thin layer that validates input, calls the service and shapes output."""

from mcp.server.mcpserver import MCPServer

from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.tools import notes, search


def register_tools(mcp: MCPServer, service: VaultService) -> None:
    search.register(mcp, service)
    notes.register(mcp, service)
