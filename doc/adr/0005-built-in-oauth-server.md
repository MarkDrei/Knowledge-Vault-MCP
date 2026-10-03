# ADR-0005: Built-in single-owner OAuth server

Status: accepted (2026-10-03)

**Context.** The server must be usable as a custom connector from Claude mobile/web/desktop. Those clients support OAuth 2.0 with Dynamic Client Registration (or CIMD) and PKCE S256; static request headers exist only as a beta for organization administrators. Options: external IdP (Auth0, Keycloak, Authentik), a separate auth proxy, or an authorization server inside this app.

**Decision.** Run the OAuth 2.1 authorization server inside the app using the MCP SDK's endpoints (`/register`, `/authorize`, `/token`, `/revoke`, RFC 8414 and RFC 9728 metadata). `/authorize` redirects to our `/login` consent page, where the single owner approves with a password (scrypt hash in `OWNER_PASSWORD_HASH`). Hardening:

- DCR accepts only allow-listed redirect URIs (`https://claude.ai/api/mcp/auth_callback` by default) and loopback URIs (Claude Code).
- Tokens are bound to the MCP resource URL and checked on each request; access tokens expire after 1 h; refresh tokens rotate on every use (30 days).
- Failed logins are throttled per IP.
- An optional static `AUTH_TOKEN` remains for scripts and tests.

**Consequences.** No extra service or account, works with every Claude surface. We own a small amount of security-critical code (covered by an end-to-end test of the flow). Multi-user would need a real IdP (non-goal).
