"""SteamPipe build scripts: ``app_build_<appid>.vdf`` and one ``depot_build_<depotid>.vdf`` per depot.

Paths in the scripts are relative to the folder the scripts are written to. The default branch is never set live
from a script (Valve: that is done by hand in App Admin); a beta branch can be, when ``builds.set_live_on`` says so.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rel(target: Path, base: Path) -> str:
    return os.path.relpath(target, base).replace("\\", "/")


def app_build(
    appid: int, depots: list[dict[str, Any]], script_dir: Path, root: Path, set_live: str | None, desc: str
) -> str:
    live = set_live if set_live and set_live != "default" else ""
    lines = [
        '"AppBuild"',
        "{",
        f'\t"AppID" {_q(str(appid))}',
        f'\t"Desc" {_q(desc)}',
        f'\t"BuildOutput" {_q(_rel(root / ".steam-mcp" / "exports" / "build_output", script_dir) + "/")}',
        f'\t"SetLive" {_q(live)}',
        '\t"Depots"',
        "\t{",
    ]
    for d in depots:
        depot_id = str(d["depot_id"])
        lines.append(f"\t\t{_q(depot_id)} {_q('depot_build_' + depot_id + '.vdf')}")
    lines += ["\t}", "}", ""]
    return "\n".join(lines)


def depot_build(depot: dict[str, Any], script_dir: Path, root: Path) -> str:
    content = _rel(root / str(depot.get("content_root") or "Builds"), script_dir)
    lines = [
        '"DepotBuild"',
        "{",
        f'\t"DepotID" {_q(str(depot["depot_id"]))}',
        f'\t"ContentRoot" {_q(content + "/")}',
        '\t"FileMapping"',
        "\t{",
        '\t\t"LocalPath" "*"',
        '\t\t"DepotPath" "."',
        '\t\t"Recursive" "1"',
        "\t}",
    ]
    lines += [f'\t"FileExclusion" {_q(x)}' for x in depot.get("exclude") or []]
    lines += ["}", ""]
    return "\n".join(lines)


def build_scripts(values: dict[str, Any], root: Path, script_dir: Path, app: str = "main") -> dict[str, str] | str:
    """``{file name: content}`` for one app profile, or why the scripts cannot be written yet."""
    profile = (values.get("apps") or {}).get(app) or {}
    appid = profile.get("appid")
    builds = profile.get("builds") or {}
    depots = [d for d in builds.get("depots") or [] if d.get("depot_id")]
    if not appid:
        return f"apps.{app}.appid is not set"
    if not depots:
        return f"apps.{app}.builds.depots has no depot with a depot_id"
    name = (values.get("game") or {}).get("name") or "build"
    files = {
        f"app_build_{appid}.vdf": app_build(
            appid, depots, script_dir, root, builds.get("set_live_on"), f"{name} ({app})"
        )
    }
    for d in depots:
        files[f"depot_build_{d['depot_id']}.vdf"] = depot_build(d, script_dir, root)
    return files


def steamcmd_command(appid: int, steamcmd: str = "steamcmd") -> str:
    """The upload command; the builder account's name and password are never written into files."""
    return f'{steamcmd} +login <builder account> +run_app_build "app_build_{appid}.vdf" +quit'
