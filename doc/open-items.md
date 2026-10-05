# Open items

Status as of 2026-10-05. Roadmap steps 1–7 are merged into `main` (PR #1). This list covers what is still to do, mostly operations on the live server. Scope and design are in the [README](../README.md); setup steps in [deployment.md](deployment.md).

## State of the live instance (vault.ironstrike.de)

- Runs the merged `main` (roadmap steps 1–7), deployed 2026-10-05. All tools are available: `search`, `get_note`, `list_notes`, `get_backlinks`, `add_note`, `update_note`, `append_note`, `move_note`, `delete_note`, `server_info`.
- Semantic search works (`multilingual-e5-small`, `semantic_search: true`); 20 notes / 168 chunks indexed.
- `VAULT_GIT_SYNC=true`: every write tool commits and pushes at once; the server pulls every `SYNC_INTERVAL`.
- Not yet live: the GitHub push webhook (not in `main`).
- MCP clients that cached the old tool list (e.g. `search` with `tag` instead of `tags`) pick up the new schema after reconnecting the connector.

## To do on the server

1. ~~Deploy `main`~~ and ~~enable git sync~~: done 2026-10-05. Redeploy the same way once the webhook is merged:
   ```bash
   cd ~/clones/Knowledge-Vault-MCP && git checkout main && git pull
   docker build -t knowledge-vault-mcp:prod-latest .
   cd /var/lib/deployments/Knowledge-Vault-MCP/main && docker compose -p knowledge-vault-mcp__main up -d
   ```
2. **Finish the push webhook**:
   - The webhook on `Marks-Knowledge-Vault` was created **without a secret**; the server rejects unsigned deliveries. Generate one (`openssl rand -hex 32`), enter it in GitHub (*Settings → Webhooks → Edit → Secret*) and as `GITHUB_WEBHOOK_SECRET` in `.env`.
   - Payload URL `https://vault.ironstrike.de/webhook/github`, content type `application/json`, push events only.
   - After deploying, redeliver the ping: expect `200 {"ok": true}`.
3. **Backups**: schedule `kvault backup` for `state.db` (see [deployment §6](deployment.md#6-backups-and-restore)), e.g. `docker compose exec kvault kvault backup /data/backups` from cron, and copy the result off the VPS.

## To do in the code repository

- **Merge the webhook branch**: `claude/git-branches-overview-o0zowg` holds the GitHub push webhook (commit "Add GitHub push webhook for instant sync"), not yet in `main`. Open a PR, merge, redeploy (step 1).
- Optional: check how the other repositories wire their GitHub hooks (e.g. a central deploy service) and align if they use a different mechanism than a per-service `POST /webhook/github`.
- Delete the superseded branches `feat/hybrid-search` and `deploy/vps-traefik` (the live server now runs `main`).

## Open questions

- **Embedding model benchmark**: write `eval/queries.yaml` from the real vault (30–100 questions, German and English, with the notes that answer them), run `kvault eval eval/queries.yaml`, then `--model intfloat/multilingual-e5-base` and `--model BAAI/bge-m3`. Record the outcome as an ADR (supersede ADR-0004 if another model wins). See [deployment §8](deployment.md#8-retrieval-evaluation-and-model-benchmark).
- **Chunk size**: 1500/150 characters is a default; confirm or tune with the same evaluation set.
- **Search quality with more content**: the vault is still small and dominated by a few long notes; judge semantic quality once there is more varied content.
- **German compound words**: FTS5 tokenizes them poorly; revisit if the evaluation shows misses that vectors do not cover (arc42 §11).
