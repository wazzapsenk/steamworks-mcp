"""A one-user OAuth 2.1 authorization server for the HTTP transport, for clients that sign in with OAuth (ChatGPT,
claude.ai, ``codex mcp login``). Clients that can send a header keep using ``Authorization: Bearer <token>``.

It follows the MCP authorization spec as far as the SDK does: protected resource metadata, authorization server
metadata, dynamic client registration, authorization code + PKCE (S256), refresh tokens, revocation, and tokens bound
to this server (RFC 8707 ``resource``). Signing in means typing the server's own token (``STEAMWORKS_MCP_TOKEN``) on
an approval page served by this server; it is never part of a URL.

Hardening:
* Clients may only register redirect URIs of known MCP hosts (ChatGPT, Claude) or loopback addresses, plus the
  prefixes in ``STEAMWORKS_MCP_OAUTH_REDIRECTS``, so an approval can never send a code to some other site.
* The approval page shows which client and which host the code goes to; it cannot be framed.
* Wrong tokens are counted per request and globally; after too many the page locks for a while.
* Refresh tokens are stored as SHA-256 hashes (``~/.steamworks-mcp/oauth.json``); access tokens live in memory.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from steamworks_mcp.manifest.io import atomic_write

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
REQUEST_TTL = 600
MAX_TRIES_PER_REQUEST = 5
MAX_FAILURES = 20
LOCK_SECONDS = 600
STATIC_CLIENT = "static-bearer-token"
MAX_CLIENTS = 100

KNOWN_REDIRECTS = (
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/",
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
"""Redirect URI prefixes accepted at registration besides loopback addresses."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def redirect_allowed(uri: str, extra: tuple[str, ...] = ()) -> bool:
    parts = urlsplit(uri)
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return True
    return parts.scheme == "https" and any(uri.startswith(p) for p in (*KNOWN_REDIRECTS, *extra))


@dataclass
class Pending:
    client_id: str
    params: AuthorizationParams
    expires: float
    tries: int = 0


@dataclass
class LocalOAuth:
    """``OAuthAuthorizationServerProvider`` for one user who proves themselves with the server token."""

    server_token: str = field(repr=False)
    public_url: str
    resource_url: str
    store: Path
    extra_redirects: tuple[str, ...] = ()
    _pending: dict[str, Pending] = field(default_factory=dict, repr=False)
    _codes: dict[str, AuthorizationCode] = field(default_factory=dict, repr=False)
    _access: dict[str, AccessToken] = field(default_factory=dict, repr=False)
    _failures: list[float] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------------------------------------ storage

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write(self.store, json.dumps(data, indent=2) + "\n")
        if os.name != "nt":
            self.store.chmod(0o600)

    # ------------------------------------------------------------------------------------------------ clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self._load().get("clients", {}).get(client_id)
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        bad = [str(u) for u in client_info.redirect_uris or [] if not redirect_allowed(str(u), self.extra_redirects)]
        if bad or not client_info.redirect_uris:
            raise RegistrationError(
                "invalid_redirect_uri",
                f"Redirect URI not allowed: {', '.join(bad) or 'none given'}. Allowed: ChatGPT, Claude, loopback "
                "addresses, and the prefixes in STEAMWORKS_MCP_OAUTH_REDIRECTS.",
            )
        data = self._load()
        if len(data.get("clients", {})) >= MAX_CLIENTS:
            raise RegistrationError(
                "invalid_client_metadata", f"{MAX_CLIENTS} clients are registered already; delete oauth.json to reset."
            )
        data.setdefault("clients", {})[str(client_info.client_id)] = client_info.model_dump(
            mode="json", exclude_none=True
        )
        self._save(data)

    # ------------------------------------------------------------------------------------------------ authorize

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (
            self.resource_url.rstrip("/"),
            self.public_url.rstrip("/"),
        ):
            raise AuthorizeError("invalid_target", f"This server only issues tokens for {self.resource_url}.")
        self._expire()
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = Pending(str(client.client_id), params, time.time() + REQUEST_TTL)
        return f"{self.public_url.rstrip('/')}/oauth/approve?request={request_id}"

    def _expire(self) -> None:
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v.expires >= now}
        self._codes = {k: v for k, v in self._codes.items() if v.expires_at >= now}
        self._failures = [t for t in self._failures if t > now - LOCK_SECONDS]

    async def approval_page(self, request: Request) -> Response:
        self._expire()
        request_id = request.query_params.get("request", "")
        if request.method == "POST":
            form = await request.form()
            request_id = str(form.get("request", ""))
        pending = self._pending.get(request_id)
        if pending is None:
            return _page("This sign-in request has expired. Start again from your MCP client.", status=400)
        client = await self.get_client(pending.client_id)
        name = (client.client_name if client else None) or pending.client_id
        target = urlsplit(str(pending.params.redirect_uri)).netloc
        if request.method == "GET":
            return _approval_form(request_id, name, target)

        form = await request.form()
        if form.get("decision") != "allow":
            del self._pending[request_id]
            return _redirect(str(pending.params.redirect_uri), error="access_denied", state=pending.params.state)
        if len(self._failures) >= MAX_FAILURES:
            return _page("Too many wrong tokens. Try again in a few minutes.", status=429)
        if not hmac.compare_digest(str(form.get("token", "")).strip().encode(), self.server_token.encode()):
            pending.tries += 1
            self._failures.append(time.time())
            if pending.tries >= MAX_TRIES_PER_REQUEST:
                del self._pending[request_id]
                return _page("Too many wrong tokens for this request. Start again from your MCP client.", status=403)
            return _approval_form(request_id, name, target, error="That is not this server's token.", status=401)

        del self._pending[request_id]
        p = pending.params
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=p.scopes or [],
            expires_at=time.time() + CODE_TTL,
            client_id=pending.client_id,
            code_challenge=p.code_challenge,
            redirect_uri=p.redirect_uri,
            redirect_uri_provided_explicitly=p.redirect_uri_provided_explicitly,
            resource=self.resource_url,  # tokens only ever work for this server
            subject="owner",
        )
        return _redirect(str(p.redirect_uri), code=code, state=p.state)

    # ------------------------------------------------------------------------------------------------ tokens

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self._expire()
        code = self._codes.get(authorization_code)
        return code if code is not None and code.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "Authorization code already used or expired.")
        return self._issue(str(client.client_id), authorization_code.scopes, authorization_code.resource)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._access[_hash(access)] = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TTL,
            resource=resource or self.resource_url,
            subject="owner",
        )
        data = self._load()
        tokens = {k: v for k, v in data.get("refresh", {}).items() if v["expires_at"] > now}
        tokens[_hash(refresh)] = {
            "client_id": client_id,
            "scopes": scopes,
            "expires_at": now + REFRESH_TTL,
            "resource": resource or self.resource_url,
        }
        data["refresh"] = tokens
        self._save(data)
        return OAuthToken(
            access_token=access, expires_in=ACCESS_TTL, refresh_token=refresh, scope=" ".join(scopes) or None
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        raw = self._load().get("refresh", {}).get(_hash(refresh_token))
        if not raw or raw["client_id"] != client.client_id or raw["expires_at"] < time.time():
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=raw["client_id"],
            scopes=raw["scopes"],
            expires_at=raw["expires_at"],
            resource=raw.get("resource"),
            subject="owner",
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        data = self._load()
        if data.get("refresh", {}).pop(_hash(refresh_token.token), None) is None:
            raise TokenError("invalid_grant", "Refresh token already used or revoked.")
        self._save(data)  # rotation: the old refresh token stops working
        return self._issue(str(client.client_id), scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token.encode(), self.server_token.encode()):
            return AccessToken(
                token=token, client_id=STATIC_CLIENT, scopes=[], resource=self.resource_url, subject="owner"
            )
        found = self._access.get(_hash(token))
        if found is None or (found.expires_at or 0) < time.time():
            self._access.pop(_hash(token), None)
            return None
        return found

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "Only the authorization-code grant is supported.")

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._access.pop(_hash(token.token), None)
        data = self._load()
        if data.get("refresh", {}).pop(_hash(token.token), None) is not None:
            self._save(data)


# ---------------------------------------------------------------------------------------------------- pages

HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https: http:; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;line-height:1.5}"
    "input[type=password]{width:100%;padding:.5rem;font-size:1rem;box-sizing:border-box}"
    "button{padding:.5rem 1rem;font-size:1rem;margin:.75rem .5rem 0 0}.err{color:#b00020}"
)


def _page(message: str, status: int = 200) -> HTMLResponse:
    body = (
        f"<!doctype html><meta charset=utf-8><title>steamworks-mcp</title><style>{STYLE}</style>"
        f"<p>{html.escape(message)}</p>"
    )
    return HTMLResponse(body, status_code=status, headers=HEADERS)


def _approval_form(request_id: str, client: str, target: str, error: str = "", status: int = 200) -> HTMLResponse:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    body = (
        f"<!doctype html><meta charset=utf-8><title>Allow access - steamworks-mcp</title><style>{STYLE}</style>"
        f"<h1>Allow access to steamworks-mcp?</h1>"
        f"<p><b>{html.escape(client)}</b> wants to use this server: read and edit your game projects and, if you "
        f"turned them on, act in Steamworks. The sign-in result goes to <b>{html.escape(target)}</b>.</p>"
        f"<p>Only continue if you started this sign-in yourself, from that app.</p>{err}"
        f'<form method="post" action="approve"><input type="hidden" name="request" value="{html.escape(request_id)}">'
        f"<label>Server token (STEAMWORKS_MCP_TOKEN)<br>"
        f'<input type="password" name="token" autocomplete="off" autofocus>'
        f'</label><br><button name="decision" value="allow">Allow</button>'
        f'<button name="decision" value="deny">Deny</button></form>'
    )
    return HTMLResponse(body, status_code=status, headers=HEADERS)


def _redirect(uri: str, **params: str | None) -> RedirectResponse:
    return RedirectResponse(
        construct_redirect_uri(uri, **params), status_code=302, headers={"Cache-Control": "no-store"}
    )
