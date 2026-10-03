# ADR-0001: Python and the official MCP SDK

Status: accepted (2026-10-03)

**Context.** The server needs MCP over HTTP, an OAuth authorization server, text extraction (PDF/DOCX/HTML) and local embeddings. The ML and document tooling is strongest in Python.

**Decision.** Python 3.12+, official `mcp` SDK v2 (`MCPServer`, Streamable HTTP, its OAuth endpoints), Starlette/uvicorn underneath, `uv` for dependencies.

**Consequences.** One language for server, indexing and ML. The SDK's OAuth handlers (metadata, DCR, PKCE checks, token endpoint) are reused, so only storage and the login step are our code. We follow SDK major versions (pinned `<3`).
