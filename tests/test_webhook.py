import hashlib
import hmac
import json
import time

import pytest
from conftest import BASE
from pydantic import SecretStr
from starlette.testclient import TestClient

from knowledge_vault_mcp.server import create_app

SECRET = "webhook-secret-0123456789"


@pytest.fixture
def hook_client(settings):
    app = create_app(settings.model_copy(update={"github_webhook_secret": SecretStr(SECRET)}))
    with TestClient(app, base_url=BASE) as c:
        assert app.state.service.wait_ready(30)
        c.service = app.state.service
        yield c


def deliver(client, payload: dict, event="push", secret=SECRET):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )


def test_push_to_branch_pulls_and_indexes(hook_client, remote):
    remote.commit({"Neu/Webhook.md": "# Webhook\n\nkakapo"})
    r = deliver(hook_client, {"ref": "refs/heads/main"})
    assert r.status_code == 202
    for _ in range(100):
        if hook_client.service.retriever.search("kakapo", 1, mode="keyword"):
            break
        time.sleep(0.1)
    hits = hook_client.service.retriever.search("kakapo", 1, mode="keyword")
    assert hits and hits[0].path == "Neu/Webhook.md"


def test_rejects_bad_signature_and_ignores_other_refs(hook_client):
    assert deliver(hook_client, {"ref": "refs/heads/main"}, secret="wrong").status_code == 401
    r = hook_client.post("/webhook/github", content=b"{}", headers={"X-GitHub-Event": "push"})
    assert r.status_code == 401
    assert deliver(hook_client, {}, event="ping").json() == {"ok": True}
    assert deliver(hook_client, {"ref": "refs/heads/feature"}).json() == {
        "ignored": "push to refs/heads/feature"
    }
    assert deliver(hook_client, {"action": "opened"}, event="issues").status_code == 200


def test_disabled_without_secret(client):
    assert client.post("/webhook/github", content=b"{}").status_code == 404
