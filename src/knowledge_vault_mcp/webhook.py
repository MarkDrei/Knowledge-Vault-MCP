"""GitHub push webhook: `POST /webhook/github` triggers an immediate pull + reindex.

GitHub signs each delivery with HMAC-SHA256 over the raw body (`X-Hub-Signature-256`)
using the secret configured on both sides (`GITHUB_WEBHOOK_SECRET`). Unsigned or wrongly
signed requests are rejected. A push to `VAULT_BRANCH` only schedules a sync in the
background thread and returns `202` at once; the payload is not trusted for anything else.
"""

import hashlib
import hmac
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from knowledge_vault_mcp.service import VaultService

log = logging.getLogger(__name__)

MAX_BODY = 5 * 1024 * 1024


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class GitHubWebhook:
    def __init__(self, service: VaultService):
        self.service = service
        self.settings = service.settings

    async def handle(self, request: Request) -> Response:
        secret = self.settings.github_webhook_secret
        if secret is None:
            return JSONResponse({"error": "webhook not configured"}, status_code=404)
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"error": "payload too large"}, status_code=413)
        if not valid_signature(secret.get_secret_value(), body, request.headers.get("x-hub-signature-256")):
            log.warning("webhook: rejected delivery with invalid signature")
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return JSONResponse({"ok": True})
        if event != "push":
            return JSONResponse({"ignored": f"event {event}"})
        try:
            ref = json.loads(body).get("ref", "")
        except (ValueError, AttributeError):
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if ref != f"refs/heads/{self.settings.vault_branch}":
            return JSONResponse({"ignored": f"push to {ref}"})
        log.info("webhook: push to %s, scheduling sync", ref)
        self.service.request_sync()
        return JSONResponse({"sync": "scheduled"}, status_code=202)
