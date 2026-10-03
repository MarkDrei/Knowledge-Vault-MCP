"""Knowledge-Vault-MCP: MCP server for a git-backed Obsidian vault."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("knowledge-vault-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"
