"""How BROWSER-mode requests reach Steamworks.

* ``PlaywrightTransport``: inside the user's logged-in page (``fetch`` with the page's own cookies and CSRF id), in a
  visible browser window with a dedicated profile. Requires the optional ``[browser]`` extra.
* ``ReplayTransport``: answers from recorded HAR fixtures, for offline tests.

Both check every request against :mod:`steamworks_mcp.execute.guard` before sending it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

from steamworks_mcp.execute import guard

BASE = "https://partner.steamgames.com"


class NotLoggedInError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Not logged in to Steamworks. Call steamworks_open and sign in in the browser window that opens."
        )


@dataclass
class Response:
    status: int
    url: str
    text: str
    redirect: str = ""

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as exc:
            # Without a session Steamworks answers AJAX calls with its HTML sign-in page.
            if "goto=" in self.url or "<html" in self.text[:500].lower():
                raise NotLoggedInError() from exc
            raise


class Transport(Protocol):
    async def get(self, path: str) -> Response: ...

    async def post(self, path: str, form: dict[str, str]) -> Response: ...

    async def post_multipart(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
    ) -> Response: ...

    async def upload_store_localization(self, appid: int, file_name: str, data: bytes) -> Response: ...


# ---------------------------------------------------------------------------------------------------- replay


@dataclass
class Sent:
    method: str
    path: str
    form: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


class ReplayTransport:
    """Serves recorded exchanges in order, matched on method + path; remembers what was sent."""

    def __init__(self, har_files: list[Path]) -> None:
        self.queue: list[dict[str, Any]] = []
        for f in har_files:
            self.queue += json.loads(f.read_text(encoding="utf-8"))["log"]["entries"]
        self.sent: list[Sent] = []

    def _take(self, method: str, path: str) -> Response:
        for i, e in enumerate(self.queue):
            u = urlsplit(e["request"]["url"])
            if e["request"]["method"] == method and (u.path + (f"?{u.query}" if u.query else "")).startswith(
                path.split("#")[0]
            ):
                self.queue.pop(i)
                res = e["response"]
                return Response(
                    res["status"], e["request"]["url"], res["content"].get("text") or "", res.get("redirectURL", "")
                )
        raise LookupError(f"no recorded {method} {path}")

    async def get(self, path: str) -> Response:
        guard.check("GET", BASE + path)
        self.sent.append(Sent("GET", path))
        res = self._take("GET", path)
        while res.status in (301, 302) and res.redirect:
            target = urlsplit(res.redirect)
            nxt = target.path + (f"?{target.query}" if target.query else "")
            try:
                res = self._take("GET", nxt)
            except LookupError:
                return Response(200, res.redirect, "", "")
        return res

    async def post(self, path: str, form: dict[str, str]) -> Response:
        guard.check("POST", BASE + path)
        self.sent.append(Sent("POST", path, dict(form)))
        return self._take("POST", path)

    async def post_multipart(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
    ) -> Response:
        guard.check("POST", BASE + path)
        self.sent.append(Sent("POST", path, dict(fields), [f"{k}:{v[0]}" for k, v in files.items()]))
        return self._take("POST", path)

    async def upload_store_localization(self, appid: int, file_name: str, data: bytes) -> Response:
        self.sent.append(
            Sent("UPLOAD", f"/admin/game/editbyappid/{appid}", {"file": file_name, "json": data.decode("utf-8")})
        )
        res = await self.get(f"/admin/game/editbyappid/{appid}")
        path = urlsplit(res.url).path
        item = path.rstrip("/").split("/")[-1]
        guard.check("POST", f"{BASE}/admin/game/uploadloc/{item}")
        return self._take("POST", f"/admin/game/uploadloc/{item}")


# ---------------------------------------------------------------------------------------------------- playwright

FETCH = """async ({url, method, body, json}) => {
  const init = {method, credentials: "same-origin", headers: {Accept: "application/json, text/plain, */*"}};
  if (body !== null) { const p = new URLSearchParams(body); p.set("sessionid", window.g_sessionID); init.body = p; }
  const r = await fetch(url, init);
  return {status: r.status, url: r.url, text: await r.text()};
}"""

MULTIPART = """async ({url, fields, files}) => {
  const fd = new FormData();
  fd.set("sessionid", window.g_sessionID);
  for (const [k, v] of Object.entries(fields)) fd.set(k, v);
  for (const [k, f] of Object.entries(files)) {
    const bin = Uint8Array.from(atob(f.b64), c => c.charCodeAt(0));
    fd.set(k, new Blob([bin], {type: f.mime}), f.name);
  }
  const r = await fetch(url, {method: "POST", body: fd, credentials: "same-origin"});
  return {status: r.status, url: r.url, text: await r.text()};
}"""


class PlaywrightTransport:
    """Requests from inside the logged-in Steamworks page (its own session cookie and CSRF id; never read here)."""

    def __init__(self, page: Any) -> None:
        self.page = page

    async def _ensure_partner_page(self) -> None:
        if not str(self.page.url).startswith(BASE):
            await self.page.goto(BASE + "/", wait_until="domcontentloaded")

    async def get(self, path: str) -> Response:
        guard.check("GET", BASE + path)
        await self._ensure_partner_page()
        r = await self.page.evaluate(FETCH, {"url": BASE + path, "method": "GET", "body": None, "json": False})
        return Response(r["status"], r["url"], r["text"])

    async def post(self, path: str, form: dict[str, str]) -> Response:
        guard.check("POST", BASE + path)
        await self._ensure_partner_page()
        r = await self.page.evaluate(FETCH, {"url": BASE + path, "method": "POST", "body": form, "json": False})
        return Response(r["status"], r["url"], r["text"])

    async def post_multipart(
        self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
    ) -> Response:
        guard.check("POST", BASE + path)
        await self._ensure_partner_page()
        payload = {k: {"name": n, "b64": base64.b64encode(b).decode(), "mime": m} for k, (n, b, m) in files.items()}
        r = await self.page.evaluate(MULTIPART, {"url": BASE + path, "fields": fields, "files": payload})
        return Response(r["status"], r["url"], r["text"])

    async def upload_store_localization(self, appid: int, file_name: str, data: bytes) -> Response:
        """The store page's own Localization-tab import (it posts the whole store form, so it is driven in the page)."""
        guard.check("GET", f"{BASE}/admin/game/editbyappid/{appid}")
        await self.page.goto(f"{BASE}/admin/game/editbyappid/{appid}", wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1500)
        await self.page.locator("#tab_localization").click()
        await self.page.locator('input[name="localization_files[]"]').set_input_files(
            files=[{"name": file_name, "mimeType": "application/json", "buffer": data}]
        )
        async with self.page.expect_navigation(timeout=90_000):
            await self.page.locator("#tab_localization_content button[type=submit]").click()
        return Response(200, str(self.page.url), "")


def form_body(form: dict[str, str]) -> str:
    return urlencode(form)


def parse_body(body: str) -> dict[str, str]:
    return dict(parse_qsl(body, keep_blank_values=True))
