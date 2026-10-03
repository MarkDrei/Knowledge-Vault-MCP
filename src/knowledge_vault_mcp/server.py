"""Assembles the MCP server, OAuth endpoints and health check into one ASGI app."""

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from knowledge_vault_mcp import __version__
from knowledge_vault_mcp.auth.login import LoginHandler
from knowledge_vault_mcp.auth.provider import OFFLINE_SCOPE, SCOPE, VaultOAuthProvider
from knowledge_vault_mcp.auth.store import StateStore
from knowledge_vault_mcp.config import MCP_PATH, Settings
from knowledge_vault_mcp.vault import Vault, VaultError

INSTRUCTIONS = (
    "Knowledge Vault: a personal, git-backed knowledge base. "
    "Use it to look up stored knowledge and to capture new notes into the inbox."
)


def build_mcp(settings: Settings, store: StateStore) -> MCPServer:
    provider = VaultOAuthProvider(settings, store)
    auth = AuthSettings(
        issuer_url=settings.public_url,
        resource_server_url=settings.mcp_url,
        required_scopes=[SCOPE],
        validate_token_resource=True,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[SCOPE, OFFLINE_SCOPE],
            default_scopes=[SCOPE, OFFLINE_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    mcp = MCPServer(
        name="knowledge-vault",
        title="Knowledge Vault",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth_server_provider=provider,
        auth=auth,
    )

    vault = Vault(
        settings.vault_path,
        inbox_dir=settings.inbox_dir,
        timezone=settings.timezone,
        git_sync=settings.vault_git_sync,
        branch=settings.vault_branch,
    )

    def guarded(fn):
        try:
            return fn()
        except VaultError as e:
            raise ValueError(str(e)) from e

    @mcp.tool()
    def server_info() -> dict:
        """Return server version and status. Useful to check that the connector works."""
        return {
            "name": "knowledge-vault",
            "version": __version__,
            "vault_configured": vault.available,
            "git_sync": settings.vault_git_sync,
            "inbox_dir": settings.inbox_dir,
        }

    @mcp.tool()
    def search(query: str, limit: int = 10, path_prefix: str = "", tag: str = "") -> list[dict]:
        """Keyword search (BM25) over all notes. Returns path, title, tags, snippet and score per hit."""
        return guarded(lambda: vault.search(query, min(max(limit, 1), 50), path_prefix, tag))

    @mcp.tool()
    def get_note(path: str) -> dict:
        """Return a note's full Markdown (incl. frontmatter), title, tags and outgoing wikilinks."""

        def run():
            n = vault.get_note(path)
            return {"path": n.path, "title": n.title, "tags": n.tags, "links": n.links, "content": n.raw}

        return guarded(run)

    @mcp.tool()
    def list_notes(path_prefix: str = "", limit: int = 200) -> list[dict]:
        """List notes (path and title), optionally below a folder prefix such as '10-Projects/'."""
        return guarded(lambda: vault.list_notes(path_prefix, min(max(limit, 1), 1000)))

    @mcp.tool()
    def get_backlinks(path: str) -> list[dict]:
        """Return notes that contain a [[wikilink]] to the given note."""
        return guarded(lambda: vault.get_backlinks(path))

    @mcp.tool()
    def add_note(title: str, body: str, tags: list[str] | None = None, source_url: str = "") -> dict:
        """Capture a new note into the inbox with generated metadata. Commits (and pushes) to git."""
        return guarded(lambda: {"path": vault.add_note(title, body, tags, source_url)})

    @mcp.tool()
    def append_note(path: str, text: str) -> dict:
        """Append text to the end of an existing note. One git commit."""
        return guarded(lambda: {"path": vault.append_note(path, text)})

    @mcp.tool()
    def update_note(path: str, content: str) -> dict:
        """Replace the full content of an existing note (include frontmatter). One git commit."""
        return guarded(lambda: {"path": vault.update_note(path, content)})

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    login = LoginHandler(provider)
    mcp.custom_route("/login", methods=["GET"], include_in_schema=False)(login.get)
    mcp.custom_route("/login", methods=["POST"], include_in_schema=False, name="login_post")(login.post)
    return mcp


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings()
    store = StateStore(settings.state_db_path)
    store.purge_expired()
    mcp = build_mcp(settings, store)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[settings.public_host, "127.0.0.1:*", "localhost:*"],
        allowed_origins=[
            settings.public_url,
            "https://claude.ai",
            "http://127.0.0.1:*",
            "http://localhost:*",
        ],
    )
    return mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=settings.host,
    )
