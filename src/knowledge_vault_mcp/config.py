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

    # Vault and index (roadmap step 2+)
    vault_repo_url: str | None = None
    vault_branch: str = "main"
    vault_path: Path = Path("./data/vault")
    vault_git_sync: bool = True  # pull --rebase before and push after each write
    inbox_dir: str = "_inbox"
    timezone: str = "Europe/Berlin"
    db_path: Path = Path("./data/index.db")
    embedding_model: str = "intfloat/multilingual-e5-small"
    sync_interval: int = 300

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("PUBLIC_URL must be an absolute http(s) URL")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("PUBLIC_URL must use https unless it is localhost")
        return v.rstrip("/")

    @field_validator("auth_token", "owner_password_hash", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if v == "" else v

    @property
    def mcp_url(self) -> str:
        return self.public_url + MCP_PATH

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc
