"""Local vault clone: git sync, path rules, Markdown/frontmatter/wikilink parsing."""

from knowledge_vault_mcp.vault.paths import PathError, normalize_path
from knowledge_vault_mcp.vault.repo import GitError
from knowledge_vault_mcp.vault.vault import SyncResult, Vault

__all__ = ["GitError", "PathError", "SyncResult", "Vault", "normalize_path"]
