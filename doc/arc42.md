# Knowledge-Vault-MCP – Architecture (arc42, condensed)

Scope and feature details live in the [README](../README.md); decisions in [adr/](adr/). This document only covers what is needed to understand the structure.

## 1. Introduction and goals

An MCP server that gives an LLM client grounded, fast access to a personal Obsidian vault kept in git, and a safe way to add and edit knowledge.

| Priority | Quality goal | Meaning |
|---|---|---|
| 1 | Security | Only the owner can read or write the vault, also from Claude mobile over the internet. |
| 2 | Data safety | Git is the source of truth. The server never loses, overwrites or silently merges user content. |
| 3 | Retrieval quality | Relevant German and English chunks in the top results. |
| 4 | Operability | Runs on a small CPU-only VPS; one process, few files, trivial backup. |

Stakeholder: one owner, who is user, operator and developer.

## 2. Constraints

- Python, official MCP SDK (v2), Streamable HTTP transport.
- Must work as a claude.ai **custom connector** (web, desktop, mobile), so OAuth 2.1 with DCR and PKCE is required.
- Everything local: no hosted model APIs, no external search service.
- VPS: 2–4 vCPU, 4–8 GB RAM, no GPU; vault < ~10k notes.

## 3. Context and scope

```
 Owner ── Claude apps (mobile/web/desktop) ──HTTPS──┐
 Owner ── Claude Code / scripts ──────────HTTPS─────┤
                                                     ▼
                                        Knowledge-Vault-MCP ──git (SSH deploy key)── Vault remote repo
                                                     ▲
 Owner ── Obsidian (desktop/mobile) ── git ──────────┘ (indirectly, via the vault remote)
```

- **Claude apps**: reach the server via Anthropic's cloud (egress `160.79.104.0/21`); authenticate with OAuth.
- **Vault remote**: the only place knowledge is stored. Obsidian edits arrive here; the server pulls and pushes.

## 4. Solution strategy

- Git repo = truth; SQLite index = derived, rebuildable cache ([ADR-002](adr/0002-single-sqlite-index.md)).
- Hybrid retrieval: BM25 (FTS5) + vectors (sqlite-vec), fused with RRF ([ADR-007](adr/0007-hybrid-retrieval-rrf.md)).
- Small local multilingual embedding model ([ADR-004](adr/0004-embedding-model-multilingual-e5-small.md)).
- Built-in single-owner OAuth server, no external IdP ([ADR-005](adr/0005-built-in-oauth-server.md)).
- Writes are serialized and go pull → write → commit → push; any conflict aborts ([ADR-006](adr/0006-git-write-flow.md)).

## 5. Building block view

```
knowledge_vault_mcp/
  cli.py          serve, hash-password, reindex, search, eval, backup
  evaluation.py   retrieval metrics per search mode, model benchmark in a separate index
  backup.py       online backup of state.db
  config.py       Settings from env / .env
  server.py       assembles MCPServer, OAuth routes, /healthz, /login
  auth/           provider (OAuth AS), store (state DB), login page, passwords
  vault/          repo (git binary), vault (clone, lock, sync), paths, markdown (frontmatter, tags,
                  wikilinks, link rewriting), links (Obsidian link resolution)
  service.py      wires vault, index and retrieval; background sync thread
  index/          db (schema of index.db), chunker, embedder (fastembed/ONNX), extract (PDF/DOCX/HTML),
                  indexer (incremental)
  retrieval/      search: BM25 + vector search, RRF, filters
  writes.py       write transaction (pull → change → commit → push → reindex, reset on failure),
                  inbox naming/frontmatter, link rewriting for moves
  tools/          MCP tools: search, get_note, list_notes, get_backlinks, add/update/append/move/delete_note
```

| Block | Responsibility |
|---|---|
| auth | Issues and verifies tokens; owner login/consent page. State in `state.db`. |
| vault | Owns the local clone; the only block that runs `git`. Holds the write lock. |
| index | Turns changed files into notes, chunks, links, tags and embeddings in `index.db`. |
| retrieval | Answers queries from `index.db`. Read only. |
| writes | Write operations as git transactions on the vault, under the vault lock. |
| tools | Thin MCP layer: validates input, calls vault/retrieval/writes, shapes output. |

## 6. Runtime view

**Connecting Claude mobile (once):** client calls `/mcp` → `401` + `resource_metadata` → reads protected-resource and authorization-server metadata → registers via `/register` (DCR) → `/authorize` with PKCE → server redirects to `/login` → owner enters password → code → `/token` → access + refresh token. Refresh tokens rotate on use.

**Search:** `search` → embed query → FTS5 query and exact vector scan, both with the filters in SQL → RRF → ranked chunks with path, heading path, text, metadata.

