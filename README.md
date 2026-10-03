# Knowledge-Vault-MCP

A self-hosted [MCP](https://modelcontextprotocol.io) server that exposes a git-backed, Obsidian-style knowledge vault for **hybrid retrieval (RAG)** and **safe capture of new knowledge**. It runs entirely locally on a small VPS.

> Status: design phase. This README is the source of truth for scope; nothing is implemented yet.

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
| `#tags` and frontmatter tags | Stored and filterable |
| `[[wikilinks]]` (incl. aliases, headings) | Parsed into a link graph; powers backlinks |
| PDF, DOCX, HTML | Text extracted and indexed, tied to the file path |
| Images and other attachments | Not indexed; kept in the repo and resolved from embeds like `![[img.png]]` |

## Architecture

```
            MCP client (Claude, ...)
                     |  Streamable HTTP + bearer token
              TLS reverse proxy (Caddy/nginx)
                     |
        +------------+-------------------------+
        |        Knowledge-Vault-MCP (Python)  |
        |                                      |
        |  MCP tools: search / get_note /      |
        |             get_backlinks / write    |
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

1. Query is run against FTS5 (BM25) and against the vector index.
2. Results are fused (Reciprocal Rank Fusion) into one ranked list.
3. Optional filters: path prefix, tags, frontmatter fields (e.g. `status`), date range.
4. Each hit returns path, heading path, score, snippet, and note metadata.

Embeddings are computed locally on CPU with a small **multilingual model** suited to German and English (candidate class: multilingual-e5-small or bge-m3 class; final choice to be benchmarked on a real German/English sample). No network calls at query or index time.

### Sync and indexing

- The server clones the vault repo on first start.
- A periodic `git pull` (and optionally a webhook) triggers an **incremental** reindex: diff the old and new commit, re-parse and re-embed only changed files, drop deleted ones.
- A full reindex is available as a CLI command for model changes or recovery.

## MCP tools (v1)

| Tool | Purpose |
|---|---|
| `search` | Hybrid search. Params: `query`, `limit`, optional `tags`, `path_prefix`, `status`, date filters. Returns ranked chunks with sources. |
| `get_note` | Return a note's full content and metadata by path. |
| `get_backlinks` | Return notes that link to a given note, with link context. |
| `add_note` | Capture new knowledge into the inbox (see below). Commits and pushes. |
| `update_note` / `append_note` / `move_note` / `delete_note` | Edit existing notes. Each is one commit, then push. |

### Capturing new knowledge (inbox)

New knowledge never lands directly in the curated vault. `add_note` writes into an inbox folder (default `_inbox/`) so it can be reviewed and curated later by a process outside this project.

**Naming.** Every new note gets a human-readable, practically collision-free file name:

```
YYYY-MM-DD-HHMMSS-<slug-of-title>.md
e.g. 2026-10-03-184512-vps-backup-strategy.md
```

Time is local to the configured timezone (default `Europe/Berlin`). If a file of that name already exists, a numeric suffix is appended (`-2`, `-3`, ...); existing files are never overwritten.

**Metadata.** The server stamps frontmatter on every captured note; the caller supplies title, body and optionally tags and a source:

```yaml
---
id: 2026-10-03-184512-vps-backup-strategy
title: VPS backup strategy
created: 2026-10-03T18:45:12+02:00
updated: 2026-10-03T18:45:12+02:00
status: inbox          # inbox -> curated (set by the curation process)
source: mcp            # origin: mcp | web | manual | ...
captured_by: claude    # client/agent name as reported over MCP
source_url:            # optional
tags: []
---
```

`updated` is maintained by the server on edits. Inbox notes are indexed like any other note and are marked by `status: inbox`, so searches can include or exclude them.

### Write safety

- Before every write the server runs `git pull --rebase`.
- Write, commit (clear, machine-generated message), push.
- **Any conflict aborts the operation and returns an error** to the client. Nothing is auto-resolved.
- No locking: the design assumes a single writer per vault.

## Configuration (planned)

Environment/config file, no secrets in the repo:

- `VAULT_REPO_URL`, `VAULT_BRANCH`, `VAULT_PATH` (clone location), git credentials (deploy key)
- `INBOX_DIR` (default `_inbox`), `TIMEZONE`
- `AUTH_TOKEN` (bearer token for the HTTP transport)
- `EMBEDDING_MODEL`, `SYNC_INTERVAL`
- `DB_PATH` (SQLite file)

## Deployment target

A single small VPS (2-4 vCPU, 4-8 GB RAM, CPU only), vault size under ~10k notes. Run as a systemd service or container behind a TLS-terminating reverse proxy. Streamable HTTP with a bearer token is the primary transport; stdio may be added for local development.

## Tech stack

- Python, official MCP SDK
- SQLite (FTS5 + sqlite-vec)
- Local embedding model on CPU (multilingual, German + English)
- Git via the system `git` binary
- Document extraction for PDF/DOCX/HTML (library TBD)

## Roadmap

1. **Skeleton:** project layout, config, HTTP MCP server with auth, health check.
2. **Vault:** clone/sync, Markdown + frontmatter + wikilink parsing, SQLite schema.
3. **Index and search:** chunking, local embeddings, hybrid search with RRF, incremental reindex.
4. **Read tools:** `get_note`, `get_backlinks`.
5. **Write tools:** `add_note` with inbox naming/metadata, edit tools, pull-rebase-push flow.
6. **Documents:** PDF/DOCX/HTML text extraction in the indexer.
7. **Hardening:** deployment docs, backups, evaluation set for retrieval quality (German + English).

## Open questions

- Final embedding model (benchmark on real notes, CPU latency and RAM).
- Should inbox notes be included in search by default, or excluded unless requested?
- Webhook vs. polling for sync; which git host will deliver webhooks.
- Chunk size and overlap defaults.

## License

None yet (private project, all rights reserved).
