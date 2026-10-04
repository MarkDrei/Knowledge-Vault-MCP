# Knowledge-Vault-MCP

A self-hosted [MCP](https://modelcontextprotocol.io) server that exposes a git-backed, Obsidian-style knowledge vault for **hybrid retrieval (RAG)** and **safe capture of new knowledge**. It runs entirely locally on a small VPS.

> Status: roadmap steps 1–7 implemented (v1 feature complete). Still open: running the embedding model benchmark on a real evaluation set (see [Open questions](#open-questions)). This README is the source of truth for scope; architecture and decisions are in [doc/arc42.md](doc/arc42.md) and [doc/adr/](doc/adr/), operations in [doc/deployment.md](doc/deployment.md).

## Goal

Give an LLM client (Claude, or any MCP client) fast, grounded access to a personal knowledge base, and a safe way to add to it, without the server owning the knowledge itself.

- The **knowledge lives in a separate git repository** (an Obsidian vault). This project only clones, syncs, indexes and serves it.
- **Retrieval** is hybrid (keyword + semantic), fully local, returning ranked chunks with sources so the client can ground its answers.
- **New knowledge** is captured into an *inbox* with rich metadata. Curating the inbox into the main vault is a separate process and out of scope here.

## Non-goals (v1)

- Multi-user or multi-tenant operation. One user, one vault per server instance.
- Generating answers. The server retrieves; the MCP client's LLM does the generating.
- Curating or promoting inbox notes into the main vault.
- Searching images or scanned documents (no OCR, captions or image embeddings). Images are stored and linked only.
- Document ingestion tools (upload/convert). Documents are committed to the vault repo by the user and picked up by the indexer.
- Obsidian Canvas and Dataview support.
- Reranking and external/hosted model APIs.

## Vault content

The indexer understands:

| Content | Handling |
|---|---|
| Markdown notes | Chunked by heading structure, with the heading path kept as context |
| Frontmatter (YAML) | Stored as filterable metadata |
| `#tags` and frontmatter tags | Stored lowercased and filterable; tags and links inside code are ignored |
| `[[wikilinks]]` (incl. aliases, headings) | Parsed into a link graph; powers backlinks. Resolved like Obsidian: by file name, or by path suffix for `[[Folder/Note]]`, case-insensitive; ambiguous names go to the shortest path |
| PDF, DOCX, HTML | Text extracted and indexed, tied to the file path. Document headings become the heading path; PDF chunks are headed `Page N`. No OCR ([ADR-0010](doc/adr/0010-document-extraction.md)). Files over `MAX_DOCUMENT_MB` are skipped |
| Images and other attachments | Not indexed; kept in the repo and resolved from embeds like `![[img.png]]` |

`.git/`, `.obsidian/` and `.trash/` are ignored.

## Architecture

```
            MCP client (Claude, ...)
                     |  Streamable HTTP + OAuth 2.1 bearer token
              TLS reverse proxy (Caddy/nginx)
                     |
        +------------+-------------------------+
        |        Knowledge-Vault-MCP (Python)  |
        |                                      |
        |  MCP tools: search / get_note /      |
        |             get_backlinks / write    |
        |  OAuth server (DCR, PKCE, /login)    |
        |                                      |
        |  Indexer ---- Retriever              |
        |  (parse, chunk, embed)  (BM25+vec)   |
        |        \          /                  |
        |      SQLite (single file)            |
        |   FTS5 | sqlite-vec | links | tags   |
        |                                      |
        |  Git sync (pull / commit / push)     |
        +------------------+-------------------+
                           |
                 local clone of vault repo  <---->  remote git repo
```

### Storage

A single SQLite database: FTS5 for BM25 keyword search, `sqlite-vec` for vector search, plus tables for notes, chunks, links and tags. One file means trivial backup and minimal operations on a small VPS. The index is derived data and can always be rebuilt from the repo.

### Retrieval

1. Query is run against FTS5 (BM25, title and headings weighted higher than text) and against the vectors (exact cosine scan with sqlite-vec).
2. Results are fused (Reciprocal Rank Fusion, `k = 60`) into one ranked list.
3. Optional filters, applied inside both queries: path prefix, tags (all must match; `infra` also matches `infra/vps`), `status`, `include_inbox`, last-modified range (time of the last commit touching the file).
4. Each hit returns path, title, heading path, the chunk text, score, whether it matched by keyword, semantically or both, and note metadata.

**Chunking.** Notes are split at headings; each chunk keeps its heading path (`Note > Section > Subsection`) as context for the embedding. Sections longer than `CHUNK_MAX_CHARS` (default 1500, well below the 512-token limit of e5) are split at paragraph, sentence or word boundaries with `CHUNK_OVERLAP` (default 150) characters of overlap. The defaults are to be validated with the evaluation set.

Embeddings are computed locally on CPU with a small **multilingual model** suited to German and English. Default: `intfloat/multilingual-e5-small` (384 dims, see [ADR-0004](doc/adr/0004-embedding-model-multilingual-e5-small.md)); to be confirmed by a benchmark on a real German/English sample. The model runs as ONNX via fastembed, without PyTorch ([ADR-0009](doc/adr/0009-onnx-embeddings-fastembed.md)). It is downloaded once from Hugging Face into `MODEL_CACHE_PATH`; afterwards there are no network calls at query or index time.

### Sync and indexing

- The server clones the vault repo on first start and builds the index in the background; the MCP endpoint is available immediately (search notes that the index is incomplete until the first run finishes).
- Every `SYNC_INTERVAL` seconds, and immediately on a GitHub push webhook (`POST /webhook/github`, HMAC-signed with `GITHUB_WEBHOOK_SECRET`, push to `VAULT_BRANCH` only), a `git pull` triggers an **incremental** reindex: diff the indexed commit against the new HEAD, re-parse and re-embed only changed files, drop deleted ones. Files with an unchanged content hash are not re-embedded.
- A full rescan happens automatically on first start, after rewritten history and when `EMBEDDING_MODEL` changes. `kvault reindex [--full]` does the same from the command line; `kvault search "query"` queries the index for debugging.
- Uncommitted files in the work tree (e.g. a bind-mounted checkout that is also edited on the host) are indexed as well; the periodic pull is skipped while tracked files have uncommitted changes.
- If the embedding model cannot be loaded (or `EMBEDDINGS_ENABLED=false`), the server runs with keyword search only; `server_info` shows `semantic_search: false` and the reason.

## MCP tools (v1)

| Tool | Purpose |
|---|---|
| `search` | Hybrid search. Params: `query`, `limit`, optional `tags`, `path_prefix`, `status`, date filters. Returns ranked chunks with sources. |
| `get_note` | Return a note's full content and metadata (frontmatter, tags, outgoing links with resolved paths, last modified, `sha` of the content). Accepts a vault path or a name as in a `[[wikilink]]`. For PDF/DOCX/HTML it returns the extracted text. |
| `list_notes` | List notes below a folder (`path_prefix`) with title, kind, status and last change; sorted by path, up to 1000. |
| `get_backlinks` | Return notes that link to a given note, with the line containing each link, heading and alias. |
| `server_info` | Version and index status (notes, chunks, indexed commit, last sync, last error). |
| `add_note` | Capture new knowledge into the inbox (see below). Commits and pushes. |
| `update_note` | Replace the full content of a Markdown note. Optional `expected_sha` (from `get_note`) rejects the update if the note changed in between. |
| `append_note` | Append Markdown to the end of a note. |
| `move_note` | Move/rename a note or attachment. Rewrites wikilinks in linking notes in the same commit, keeping headings, aliases and embeds; links that still resolve are left alone, and links that the move would redirect to the moved file are pinned to their old target. |
| `delete_note` | Delete a note; links to it are reported as `dangling_backlinks`, not changed. |

Edit tools work on any existing note in the vault; each change is one commit, then push. A frontmatter `updated` field is refreshed on edits if the note has one (no frontmatter is added to notes without it). An edit that changes nothing creates no commit.

### Capturing new knowledge (inbox)

New knowledge never lands directly in the curated vault. `add_note` writes into an inbox folder (default `_inbox/`) so it can be reviewed and curated later by a process outside this project.

**Naming.** Every new note gets a human-readable, practically collision-free file name:

```
YYYY-MM-DD-HHMMSS-<slug-of-title>.md
e.g. 2026-10-03-184512-vps-backup-strategy.md
```

Time is local to the configured timezone (default `Europe/Berlin`). If a file of that name already exists, a numeric suffix is appended (`-2`, `-3`, ...); existing files are never overwritten.

**Metadata.** The server stamps frontmatter on every captured note; the caller supplies title, body and optionally tags, a source and a source URL. `captured_by` is the name the OAuth client registered with (e.g. `Claude`). The body gets a `# Title` heading unless it starts with one:

```yaml
---
id: 2026-10-03-184512-vps-backup-strategy
title: VPS backup strategy
created: 2026-10-03T18:45:12+02:00
updated: 2026-10-03T18:45:12+02:00
status: inbox          # inbox -> curated (set by the curation process)
source: mcp            # origin: mcp | web | manual | ...
captured_by: claude    # client/agent name as reported over MCP
source_url: https://…  # only if given
tags: []
---
```

`updated` is maintained by the server on edits. Inbox notes are indexed like any other note and are marked by `status: inbox`, so searches can include or exclude them.

### Write safety

- All git operations (periodic sync and writes) are serialized by one in-process lock.
- Writes are refused while tracked files have uncommitted changes, because the rollback (`git reset --hard`) would destroy them. Untracked files are never touched by a rollback.
- With `VAULT_GIT_SYNC=false` writes are committed locally only (no pull, no push, no periodic pull).
- Before every write the server runs `git pull --rebase` and brings the index up to date (so `move_note` sees all current links).
- Write, commit (clear, machine-generated message), push, then reindex the touched files immediately.
- **Any conflict or failed push aborts the operation**, resets the local branch to its previous state and returns an error to the client. Nothing is auto-resolved.
- The design assumes a single server instance per vault (see [ADR-0006](doc/adr/0006-git-write-flow.md)).

## Authentication

The server is its own OAuth 2.1 authorization server, so it can be added as a **custom connector** in Claude (mobile, web, desktop) and in Claude Code ([ADR-0005](doc/adr/0005-built-in-oauth-server.md)):

- Discovery via `401` + `WWW-Authenticate: resource_metadata=...`, RFC 9728 and RFC 8414 metadata.
- Dynamic Client Registration, limited to allow-listed redirect URIs (`https://claude.ai/api/mcp/auth_callback`) and loopback URIs.
- Authorization code + PKCE S256. Approval happens on a `/login` page with the owner password.
- Access tokens 1 h, rotating refresh tokens 30 days, all stored hashed in `state.db`.
- Optional static `AUTH_TOKEN` for scripts and tests.

## Configuration

Environment variables or a `.env` file (see [.env.example](.env.example)), no secrets in the repo:

- `PUBLIC_URL` (external base URL; MCP endpoint is `PUBLIC_URL/mcp`), `HOST`, `PORT`, `FORWARDED_ALLOW_IPS`
- `OWNER_PASSWORD_HASH` (from `kvault hash-password`), optional `AUTH_TOKEN`
- `STATE_DB_PATH` (OAuth state, back it up), `DB_PATH` (search index, rebuildable)
- `VAULT_REPO_URL`, `VAULT_BRANCH`, `VAULT_PATH` (clone location), `VAULT_SSH_KEY` (deploy key for an SSH remote), `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` (identity of server commits). Without `VAULT_REPO_URL` the server uses an existing checkout at `VAULT_PATH` (e.g. bind-mounted) or creates a local repository there. `VAULT_GIT_SYNC` (default `true`): pull before and push after writes, pull periodically; `false` commits locally only.
- `INBOX_DIR` (default `_inbox`; `00-Inbox` in the live vault), `TIMEZONE`
- `DB_PATH`, `EMBEDDING_MODEL` (`hash` = no model, for tests only), `EMBEDDINGS_ENABLED` (default `true`; `false` = keyword search only), `MODEL_CACHE_PATH`, `CHUNK_MAX_CHARS`, `CHUNK_OVERLAP`, `MAX_DOCUMENT_MB`, `SYNC_INTERVAL` (seconds, `0` disables periodic sync), `GITHUB_WEBHOOK_SECRET` (enables `POST /webhook/github`)

## Development

```bash
uv sync                     # Python 3.12+, installs dev tools too
uv run pytest               # tests, including the full OAuth flow
uv run ruff check . && uv run ruff format --check .
uv run kvault hash-password # prints a value for OWNER_PASSWORD_HASH
cp .env.example .env        # set PUBLIC_URL=http://localhost:8000 for local tests
uv run kvault serve
```

On Windows use WSL or Docker. `curl localhost:8000/healthz` should return `{"status":"ok",...}`. Without `VAULT_REPO_URL` the server uses a local git repository in `VAULT_PATH`; `EMBEDDING_MODEL=hash` avoids the model download (keyword search works, semantic search is meaningless). Tests use a temporary bare repository as the remote and the hash embedder, so they need no network.

Other commands (all read the same `.env`):

| Command | Purpose |
|---|---|
| `kvault reindex [--full] [--no-pull]` | Pull and update the index; `--full` re-scans every file |
| `kvault search "query" [--mode keyword\|semantic\|hybrid] [--tag t] [--path p]` | Query the index from the shell |
| `kvault eval eval/queries.yaml [--model M] [--json]` | Retrieval quality per search mode and language; benchmark another model in a separate index |
| `kvault backup <dir> [--keep 14]` | Consistent online copy of `state.db` |

### Connecting Claude

1. Deploy behind TLS ([doc/deployment.md](doc/deployment.md): deploy key, Docker with Caddy or an existing Traefik (`docker-compose.traefik.example.yml`), systemd, backups). The live instance runs at `https://vault.ironstrike.de`, see [deployment §3a′](doc/deployment.md#live-instance-vaultironstrikede).
2. In Claude: *Settings → Connectors → Add custom connector*, URL `https://<your-domain>/mcp`. Leave client ID/secret empty.
3. Claude opens the `/login` page; approve with the owner password. The connector then appears in the mobile app too.

## Deployment target

A single small VPS (2-4 vCPU, 4-8 GB RAM, CPU only), vault size under ~10k notes. Run as a systemd service or container behind a TLS-terminating reverse proxy; see [doc/deployment.md](doc/deployment.md). Streamable HTTP (stateless, OAuth) is the primary transport; stdio may be added for local development.

## Tech stack

- Python, official MCP SDK
- SQLite (FTS5 + sqlite-vec)
- Local embedding model on CPU (multilingual, German + English) via fastembed / ONNX Runtime
- Git via the system `git` binary
- Document extraction: pypdf, python-docx, stdlib `html.parser`

## Roadmap

1. **Skeleton:** project layout, config, HTTP MCP server with OAuth, health check. *(done)*
2. **Vault:** clone/sync, Markdown + frontmatter + wikilink parsing, SQLite schema. *(done)*
3. **Index and search:** chunking, local embeddings, hybrid search with RRF, incremental reindex. *(done)*
4. **Read tools:** `get_note`, `get_backlinks`. *(done)*
5. **Write tools:** `add_note` with inbox naming/metadata, edit tools, pull-rebase-push flow. *(done)*
6. **Documents:** PDF/DOCX/HTML text extraction in the indexer. *(done)*
7. **Hardening:** deployment docs, backups, evaluation set for retrieval quality (German + English). *(done: [deployment guide](doc/deployment.md), `kvault backup`, `kvault eval` with an example set; the benchmark itself needs real notes)*

## Open questions

Decided: inbox notes are included in search by default; `include_inbox=false` excludes them. Sync is a GitHub push webhook plus polling as a fallback.


- Confirm the embedding model by benchmark (real notes, CPU latency and RAM): the tooling is in place (`kvault eval --model ...`, [deployment guide §8](doc/deployment.md#8-retrieval-evaluation-and-model-benchmark)); it needs an evaluation set written from the real vault.
- Chunk size and overlap: defaults set (1500/150 characters), to be confirmed with the evaluation set.

## License

None yet (private project, all rights reserved).
