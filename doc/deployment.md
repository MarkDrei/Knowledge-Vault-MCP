# Deployment and operations

Target: one small VPS (2–4 vCPU, 4–8 GB RAM, no GPU), the server behind a TLS reverse proxy, the vault in a git remote (GitHub, GitLab, Gitea, ...). Scope and architecture: [README](../README.md), [arc42](arc42.md).

## 1. Prepare the vault remote

1. Create a key pair for the server only: `ssh-keygen -t ed25519 -N "" -f kvault_deploy_key`.
2. Add `kvault_deploy_key.pub` as a **deploy key with write access** to the vault repository (write is needed for the inbox and edit tools).
3. The server uses `VAULT_REPO_URL=git@github.com:<you>/<vault>.git`, `VAULT_SSH_KEY=<path to private key>` and `VAULT_BRANCH` (default `main`). The host key is accepted on first connect (`StrictHostKeyChecking=accept-new`); to pin it, put the host into the server user's `~/.ssh/known_hosts` beforehand.
4. On the devices where you use Obsidian, sync through the same remote (e.g. the Obsidian Git plugin). The server commits as `GIT_AUTHOR_NAME <GIT_AUTHOR_EMAIL>`, so its changes are easy to spot in the history.

## 2. Configure

```bash
cp .env.example .env
uv run kvault hash-password      # or: docker compose run --rm kvault kvault hash-password
```

Set at least `PUBLIC_URL=https://vault.example.com`, `OWNER_PASSWORD_HASH`, `VAULT_REPO_URL`, `VAULT_SSH_KEY`. Leave `AUTH_TOKEN` empty unless scripts need it; if set, use a long random value (`openssl rand -base64 32`). Never commit `.env`.

The first start downloads the embedding model (~0.5 GB for `intfloat/multilingual-e5-small`) from `huggingface.co` into `MODEL_CACHE_PATH`. After that no outbound access besides the git remote is needed. To run fully offline, copy a populated cache directory onto the server.

## 3a. Run with Docker Compose (recommended)

```bash
cp docker-compose.example.yml docker-compose.yml
cp Caddyfile.example Caddyfile          # set your domain
mkdir -p secrets && cp kvault_deploy_key secrets/ && chmod 600 secrets/kvault_deploy_key
```

Mount the key read-only and point the server at it, e.g. in `docker-compose.yml`:

```yaml
services:
  kvault:
    volumes:
      - kvault-data:/data
      - ./secrets/kvault_deploy_key:/run/secrets/kvault_deploy_key:ro
    environment:
      VAULT_SSH_KEY: /run/secrets/kvault_deploy_key
```

The container runs as uid 1000; the key file must be readable by it (`chown 1000 secrets/kvault_deploy_key`). Start with `docker compose up -d --build`; follow the first indexing with `docker compose logs -f kvault`. `/data` holds `state.db`, `index.db`, the vault clone and the model cache.

## 3b. Run with systemd

```bash
sudo useradd --system --create-home --home-dir /opt/kvault kvault
sudo -u kvault git clone <this repo> /opt/kvault/app
cd /opt/kvault/app && sudo -u kvault uv sync --frozen --no-dev
```

`/etc/systemd/system/kvault.service`:

```ini
[Unit]
Description=Knowledge-Vault-MCP
After=network-online.target
Wants=network-online.target

[Service]
User=kvault
WorkingDirectory=/opt/kvault/app
EnvironmentFile=/opt/kvault/app/.env
ExecStart=/opt/kvault/app/.venv/bin/kvault serve
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/kvault/app/data
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Keep `HOST=127.0.0.1` and put Caddy (or nginx) in front; `FORWARDED_ALLOW_IPS=127.0.0.1` so the login throttling sees real client addresses. `sudo systemctl enable --now kvault`.

## 4. Reverse proxy and firewall

- Caddy: `Caddyfile.example` (automatic certificates). nginx: proxy `/` to `127.0.0.1:8000`, set `X-Forwarded-For` / `X-Forwarded-Proto`, and disable response buffering for `/mcp`.
- Only 80/443 (and SSH) need to be open. The app port must not be reachable from outside.
- Claude's apps connect from Anthropic's cloud (egress `160.79.104.0/21`). Restricting `/mcp` to that range is optional extra hardening, but blocks direct use from other clients (Claude Code on your laptop, scripts). `/login` must stay reachable from your own browser.

## 5. Connect Claude

*Settings → Connectors → Add custom connector*, URL `https://<your-domain>/mcp`, no client ID/secret. Approve on the `/login` page with the owner password. Check with the `server_info` tool; it shows the index status.

## 6. Backups and restore

| Data | Backup | Restore |
|---|---|---|
| Vault | The git remote **is** the backup; back up the remote as you would any repository. | Nothing to do; the server clones it. |
| `state.db` (OAuth clients, token hashes) | `kvault backup <dir>`: consistent online copy, keeps the newest 14 (`--keep`). | Stop the server, copy the backup to `STATE_DB_PATH`, start. Without it, clients simply reconnect and log in again. |
| `index.db` | None; derived data. | Delete it, start the server (or `kvault reindex --full`). |
| Model cache | None. | Downloaded again on start. |

Daily backup with cron (systemd install):

```cron
17 3 * * * kvault cd /opt/kvault/app && .venv/bin/kvault backup /opt/kvault/backups
```

With Docker: `docker compose exec kvault kvault backup /data/backups`, then copy `/data/backups` off the machine.

## 7. Monitoring and maintenance

- `GET /healthz` → `{"status":"ok","index_ready":true}`; used by the container health check.
- `server_info` (MCP tool) and the logs show the last sync time, the last index run and the last error (e.g. a failing `git pull`).
- `kvault search "query"` and `kvault reindex [--full]` work against the same `.env` on the host (stop the server for `--full` on a large vault to avoid duplicate work).
- Updating: `git pull && uv sync --frozen --no-dev && systemctl restart kvault`, or `docker compose up -d --build`. Changing `EMBEDDING_MODEL` triggers a full reindex on the next start.
- Rotating the owner password: set a new `OWNER_PASSWORD_HASH` and restart. To log out every client, stop the server and delete `state.db`.

## 8. Retrieval evaluation and model benchmark

1. `cp eval/queries.example.yaml eval/queries.yaml` (git-ignored) and write 30–100 real questions with the notes that answer them, German and English, including paraphrases and cross-language questions.
2. `kvault eval eval/queries.yaml` reports hit@1, recall@5 and MRR per search mode (keyword, semantic, hybrid) and language, plus query latency.
3. Benchmark other models on the same vault: `kvault eval eval/queries.yaml --model intfloat/multilingual-e5-base` (and `BAAI/bge-m3`). Each model gets its own `eval-<model>.db` next to `index.db`; the server's index is not touched. Watch the index time and RAM use (`/usr/bin/time -v`).
4. If another model wins clearly, record it in a new ADR superseding ADR-0004 and set `EMBEDDING_MODEL`. Use the same set to tune `CHUNK_MAX_CHARS` / `CHUNK_OVERLAP` (`--db` with a scratch file).
