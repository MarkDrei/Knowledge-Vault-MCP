"""Single-owner OAuth 2.1 authorization server (DCR + PKCE), backed by `StateStore`.

The SDK supplies the protocol endpoints (/register, /authorize, /token, /revoke, metadata).
This provider supplies storage and the owner login step: /authorize redirects to our
/login page, and only after the owner password is entered is an authorization code issued.
"""

import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from knowledge_vault_mcp.auth.store import StateStore
from knowledge_vault_mcp.config import Settings

SCOPE = "vault"
OFFLINE_SCOPE = "offline_access"
OWNER = "owner"
CODE_TTL = 300
PENDING_TTL = 600
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


@dataclass
class PendingAuthorization:
    client_id: str
    client_name: str
    params: AuthorizationParams
    expires_at: float


def is_allowed_redirect_uri(uri: str, allowed: list[str]) -> bool:
    """Exact match against the allowlist, or any loopback http URI (RFC 8252 native clients)."""
    if uri in allowed:
        return True
    parsed = urlparse(uri)
    return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS


class VaultOAuthProvider:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self._pending: dict[str, PendingAuthorization] = {}

    # ---- client registration (RFC 7591) ----
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not is_allowed_redirect_uri(str(uri), self.settings.oauth_redirect_uris):
                raise RegistrationError("invalid_redirect_uri", f"redirect_uri not allowed: {uri}")
        self.store.put_client(client_info.client_id, client_info.model_dump(mode="json", exclude_none=True))

    # ---- authorization ----
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.settings.mcp_url:
            raise AuthorizeError("invalid_target", "unknown resource")
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}
        request_id = secrets.token_urlsafe(32)
        self._pending[request_id] = PendingAuthorization(
            client_id=client.client_id,
            client_name=client.client_name or client.client_id,
            params=params,
            expires_at=now + PENDING_TTL,
        )
        return f"{self.settings.public_url}/login?request={request_id}"

    def get_pending(self, request_id: str) -> PendingAuthorization | None:
        pending = self._pending.get(request_id)
        if pending and pending.expires_at > time.time():
            return pending
        return None

    def complete_authorization(self, request_id: str) -> str:
        """Called after a successful owner login. Issues a code and returns the client redirect URL."""
        pending = self._pending.pop(request_id)
        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=pending.params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=self.settings.mcp_url,
            subject=OWNER,
        )
        self.store.put_grant(
            code,
            "code",
            pending.client_id,
            secrets.token_hex(16),
            auth_code.model_dump(mode="json"),
            int(auth_code.expires_at),
        )
        return construct_redirect_uri(str(pending.params.redirect_uri), code=code, state=pending.params.state)

    def deny_authorization(self, request_id: str) -> str:
        pending = self._pending.pop(request_id)
        return construct_redirect_uri(
            str(pending.params.redirect_uri), error="access_denied", state=pending.params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        found = self.store.get_grant(authorization_code, "code")
        if not found or found[1]["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate(found[1])

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not self.store.delete_grant(authorization_code.code):  # single use
            raise TokenError("invalid_grant", "authorization code already used")
        return self._issue_tokens(client.client_id, authorization_code.scopes, secrets.token_hex(16))

    # ---- tokens ----
    def _issue_tokens(self, client_id: str, scopes: list[str], family: str) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        access_exp = now + self.settings.access_token_ttl
        refresh_exp = now + self.settings.refresh_token_ttl
        common = {
            "client_id": client_id,
            "scopes": scopes,
            "resource": self.settings.mcp_url,
            "subject": OWNER,
        }
        self.store.put_grant(
            access,
            "access",
            client_id,
            family,
            AccessToken(token="", expires_at=access_exp, **common).model_dump(mode="json"),
            access_exp,
        )
        self.store.put_grant(
            refresh,
            "refresh",
            client_id,
            family,
            RefreshToken(token="", expires_at=refresh_exp, **common).model_dump(mode="json"),
            refresh_exp,
        )
        return OAuthToken(
            access_token=access,
            expires_in=self.settings.access_token_ttl,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        found = self.store.get_grant(refresh_token, "refresh")
        if not found or found[1]["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate({**found[1], "token": refresh_token})

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        found = self.store.get_grant(refresh_token.token, "refresh")
        if not found:
            raise TokenError("invalid_grant", "refresh token is no longer valid")
        family = found[0]
        self.store.delete_family(family)  # rotate: old access + refresh token die together
        return self._issue_tokens(client.client_id, scopes or refresh_token.scopes, family)

    async def load_access_token(self, token: str) -> AccessToken | None:
        static = self.settings.auth_token
        if static and hmac.compare_digest(token.encode(), static.get_secret_value().encode()):
            return AccessToken(
                token=token,
                client_id="static-token",
                scopes=[SCOPE],
                resource=self.settings.mcp_url,
                subject=OWNER,
            )
        found = self.store.get_grant(token, "access")
        if not found:
            return None
        return AccessToken.model_validate({**found[1], "token": token})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        for kind in ("access", "refresh"):
            found = self.store.get_grant(token.token, kind)
            if found:
                self.store.delete_family(found[0])
