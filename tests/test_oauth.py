"""The built-in OAuth sign-in for remote clients (ChatGPT, claude.ai, codex mcp login), end to end over HTTP."""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from steamworks_mcp.__main__ import build_http_app, http_problems
from steamworks_mcp.config import Config
from steamworks_mcp.oauth import redirect_allowed

TOKEN = "s" * 32
CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@dataclass
class Server:
    base: str
    home: Path


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    config = Config(workspace_root=tmp_path, http_token=TOKEN, public_url=base, home_dir=tmp_path / "home")
    srv = uvicorn.Server(
        uvicorn.Config(build_http_app(config, "127.0.0.1", port), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield Server(base, tmp_path / "home")
    srv.should_exit = True
    thread.join(timeout=5)


def register(base: str, redirect: str = CHATGPT) -> httpx.Response:
    return httpx.post(
        f"{base}/register",
        json={
            "redirect_uris": [redirect],
            "client_name": "ChatGPT",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )


def start(base: str, client_id: str, challenge: str, resource: str | None = None) -> str:
    """/authorize -> the approval page's request id."""
    res = httpx.get(
        f"{base}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CHATGPT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st4te",
            "resource": resource or f"{base}/mcp",
        },
    )
    assert res.status_code == 302, res.text
    location = res.headers["location"]
    assert location.startswith(f"{base}/oauth/approve?request=")
    return parse_qs(urlsplit(location).query)["request"][0]


def approve(base: str, request_id: str, token: str = TOKEN, decision: str = "allow") -> httpx.Response:
    return httpx.post(f"{base}/oauth/approve", data={"request": request_id, "token": token, "decision": decision})


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def mcp(base: str, token: str | None) -> httpx.Response:
    headers = dict(MCP_HEADERS)
    if token:
        headers["authorization"] = f"Bearer {token}"
    return httpx.post(f"{base}/mcp", json=INIT, headers=headers)


def test_metadata_is_published(server: Server) -> None:
    prm = httpx.get(f"{server.base}/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == f"{server.base}/mcp" and prm["authorization_servers"] == [server.base]
    meta = httpx.get(f"{server.base}/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"] and meta["registration_endpoint"]
    unauth = mcp(server.base, None)
    assert unauth.status_code == 401 and "resource_metadata" in unauth.headers["www-authenticate"]


def test_full_sign_in_and_token_use(server: Server) -> None:
    client_id = register(server.base).json()["client_id"]
    verifier, challenge = pkce()
    request_id = start(server.base, client_id, challenge)

    page = httpx.get(f"{server.base}/oauth/approve", params={"request": request_id})
    assert page.status_code == 200 and "chatgpt.com" in page.text and "ChatGPT" in page.text
    assert (
        page.headers["x-frame-options"] == "DENY"
        and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    )
    assert approve(server.base, request_id, token="wrong").status_code == 401

    done = approve(server.base, request_id)
    assert done.status_code == 302
    query = parse_qs(urlsplit(done.headers["location"]).query)
    assert done.headers["location"].startswith(CHATGPT) and query["state"] == ["st4te"]
    form = {
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": CHATGPT,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": f"{server.base}/mcp",
    }
    tokens = httpx.post(f"{server.base}/token", data=form).json()
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert httpx.post(f"{server.base}/token", data=form).status_code == 400  # a code works once

    assert mcp(server.base, tokens["access_token"]).status_code == 200
    assert mcp(server.base, TOKEN).status_code == 200  # header clients (Codex, Claude Code) keep working
    assert mcp(server.base, "nope").status_code == 401

    refresh = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
    renewed = httpx.post(f"{server.base}/token", data=refresh).json()
    assert renewed["access_token"] != tokens["access_token"]
    assert httpx.post(f"{server.base}/token", data=refresh).status_code == 400  # rotated
    assert mcp(server.base, renewed["access_token"]).status_code == 200

    stored = (server.home / "oauth.json").read_text(encoding="utf-8")
    assert client_id in stored and renewed["refresh_token"] not in stored and tokens["access_token"] not in stored
    assert TOKEN not in stored


def test_codes_only_go_to_known_clients(server: Server) -> None:
    bad = register(server.base, "https://evil.example/callback")
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_redirect_uri"
    assert register(server.base, "http://127.0.0.1:1455/callback").status_code == 201  # codex mcp login
    client_id = register(server.base).json()["client_id"]
    _, challenge = pkce()
    other = httpx.get(
        f"{server.base}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CHATGPT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://other.example/mcp",
        },
    )
    assert "error=invalid_target" in other.headers.get("location", "")  # tokens only for this server
    assert redirect_allowed("https://claude.ai/api/mcp/auth_callback")
    assert not redirect_allowed("https://chatgpt.com.evil.example/connector_platform_oauth_redirect")
    assert redirect_allowed("https://my.example/cb", extra=("https://my.example/",))


def test_deny_and_lockout(server: Server) -> None:
    client_id = register(server.base).json()["client_id"]
    _, challenge = pkce()
    denied = approve(server.base, start(server.base, client_id, challenge, resource=server.base), decision="deny")
    assert denied.status_code == 302 and "error=access_denied" in denied.headers["location"]

    request_id = start(server.base, client_id, challenge)
    codes = [approve(server.base, request_id, token="wrong").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 403]
    assert approve(server.base, request_id).status_code == 400  # that request is gone


def test_public_url_must_be_https(tmp_path: Path) -> None:
    def problems(url: str) -> list[str]:
        return http_problems(Config(workspace_root=tmp_path, http_token=TOKEN, public_url=url), "127.0.0.1")

    assert problems("https://my-tunnel.example") == []
    assert problems("http://127.0.0.1:8787") == []
    assert any("https://" in p for p in problems("http://my-tunnel.example"))
    assert any("without a path" in p for p in problems("https://my-tunnel.example/mcp"))
