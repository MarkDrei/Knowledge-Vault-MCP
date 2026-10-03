# ADR-0008: Stateless Streamable HTTP

Status: accepted (2026-10-03)

**Context.** All tools are request/response; nothing needs server-initiated messages. Mobile clients reconnect often and the server restarts on deploys.

**Decision.** Serve MCP at `/mcp` with Streamable HTTP in stateless mode with JSON responses.

**Consequences.** No session state to lose on restart, no sticky routing, simpler proxying. Server-to-client notifications and long-running progress streams are not available; revisit if a tool needs them.
