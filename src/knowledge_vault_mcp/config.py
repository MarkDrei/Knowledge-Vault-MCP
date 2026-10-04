"""Runtime configuration, read from environment variables or a `.env` file."""

from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MCP_PATH = "/mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # HTTP / OAuth
    public_url: str = Field("http://localhost:8000", description="Base URL clients use to reach the server")
    host: str = "127.0.0.1"
    port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1"  # reverse proxy address(es) trusted for X-Forwarded-*
    owner_password_hash: SecretStr | None = None
    auth_token: SecretStr | None = None
    state_db_path: Path = Path("./data/state.db")
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 24 * 3600
    # Exact redirect URIs accepted at client registration; loopback URIs are always accepted.
    oauth_redirect_uris: list[str] = ["https://claude.ai/api/mcp/auth_callback"]

    # Vault (git clone). Without VAULT_REPO_URL the server uses a local repository at VAULT_PATH.
    vault_repo_url: str | None = None
    vault_branch: str = "main"
    vault_path: Path = Path("./data/vault")
    vault_ssh_key: Path | None = None  # deploy key for an SSH remote
    vault_git_sync: bool = (
        True  # pull before / push after writes and pull periodically; false = local commits only
    )
    git_author_name: str = "Knowledge Vault MCP"
    git_author_email: str = "kvault@localhost"
    inbox_dir: str = "_inbox"
    timezone: str = "Europe/Berlin"

    # Index and search
    db_path: Path = Path("./data/index.db")
    embedding_model: str = "intfloat/multilingual-e5-small"  # "hash" = no model, tests only
    embeddings_enabled: bool = True  # false = keyword search only, no model download
    model_cache_path: Path = Path("./data/models")
    chunk_max_chars: int = 1500
    chunk_overlap: int = 150
    max_document_mb: int = 25  # larger PDF/DOCX/HTML files are skipped by the indexer
    sync_interval: int = 300  # seconds between `git pull` + incremental reindex; 0 disables

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("PUBLIC_URL must be an absolute http(s) URL")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("PUBLIC_URL must use https unless it is localhost")
        return v.rstrip("/")

    @field_validator("auth_token", "owner_password_hash", "vault_repo_url", "vault_ssh_key", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if v == "" else v

    @property
    def mcp_url(self) -> str:
        return self.public_url + MCP_PATH

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc
