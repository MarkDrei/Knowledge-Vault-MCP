import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from conftest import BASE, CALLBACK, MCP_HEADERS, PASSWORD, STATIC_TOKEN
from conftest import call_tool as _call_tool


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register(client, redirect=CALLBACK):
    return client.post(
        "/register",
        json={
            "redirect_uris": [redirect],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )


def start_authorize(client, client_id, challenge, state="xyz"):
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": "vault offline_access",
            "resource": f"{BASE}/mcp",
        },
    )
    assert r.status_code == 302, r.text
    login_url = r.headers["location"]
    assert login_url.startswith(f"{BASE}/login?request=")
    return parse_qs(urlparse(login_url).query)["request"][0]


def full_login(client):
    client_id = register(client).json()["client_id"]
    verifier, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    r = client.post("/login", data={"request": request_id, "password": PASSWORD, "action": "approve"})
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["xyz"]
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "redirect_uri": CALLBACK,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": f"{BASE}/mcp",
        },
    )
    assert r.status_code == 200, r.text
    return client_id, r.json(), q["code"][0], verifier


def call_tool(client, token):
    return _call_tool(client, "server_info", token=token)


def test_unauthenticated_mcp_returns_401_with_metadata_pointer(client):
    r = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_discovery_metadata(client):
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == f"{BASE}/mcp"
    assert prm["authorization_servers"][0].rstrip("/") == BASE
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"] == f"{BASE}/register"
    assert "offline_access" in asm["scopes_supported"]


def test_registration_rejects_foreign_redirect(client):
    assert register(client, "https://evil.example/cb").status_code == 400
    assert register(client, "http://127.0.0.1:33418/callback").status_code == 201


def test_full_flow_tool_call_refresh_and_reuse(client):
    client_id, tokens, code, verifier = full_login(client)
    assert tokens["refresh_token"]

    r = call_tool(client, tokens["access_token"])
    assert r.status_code == 200, r.text
    assert "knowledge-vault" in r.text

    # code is single use
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 400

    # refresh rotates: old access and refresh tokens stop working
    r = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        },
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert call_tool(client, tokens["access_token"]).status_code == 401
    assert call_tool(client, new["access_token"]).status_code == 200
    r = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_wrong_password_and_deny(client):
    client_id = register(client).json()["client_id"]
    _, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    page = client.get("/login", params={"request": request_id})
    assert "claude.ai" in page.text and "Claude" in page.text
    r = client.post("/login", data={"request": request_id, "password": "nope", "action": "approve"})
    assert r.status_code == 401
    r = client.post("/login", data={"request": request_id, "action": "deny"})
    assert r.status_code == 302
    assert "error=access_denied" in r.headers["location"]
    # request id is consumed
    assert client.get("/login", params={"request": request_id}).status_code == 400


def test_static_token(client):
    assert call_tool(client, STATIC_TOKEN).status_code == 200
    assert call_tool(client, "wrong-token").status_code == 401
