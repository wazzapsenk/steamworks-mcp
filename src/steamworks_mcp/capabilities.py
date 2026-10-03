"""Capability matrix (``data/capabilities.yaml``): per Steamworks area, what is possible through the official API,
through the opt-in BROWSER mode, and which execution mode the tool uses."""

from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from steamworks_mcp.data import load_yaml
from steamworks_mcp.manifest.state import ExecutionMode

Status = Literal["verified", "partial", "unverified", "never"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Support(Model):
    status: Status
    how: str | None = None


class Area(Model):
    id: str
    name: str
    api: Support
    browser: Support
    default_mode: ExecutionMode
    fallback_mode: ExecutionMode | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _modes(self) -> Area:
        if self.default_mode == "BROWSER" and (self.browser.status != "verified" or not self.fallback_mode):
            raise ValueError(f"{self.id}: BROWSER by default needs a verified browser path and a fallback mode")
        if self.default_mode == "API" and self.api.status == "never":
            raise ValueError(f"{self.id}: API by default but no API exists")
        return self

    def mode(self, browser_enabled: bool) -> ExecutionMode:
        if self.default_mode == "BROWSER" and not browser_enabled:
            assert self.fallback_mode is not None
            return self.fallback_mode
        return self.default_mode


class Permissions(Model):
    browser_mode: list[str]
    builder_account: list[str]
    never_needed: list[str]
    notes: str


class Capabilities(Model):
    last_reviewed: str
    permissions: Permissions
    areas: list[Area]


@cache
def capabilities() -> Capabilities:
    return Capabilities.model_validate(load_yaml("capabilities.yaml"))


_MARK = {"verified": "✅ verified", "partial": "⚠️ partial", "unverified": "❔ unverified", "never": "—"}


def render_markdown(caps: Capabilities | None = None) -> str:
    caps = caps or capabilities()
    out = [
        "# Capabilities",
        "",
        "<!-- Generated from src/steamworks_mcp/data/capabilities.yaml by scripts/gen_docs.py. Do not edit. -->",
        "",
        "What the tool can do in Steamworks, per area. **API** is official, documented tooling (partner Web API with a",
        "publisher key, steamcmd / SteamPipe). **BROWSER** is the opt-in mode that drives the partner site in your own",
        "logged-in browser window, using undocumented behaviour that Valve can change at any time",
        "(see [STEAMWORKS_INTERNALS.md](STEAMWORKS_INTERNALS.md)). **ARTIFACT** means the tool produces the file you",
        "upload; **MANUAL** means a step-by-step checklist that you confirm with `mark_applied`.",
        "",
        f"Last reviewed: {caps.last_reviewed}.",
        "",
        "| Area | Official API | BROWSER | Mode (browser off → on) | Notes |",
        "|---|---|---|---|---|",
    ]

    def cell(s: Support) -> str:
        if not s.how:
            return _MARK[s.status]
        return f"— ({s.how})" if s.status == "never" else f"{_MARK[s.status]}: {s.how}"

    for a in caps.areas:
        api, browser = cell(a.api), cell(a.browser)
        modes = a.mode(False) if a.mode(False) == a.mode(True) else f"{a.mode(False)} → {a.mode(True)}"
        notes = (a.notes or "").replace("\n", " ").replace("|", "\\|")
        out.append(f"| {a.name} | {api} | {browser} | {modes} | {notes} |")
    p = caps.permissions
    out += [
        "",
        "## Steamworks permissions for automation",
        "",
        p.notes,
        "",
        f"- **BROWSER mode user:** {', '.join(p.browser_mode)}",
        f"- **steamcmd builder account:** {', '.join(p.builder_account)}",
        f"- **Never needed by this tool:** {', '.join(p.never_needed)}",
        "",
        "Publishing, Prepare for Publishing, Revert Changes and Release are never automated.",
        "",
    ]
    return "\n".join(out)
