import json

import pytest
from starlette.testclient import TestClient
from vaultutil import Remote

from knowledge_vault_mcp.auth.passwords import hash_password
from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.server import create_app

BASE = "http://localhost:8000"
PASSWORD = "correct horse battery staple"
STATIC_TOKEN = "static-test-token-0123456789"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

SAMPLE_VAULT = {
    "Ops/Backup-Strategie.md": """---
tags: [ops]
status: curated
---
# Backup-Strategie

Tägliche Sicherung des VPS mit restic nach S3. Siehe [[Restic]] und [[Infra/VPS#Netz|den VPS]].

## Aufbewahrung

Sieben tägliche, vier wöchentliche und zwölf monatliche Snapshots. #ops/backup
""",
    "Tools/Restic.md": """# Restic

Restic is a fast, encrypted backup program. Used by [[Backup-Strategie]].

## Retention

`restic forget --keep-daily 7` prunes old snapshots.
""",
    "Infra/VPS.md": """---
title: VPS
tags: infra
---
## Netz

The VPS runs Caddy as reverse proxy for [[Tools/Restic|restic]] metrics.
""",
    "Kochen/Brot.md": "# Sauerteigbrot\n\nMehl, Wasser, Salz und Geduld. #kochen\n",
    ".obsidian/workspace.json": "{}",
}


@pytest.fixture(scope="session")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def remote(tmp_path) -> Remote:
    return Remote(tmp_path, SAMPLE_VAULT)


@pytest.fixture
def settings(tmp_path, password_hash, remote) -> Settings:
    return Settings(
        _env_file=None,
        public_url=BASE,
        owner_password_hash=password_hash,
        auth_token=STATIC_TOKEN,
        state_db_path=tmp_path / "state.db",
        db_path=tmp_path / "index.db",
        vault_repo_url=remote.url,
        vault_path=tmp_path / "vault",
        embedding_model="hash",
        sync_interval=0,
    )


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        assert app.state.service.wait_ready(30)
        c.service = app.state.service
        yield c


def call_tool(client, name: str, arguments: dict | None = None, token: str = STATIC_TOKEN):
    """Call an MCP tool; returns the raw HTTP response."""
    return client.post(
        "/mcp",
        headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


def tool(client, name: str, _token: str = STATIC_TOKEN, **arguments) -> dict:
    """Call a tool and return its result as a dict; raises if the tool reported an error."""
    r = call_tool(client, name, arguments, token=_token)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    if result.get("isError"):
        raise ToolFailed(result["content"][0]["text"])
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


class ToolFailed(Exception):
    pass
