"""Owner login / consent page that completes the OAuth /authorize step."""

import asyncio
import html
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from knowledge_vault_mcp.auth.passwords import verify_password
from knowledge_vault_mcp.auth.provider import VaultOAuthProvider

MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Knowledge Vault – authorize</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:26rem;margin:3rem auto;padding:0 1rem;color:#222}}
 .box{{border:1px solid #ccc;border-radius:8px;padding:1.25rem}}
 input[type=password]{{width:100%;padding:.6rem;font-size:1rem;box-sizing:border-box;margin:.5rem 0 1rem}}
 button{{padding:.6rem 1rem;font-size:1rem;margin-right:.5rem}} .err{{color:#b00020}} code{{word-break:break-all}}
</style></head><body><div class="box">
<h1 style="font-size:1.25rem">Authorize access to your vault</h1>
<p><strong>{client}</strong> wants read and write access to your knowledge vault.</p>
<p>After approval you will be sent to <code>{redirect_host}</code>.</p>
{error}
<form method="post" action="/login">
 <input type="hidden" name="request" value="{request_id}">
 <label>Owner password<input type="password" name="password" autocomplete="current-password" autofocus></label>
 <button type="submit" name="action" value="approve">Approve</button>
 <button type="submit" name="action" value="deny">Deny</button>
</form></div></body></html>"""


class LoginHandler:
    def __init__(self, provider: VaultOAuthProvider):
        self.provider = provider
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _render(self, request_id: str, error: str = "", status: int = 200) -> Response:
        pending = self.provider.get_pending(request_id)
        if pending is None:
            return HTMLResponse(
                "Authorization request expired. Start again from your client.", status_code=400
            )
        page = _PAGE.format(
            client=html.escape(pending.client_name),
            redirect_host=html.escape(urlparse(str(pending.params.redirect_uri)).netloc),
            request_id=html.escape(request_id),
            error=f'<p class="err">{html.escape(error)}</p>' if error else "",
        )
        return HTMLResponse(
            page, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"}
        )

    def _locked_out(self, ip: str) -> bool:
        q = self._failures[ip]
        while q and q[0] < time.time() - FAILURE_WINDOW:
            q.popleft()
        return len(q) >= MAX_FAILURES

    async def get(self, request: Request) -> Response:
        return self._render(request.query_params.get("request", ""))

    async def post(self, request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request", ""))
        if self.provider.get_pending(request_id) is None:
            return self._render(request_id)
        if form.get("action") == "deny":
            return RedirectResponse(self.provider.deny_authorization(request_id), status_code=302)

        ip = request.client.host if request.client else "unknown"
        if self._locked_out(ip):
            return self._render(request_id, "Too many failed attempts. Try again later.", status=429)
        stored = self.provider.settings.owner_password_hash
        if stored is None:
            return self._render(request_id, "Login is disabled: OWNER_PASSWORD_HASH is not set.", status=503)
        if not verify_password(str(form.get("password", "")), stored.get_secret_value()):
            self._failures[ip].append(time.time())
            await asyncio.sleep(1)
            return self._render(request_id, "Wrong password.", status=401)

        self._failures.pop(ip, None)
        return RedirectResponse(self.provider.complete_authorization(request_id), status_code=302)
