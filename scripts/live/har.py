"""HAR 1.2 recording of what a Playwright page sends to Steam (documents, XHR and fetch calls), plus entries for
requests made outside the browser (the Web API). Bodies are read as soon as each response arrives, because a page
that navigates right after a request would otherwise lose them.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

KEEP_TYPES = {"document", "xhr", "fetch"}
STEAM_HOST = re.compile(r"(^|\.)(steamgames\.com|steampowered\.com|steam-api\.com|steamcommunity\.com)$")


def is_text(mime: str) -> bool:
    return bool(
        re.match(r"(text/|application/(json|javascript|x-www-form-urlencoded|xml|vnd\.valve))", mime, re.I)
    ) or ("charset=" in mime.lower())


def query_of(url: str) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)]


def describe_multipart(body: bytes, content_type: str) -> str:
    """Binary file parts replaced by a size note; text parts stay readable."""
    m = re.search(r'boundary=("?)([^";]+)\1', content_type, re.I)
    if not m:
        return f"[multipart body, {len(body)} bytes]"
    boundary = m.group(2)
    parts = body.decode("latin-1").split(f"--{boundary}")
    out = []
    for part in parts:
        split = part.find("\r\n\r\n")
        if split < 0:
            out.append(part)
            continue
        head, content = part[:split], part[split + 4 :]
        file_name = re.search(r'filename="([^"]*)"', head, re.I)
        part_type = re.search(r"content-type:\s*([^\r\n]+)", head, re.I)
        textual = (
            not file_name
            or is_text(part_type.group(1) if part_type else "")
            or bool(re.search(r"\.(json|csv|vdf|txt)$", file_name.group(1), re.I))
        )
        decoded = (
            content.encode("latin-1").decode("utf-8", "replace")
            if textual
            else f"[binary file omitted: {len(content.encode('latin-1')) - 2} bytes]\r\n"
        )
        out.append(f"{head}\r\n\r\n{decoded}")
    return f"--{boundary}".join(out)


def _iso(ms: float | None = None) -> str:
    t = dt.datetime.fromtimestamp(ms / 1000, dt.UTC) if ms and ms > 0 else dt.datetime.now(dt.UTC)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class HarRecorder:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.pending: set[asyncio.Task[None]] = set()
        self.bodies: dict[Any, asyncio.Task[bytes | None]] = {}
        self.seen: set[int] = set()
        self.active = False

    def attach(self, page: Any) -> None:
        page.on("response", self._on_response)
        page.on("requestfinished", self._track)
        page.on("requestfailed", self._track)

    def _on_response(self, res: Any) -> None:
        if self.active:
            self.bodies[res.request] = asyncio.ensure_future(self._body(res))

    @staticmethod
    async def _body(res: Any) -> bytes | None:
        try:
            body: bytes = await res.body()
            return body
        except Exception:  # redirects and aborted requests have no body
            return None

    def begin(self) -> None:
        self.entries, self.active = [], True

    def add_manual(self, entry: dict[str, Any]) -> None:
        if self.active:
            self.entries.append(entry)

    def _track(self, req: Any) -> None:
        if self.active:
            task = asyncio.ensure_future(self._add(req))
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)

    async def _add(self, req: Any) -> None:
        chain: list[Any] = []
        r = req
        while r is not None:
            chain.insert(0, r)
            r = r.redirected_from
        for r in chain:
            if id(r) in self.seen:
                continue
            self.seen.add(id(r))
            if r.resource_type not in KEEP_TYPES or not STEAM_HOST.search(urlsplit(r.url).hostname or ""):
                continue
            try:
                self.entries.append(await self._entry(r))
            except Exception as exc:  # keep recording the other requests
                print(f"  (recorder: {str(exc).splitlines()[0]})")

    async def _entry(self, r: Any) -> dict[str, Any]:
        res = await r.response()
        req_headers = await r.headers_array()
        req_type = next((h["value"] for h in req_headers if h["name"].lower() == "content-type"), "")
        post = r.post_data_buffer
        timing = r.timing
        request: dict[str, Any] = {
            "method": r.method,
            "url": r.url,
            "httpVersion": "HTTP/1.1",
            "headers": req_headers,
            "queryString": query_of(r.url),
            "cookies": [],
            "headersSize": -1,
            "bodySize": len(post) if post else 0,
        }
        if post:
            text = (
                describe_multipart(post, req_type)
                if "multipart/" in req_type.lower()
                else post.decode("utf-8", "replace")
            )
            request["postData"] = {"mimeType": req_type, "text": text}
        entry: dict[str, Any] = {
            "startedDateTime": _iso(timing.get("startTime")),
            "time": max(0, timing.get("responseEnd", 0)),
            "request": request,
            "response": {
                "status": res.status if res else 0,
                "statusText": res.status_text if res else "",
                "httpVersion": "HTTP/1.1",
                "headers": await res.headers_array() if res else [],
                "cookies": [],
                "content": {"size": 0, "mimeType": ""},
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": -1,
            },
            "cache": {},
            "timings": {
                "send": 0,
                "wait": max(0, timing.get("responseStart", 0)),
                "receive": max(0, timing.get("responseEnd", 0) - timing.get("responseStart", 0)),
            },
            "_resourceType": r.resource_type,
        }
        if r.failure:
            entry["_failureText"] = r.failure
        if res:
            mime = await res.header_value("content-type") or ""
            entry["response"]["content"]["mimeType"] = mime
            entry["response"]["redirectURL"] = await res.header_value("location") or ""
            if res.status < 300 or res.status >= 400:
                task = self.bodies.get(r)
                body = (await task) if task else await self._body(res)
                if body:
                    entry["response"]["content"]["size"] = entry["response"]["bodySize"] = len(body)
                    if is_text(mime) or mime == "":
                        entry["response"]["content"]["text"] = body.decode("utf-8", "replace")
                    else:
                        entry["response"]["content"]["comment"] = f"binary body omitted ({len(body)} bytes)"
        return entry

    async def end(self, file: Path, meta: dict[str, Any]) -> int:
        while self.pending:
            await asyncio.gather(*list(self.pending))
        self.active = False
        entries = sorted(self.entries, key=lambda e: e["startedDateTime"])
        har = {
            "log": {
                "version": "1.2",
                "creator": {"name": "steamworks-mcp/record", "version": "0.2"},
                "entries": entries,
                "_meta": meta,
            }
        }
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(har, indent=2, ensure_ascii=False), encoding="utf-8")
        self.entries, self.bodies = [], {}
        return len(entries)


def httpx_entry(response: httpx.Response, redact: list[str]) -> dict[str, Any]:
    """HAR entry for a request made with httpx (no browser involved). ``redact`` strings never reach the file."""

    def scrub(s: str) -> str:
        for secret in redact:
            if secret:
                s = s.replace(secret, "<redacted>")
        return s

    req = response.request
    body = req.content.decode("utf-8", "replace") if req.content else ""
    entry: dict[str, Any] = {
        "startedDateTime": _iso(),
        "time": response.elapsed.total_seconds() * 1000 if response.elapsed else 0,
        "request": {
            "method": req.method,
            "url": scrub(str(req.url)),
            "httpVersion": "HTTP/1.1",
            "headers": [],
            "queryString": [{"name": k, "value": scrub(v)} for k, v in req.url.params.multi_items()],
            "cookies": [],
            "headersSize": -1,
            "bodySize": len(req.content or b""),
        },
        "response": {
            "status": response.status_code,
            "statusText": response.reason_phrase,
            "httpVersion": "HTTP/1.1",
            "headers": [{"name": k, "value": v} for k, v in response.headers.items() if k.lower() != "set-cookie"],
            "cookies": [],
            "content": {
                "size": len(response.content),
                "mimeType": response.headers.get("content-type", ""),
                "text": scrub(response.text),
            },
            "redirectURL": response.headers.get("location", ""),
            "headersSize": -1,
            "bodySize": len(response.content),
        },
        "cache": {},
        "timings": {"send": 0, "wait": 0, "receive": 0},
        "_resourceType": "fetch",
    }
    if body:
        entry["request"]["postData"] = {"mimeType": req.headers.get("content-type", ""), "text": scrub(body)}
    return entry
