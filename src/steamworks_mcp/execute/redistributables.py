"""Steam's common redistributables (App Admin > Installation > Redistributables).

Steamworks' "Your Game Build" checklist wants the needed ones ticked or "none needed" confirmed. The page saves on
every click (``/apps/setcommonredists/<appid>``: "none needed" plus every offered redistributable on or off), into the
unpublished app data. steamworks.yaml names them (``vc2022``, ``directx``, …, or ``none``); scan_project drafts them
from the engine and the game's binaries.
"""

from __future__ import annotations

from typing import Any

from steamworks_mcp.execute import sync
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.scanners.redist import DEPOTS, DEPRECATED

SECTION = "redistributables"
NAMES = {v: k for k, v in DEPOTS.items()}


async def read_section(t: Transport, appid: int) -> dict[str, Any]:
    """``{"offered": {name: depot id}, "selected": [names], "none_needed": bool}``. A redistributable this tool has
    no name for yet is listed as ``depot_<id>``."""
    page = await P.read_common_redists(t, appid)
    name = {d: NAMES.get(d, f"depot_{d}") for d in page["offered"]}
    return {
        "offered": {name[d]: d for d in page["offered"]},
        "selected": sorted(name[d] for d in page["checked"]),
        "none_needed": page["none_needed"],
    }


def desired(values: dict[str, Any], app: str, current: dict[str, Any]) -> dict[str, Any]:
    """What steamworks.yaml asks for; an empty list leaves Steamworks as it is."""
    wanted = list(fp.get(values, f"apps.{app}.installation.redistributables") or [])
    if not wanted:
        return {"selected": current["selected"], "none_needed": current["none_needed"], "problems": []}
    problems = [
        f"{n}: deprecated in Steamworks, use {DEPRECATED[n]}"
        for n in wanted
        if n in DEPRECATED and n not in current["selected"]
    ]
    problems += [
        f"{n}: not offered on the Redistributables page" for n in wanted if n != "none" and n not in current["offered"]
    ]
    bad = {p.split(":")[0] for p in problems}
    return {
        "selected": sorted(n for n in wanted if n != "none" and n not in bad),
        "none_needed": wanted == ["none"],
        "problems": problems,
    }


def plan(appid: int, want: dict[str, Any], current: dict[str, Any], force: bool = False) -> list[sync.Op]:
    ops = [sync.Op(SECTION, "skip", p, None, "not written", sync._nothing) for p in want.get("problems", [])]
    before = {"selected": current["selected"], "none_needed": current["none_needed"]}
    after = {"selected": want["selected"], "none_needed": want["none_needed"]}
    if force or after != before:
        depots = {d: n in after["selected"] for n, d in current["offered"].items()}

        async def save(t: Transport, d: dict[int, bool] = depots, none: bool = after["none_needed"]) -> None:
            await P.set_common_redists(t, appid, d, none)

        ops.append(sync.Op(SECTION, "set", "common redistributables", before, after, save))
    return ops
