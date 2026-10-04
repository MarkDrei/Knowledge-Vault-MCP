"""Assembles the MCP server, OAuth endpoints and health check into one ASGI app."""

from contextlib import asynccontextmanager

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
from knowledge_vault_mcp.service import VaultService
from knowledge_vault_mcp.tools import register_tools

INSTRUCTIONS = (
    "Knowledge Vault: a personal, git-backed knowledge base. "
    "Use it to look up stored knowledge and to capture new notes into the inbox."
)


def build_mcp(settings: Settings, store: StateStore, service: VaultService) -> MCPServer:
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

    @mcp.tool()
    def server_info() -> dict:
        """Return server version and index status. Useful to check that the connector works."""
        return {
            "name": "knowledge-vault",
            "version": __version__,
            "vault_remote_configured": bool(settings.vault_repo_url),
            "index": service.status(),
        }

    register_tools(mcp, service)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__, "index_ready": service.ready})

    login = LoginHandler(provider)
    mcp.custom_route("/login", methods=["GET"], include_in_schema=False)(login.get)
    mcp.custom_route("/login", methods=["POST"], include_in_schema=False, name="login_post")(login.post)
    return mcp


def create_app(settings: Settings | None = None, service: VaultService | None = None) -> Starlette:
    settings = settings or Settings()
    store = StateStore(settings.state_db_path)
    store.purge_expired()
    service = service or VaultService(settings)
    mcp = build_mcp(settings, store, service)
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
    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=settings.host,
    )
    mcp_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_: Starlette):
        async with mcp_lifespan(app_):
            service.start()
            try:
                yield
            finally:
                service.stop()

    app.router.lifespan_context = lifespan
    app.state.service = service
    return app
