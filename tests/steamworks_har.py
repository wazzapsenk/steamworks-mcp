"""Load and replay the recorded Steamworks HAR fixtures (tests/fixtures/steamworks) without a network or a login.

Placeholders used by the recordings: app id ``1000000``, store item id ``2000000``, partner id ``900000``,
session id ``ffffffffffffffffffff0001``. See tests/fixtures/steamworks/README.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "steamworks"

APP_ID = 1000000
STORE_ITEM_ID = 2000000
SESSION_ID = "ffffffffffffffffffff0001"


@dataclass(frozen=True)
class Exchange:
    method: str
    url: str
    status: int
    request_headers: dict[str, str]
    request_body: str
    response_headers: dict[str, str]
    response_body: str | None
    redirect_url: str
    resource_type: str

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def query(self) -> dict[str, str]:
        return dict(parse_qsl(urlsplit(self.url).query))

    @property
    def form(self) -> dict[str, str]:
        """Decoded application/x-www-form-urlencoded request body (empty for other bodies)."""
        if "x-www-form-urlencoded" not in self.request_headers.get("content-type", ""):
            return {}
        return dict(parse_qsl(self.request_body, keep_blank_values=True))

    def json(self) -> Any:
        assert self.response_body is not None, f"{self.method} {self.url} has no recorded body"
        return json.loads(self.response_body)


def load(step: str) -> list[Exchange]:
    """Exchanges of one recorded step, e.g. ``load("cloud/write")``, in recording order."""
    har = json.loads((FIXTURES / f"{step}.har").read_text(encoding="utf-8"))
    out = []
    for e in har["log"]["entries"]:
        req, res = e["request"], e["response"]
        out.append(
            Exchange(
                method=req["method"],
                url=req["url"],
                status=res["status"],
                request_headers={h["name"].lower(): h["value"] for h in req["headers"]},
                request_body=(req.get("postData") or {}).get("text", ""),
                response_headers={h["name"].lower(): h["value"] for h in res["headers"]},
                response_body=res["content"].get("text"),
                redirect_url=res.get("redirectURL", ""),
                resource_type=e.get("_resourceType", ""),
            )
        )
    return out


def steps() -> list[str]:
    return sorted(p.relative_to(FIXTURES).with_suffix("").as_posix() for p in FIXTURES.rglob("*.har"))


class Replay:
    """Answers requests from a step's recording, in order, matching method + path (+ optional form fields)."""

    def __init__(self, *step_names: str) -> None:
        self._pending = [x for s in step_names for x in load(s)]

    def take(self, method: str, path: str, **form: str) -> Exchange:
        for i, x in enumerate(self._pending):
            if x.method == method and x.path == path and all(x.form.get(k) == v for k, v in form.items()):
                return self._pending.pop(i)
        raise LookupError(f"no recorded {method} {path} {form or ''}")