**Write (e.g. `add_note`):** acquire write lock → refuse if tracked files have uncommitted changes → `git pull --rebase` → incremental reindex → write file(s) → `git commit` → `git push` → incremental reindex → release lock. A failed pull or push resets the local branch to the commit before the operation, removes files it created and returns an error. With `VAULT_GIT_SYNC=false` pull and push are skipped.

**Sync:** background thread every `SYNC_INTERVAL` → acquire write lock → `git pull --rebase` (skipped if git sync is off or tracked files are dirty) → diff indexed commit..HEAD plus uncommitted and previously uncommitted files → reindex changed, drop deleted files. On start the same runs once as a full scan; the server already accepts requests meanwhile.

## 7. Deployment view

```
VPS ── Caddy or Traefik (TLS, :443) ──► kvault container/systemd unit (:8000)
                              └─ /data: state.db, index.db, vault/ (git clone or bind mount), models/ (model cache)
```

Backup: `state.db` (tokens, clients) via `kvault backup`. `index.db`, `vault/` and `models/` are rebuildable from the remote repo and Hugging Face. Step-by-step setup (deploy key, Docker or systemd, proxy, cron backup, monitoring): [deployment.md](deployment.md). Live instance: `https://vault.ironstrike.de` behind the shared Traefik, vault bind-mounted from the host checkout.

## 8. Cross-cutting concepts

- **Security:** OAuth 2.1 (PKCE S256, DCR limited to allow-listed redirect URIs, rotating refresh tokens, tokens stored hashed); optional static token for scripts; DNS rebinding protection (Host allow-list); login throttling.
- **Configuration:** environment / `.env` only, no secrets in the repo.
- **Note identity:** vault-relative path; inbox notes additionally carry `id` in frontmatter.
- **Time:** all timestamps in the configured timezone (default Europe/Berlin), ISO 8601 with offset.

## 9. Architecture decisions

See [adr/](adr/):

1. [Python and the official MCP SDK](adr/0001-python-mcp-sdk.md)
2. [Single SQLite file for the index](adr/0002-single-sqlite-index.md)
3. [Separate state database](adr/0003-separate-state-db.md)
4. [Embedding model: multilingual-e5-small](adr/0004-embedding-model-multilingual-e5-small.md)
5. [Built-in single-owner OAuth server](adr/0005-built-in-oauth-server.md)
6. [Git write flow and edit scope](adr/0006-git-write-flow.md)
7. [Hybrid retrieval with RRF, no reranker](adr/0007-hybrid-retrieval-rrf.md)
8. [Stateless Streamable HTTP](adr/0008-stateless-http.md)
9. [ONNX embeddings via fastembed](adr/0009-onnx-embeddings-fastembed.md)
10. [Document text extraction](adr/0010-document-extraction.md)

## 10. Quality scenarios

| Scenario | Expected |
|---|---|
| Unknown client calls `/mcp` without a token | `401` with resource metadata pointer, no data. |
| Password guessing on `/login` | Locked after 5 failures per IP for 15 minutes. |
| Obsidian pushed a change to a note the server is editing | Push or rebase fails → operation aborted, error returned, local clone reset. |
| `index.db` deleted | `kvault reindex` rebuilds it; no data loss. |
| German query "Datensicherung" | Finds note titled "Backup-Strategie" (via vectors). |
| Search on a 10k-note vault | p95 < 500 ms on 2 vCPU (measured by `kvault eval`, latency column). |
| Evaluation set (German + English) | Hybrid recall@5 at least as good as each single mode; tracked with `kvault eval`. |

## 11. Risks and technical debt

- German compound words are poorly handled by FTS5's tokenizer; mitigated by vectors, revisit with the evaluation set.
- `sqlite-vec` is pre-1.0; brute-force KNN is fine at this size but the API may change.
- DCR lets anyone register a client; tokens still require the owner password. Redirect URI allow-list limits phishing.
- Single writer assumption: a second server instance on the same vault would conflict (aborts, no corruption).
- The first start needs Hugging Face to download the model; the ONNX export paths of the custom-registered models are not under our control (ADR-0009).
- PDF text quality depends on the text layer; scanned or multi-column PDFs index poorly (ADR-0010).
- The full index on first start holds the vault lock, so write tools wait until it is done.

## 12. Glossary

| Term | Meaning |
|---|---|
| Vault | The Obsidian folder / git repo holding the notes. |
| Inbox | `_inbox/` folder where captured notes land, `status: inbox`. |
| Chunk | Section of a note (by heading) that is indexed and returned by search. |
| RRF | Reciprocal Rank Fusion: combines ranked lists by summing `1/(k + rank)`. |
| DCR | OAuth Dynamic Client Registration (RFC 7591). |
