# Open items

Status as of 2026-10-04. Roadmap steps 1–7 are merged into `main` (PR #1). This list covers what is still to do, mostly operations on the live server. Scope and design are in the [README](../README.md); setup steps in [deployment.md](deployment.md).

## State of the live instance (vault.ironstrike.de)

- Still runs the **old `feat/hybrid-search` build**; `server_info` reports `indexed_notes` instead of an `index` object. The merged `main` is **not deployed**.
- Tools available there: `server_info`, `search`, `get_note`, `list_notes`, `get_backlinks`, `add_note`, `append_note`, `update_note`.
- Semantic search works (`semantic_search: true`, `multilingual-e5-small` loaded, hits report `matched_by: semantic`). 19 notes indexed.
- `VAULT_GIT_SYNC=false`: writes are committed locally only and nothing is pulled automatically.

## To do on the server

1. **Deploy `main`** (and then this branch, see below):
   ```bash
   cd ~/clones/Knowledge-Vault-MCP && git checkout main && git pull
   docker build -t knowledge-vault-mcp:prod-latest .
   cd /var/lib/deployments/Knowledge-Vault-MCP/main && docker compose -p knowledge-vault-mcp__main up -d
   ```
   `index.db` is rebuilt automatically on first start (new layout); `state.db` stays, so Claude stays connected. Check with `server_info`: it should show an `index` object with `ready: true`.
2. **Enable git sync**:
   - Add `ssh/id_ed25519.pub` (in `/var/lib/deployments/Knowledge-Vault-MCP/main/ssh/`) as a deploy key **with write access** on `MarkDrei/Marks-Knowledge-Vault`.
   - `git -C ~/clones/Marks-Knowledge-Vault remote -v` must show `git@github.com:...`; otherwise `git remote set-url origin git@github.com:MarkDrei/Marks-Knowledge-Vault.git`.
   - `.env`: `VAULT_GIT_SYNC=true`; if the vault branch is `master`, also `VAULT_BRANCH=master` (default is `main`).
   - Then every write tool commits and pushes at once; periodic sync and the webhook only pull.
3. **Finish the push webhook**:
   - The webhook on `Marks-Knowledge-Vault` was created **without a secret**; the server rejects unsigned deliveries. Generate one (`openssl rand -hex 32`), enter it in GitHub (*Settings → Webhooks → Edit → Secret*) and as `GITHUB_WEBHOOK_SECRET` in `.env`.
   - Payload URL `https://vault.ironstrike.de/webhook/github`, content type `application/json`, push events only.
   - After deploying, redeliver the ping: expect `200 {"ok": true}`.
4. **Backups**: schedule `kvault backup` for `state.db` (see [deployment §6](deployment.md#6-backups-and-restore)), e.g. `docker compose exec kvault kvault backup /data/backups` from cron, and copy the result off the VPS.

## To do in the code repository

- **Merge the webhook branch**: `claude/git-branches-overview-o0zowg` holds the GitHub push webhook (commit "Add GitHub push webhook for instant sync"), not yet in `main`. Open a PR, merge, deploy (step 1).
- Optional: check how the other repositories wire their GitHub hooks (e.g. a central deploy service) and align if they use a different mechanism than a per-service `POST /webhook/github`.
- Delete the superseded branches `feat/hybrid-search` and `deploy/vps-traefik` once the live server runs `main`.

## Open questions

- **Embedding model benchmark**: write `eval/queries.yaml` from the real vault (30–100 questions, German and English, with the notes that answer them), run `kvault eval eval/queries.yaml`, then `--model intfloat/multilingual-e5-base` and `--model BAAI/bge-m3`. Record the outcome as an ADR (supersede ADR-0004 if another model wins). See [deployment §8](deployment.md#8-retrieval-evaluation-and-model-benchmark).
- **Chunk size**: 1500/150 characters is a default; confirm or tune with the same evaluation set.
- **Search quality with more content**: the vault is still small and dominated by a few long notes; judge semantic quality once there is more varied content.
- **German compound words**: FTS5 tokenizes them poorly; revisit if the evaluation shows misses that vectors do not cover (arc42 §11).
