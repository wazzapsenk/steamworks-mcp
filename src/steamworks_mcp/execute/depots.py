"""Depot settings (App Admin > SteamPipe > Depots): operating system, architecture and language of each depot.

The Depots page saves its whole depot list at once (``/depots/upload/<appid>``); a depot missing from that list would
be removed. So the tool never builds the list itself: it changes the selects of the depot rows in the page and clicks
the page's own Save, which sends every depot as the page knows it. Only depots that already exist in Steamworks are
changed; new depots are created there by hand. Saves go into the unpublished app data.
"""

from __future__ import annotations

from typing import Any

from steamworks_mcp.execute import sync
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport

SECTION = "depots"
OS = {"all": "", "windows": "windows", "macos": "macos", "linux": "linux"}
ARCH = {"all": "", "32": "32", "64": "64"}
KEYS = ("oslist", "osarch", "language")


async def read_section(t: Transport, appid: int) -> dict[str, Any]:
    return {"depots": await P.read_depots(t, appid)}


def desired(values: dict[str, Any], app: str) -> dict[str, Any]:
    """``{"settings": {depot id: {oslist, osarch, language}}, "problems": [...]}`` from apps.<app>.builds.depots."""
    profile = (values.get("apps") or {}).get(app) or {}
    settings: dict[str, dict[str, str]] = {}
    for d in (profile.get("builds") or {}).get("depots") or []:
        if d.get("depot_id"):
            settings[str(d["depot_id"])] = {
                "oslist": OS[d.get("os") or "all"],
                "osarch": ARCH[d.get("arch") or "all"],
                "language": d.get("language") or "",
            }
    return {"settings": settings}


def changes(want: dict[str, Any], current: dict[str, Any], force: bool = False) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for depot, keys in want["settings"].items():
        have = (current["depots"].get(depot) or {}).get("config") or {}
        diff = {k: v for k, v in keys.items() if force or str(have.get(k) or "") != v}
        if diff and depot in current["depots"]:
            out[depot] = diff
    return out


def plan(appid: int, want: dict[str, Any], current: dict[str, Any], force: bool = False) -> list[sync.Op]:
    ops: list[sync.Op] = []
    missing = [d for d in want["settings"] if d not in current["depots"]]
    if missing:
        ops.append(
            sync.Op(
                SECTION,
                "skip",
                ", ".join(missing),
                None,
                "Not in Steamworks: create the depot(s) on the Depots page first; this tool only changes settings.",
                sync._nothing,
            )
        )
    diff = changes(want, current, force)
    if diff:

        async def save(t: Transport, c: dict[str, dict[str, str]] = diff) -> None:
            await P.save_depots(t, appid, c)

        before = {
            d: {k: str(((current["depots"][d].get("config") or {}).get(k)) or "") for k in c} for d, c in diff.items()
        }
        ops.append(sync.Op(SECTION, "save", f"settings of {len(diff)} depot(s)", before, diff, save))
    return ops
