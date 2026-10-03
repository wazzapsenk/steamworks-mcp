"""Generators that need no writing: they derive drafts from the scan and the values already present.

Everything is written as a draft (``source: generated``) through the same rules as scans: values the user approved
are never overwritten; differences are reported as conflicts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.state import Evidence, is_empty
from steamworks_mcp.scanners.base import Finding
from steamworks_mcp.validate.crosschecks import scan_facts

DEFAULT_BYTE_QUOTA = 100_000_000
DEFAULT_FILE_QUOTA = 100
OS_FOLDER = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}

# Minimum requirements of the engines' players (a starting point; always reviewed by the user).
UNITY_MINIMUM = {
    "windows": {
        "os": "Windows 10 64-bit",
        "processor": "x64 CPU with SSE2",
        "graphics": "DirectX 11 compatible GPU",
        "directx": "Version 11",
    },
    "macos": {"os": "macOS 12", "processor": "Apple Silicon or x64 with SSE2", "graphics": "Metal capable GPU"},
    "linux": {"os": "Ubuntu 22.04 64-bit", "processor": "x64 CPU with SSE2", "graphics": "Vulkan capable GPU"},
}


def _get(values: dict[str, Any], path: str) -> Any:
    return fp.get(values, path)


def cloud(values: dict[str, Any], root: Path) -> list[Finding]:
    """Quotas and Auto-Cloud paths from the scanned save system."""
    facts = scan_facts(root)
    out: list[Finding] = []
    note = "Starting point; raise it if saves are larger or more numerous."
    if is_empty(_get(values, "apps.main.cloud.byte_quota")):
        out.append(Finding("apps.main.cloud.byte_quota", DEFAULT_BYTE_QUOTA, 0.5, note=note))
    if is_empty(_get(values, "apps.main.cloud.file_quota")):
        out.append(Finding("apps.main.cloud.file_quota", DEFAULT_FILE_QUOTA, 0.5, note=note))
    if facts.get("save_paths") and _get(values, "apps.main.cloud.enabled") is None:
        out.append(Finding("apps.main.cloud.enabled", True, 0.6, note="The game writes save files."))
    return out


def builds(values: dict[str, Any], root: Path) -> list[Finding]:
    """One depot per supported OS when none are defined (content_root = the usual build folder)."""
    if _get(values, "apps.main.builds.depots"):
        return []
    platforms = _get(values, "store.platforms") or []
    if not platforms:
        return []
    depots = [
        {"name": p, "os": p, "arch": "64", "content_root": f"Builds/{OS_FOLDER[p]}", "exclude": ["*.pdb"]}
        for p in platforms
    ]
    return [
        Finding(
            "apps.main.builds.depots", depots, 0.5, note="Fill in each depot_id from Steamworks > SteamPipe > Depots."
        )
    ]


def _folder_size_gb(folder: Path) -> float | None:
    if not folder.is_dir():
        return None
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    return total / 1_000_000_000


def requirements(values: dict[str, Any], root: Path) -> list[Finding]:
    """Minimum system requirements drafted from the engine and the build size. Always to be reviewed."""
    engine = (_get(values, "game.engine.name") or "").lower()
    out: list[Finding] = []
    for os_name in _get(values, "store.platforms") or []:
        path = f"store.system_requirements.{os_name}.minimum"
        if not is_empty(_get(values, path)):
            continue
        base = dict(UNITY_MINIMUM.get(os_name, {})) if engine == "unity" else {}
        depot = next(
            (d for d in _get(values, "apps.main.builds.depots") or [] if d.get("os") in (os_name, "all")), None
        )
        size = _folder_size_gb(root / depot["content_root"]) if depot and depot.get("content_root") else None
        if size is not None:
            base["storage"] = f"{max(1, round(size * 1.2 + 0.5))} GB available space"
        if base:
            base["memory"] = base.get("memory", "8 GB RAM")
            out.append(
                Finding(path, base, 0.3, note="Drafted from the engine's own minimums; test on real low-end hardware.")
            )
    return out


def code_definitions(values: dict[str, Any], root: Path) -> tuple[list[Finding], dict[str, list[str]]]:
    """Stats and leaderboards used in code; achievements in code vs in steamworks.yaml."""
    facts = scan_facts(root)
    out: list[Finding] = []
    ev = [Evidence(file=".steam-mcp/scan/unity.json", note="from the last scan")]
    for name in facts.get("code_stats") or []:
        if _get(values, f"stats.{name}") is None:
            out.append(Finding(f"stats.{name}", {"name": name}, 0.8, ev, kind="item"))
    for name in facts.get("code_leaderboards") or []:
        if _get(values, f"leaderboards.{name}") is None:
            out.append(Finding(f"leaderboards.{name}", {"name": name}, 0.8, ev, kind="item"))
    for name in facts.get("code_achievements") or []:
        if _get(values, f"achievements.{name}") is None:
            out.append(Finding(f"achievements.{name}", {"id": name}, 0.9, ev, kind="item"))
    for ach, max_value in (facts.get("achievement_progress_max") or {}).items():
        stats = list(facts.get("code_stats") or [])
        if len(stats) == 1 and _get(values, f"achievements.{ach}.progress") is None:
            out.append(Finding(f"achievements.{ach}.progress", {"stat": stats[0], "min": 0, "max": max_value}, 0.4))
    in_code = set(facts.get("code_achievements") or [])
    defined = {a["id"] for a in values.get("achievements") or []}
    report = {
        "in_code_not_defined": sorted(in_code - defined),
        "defined_not_in_code": sorted(defined - in_code) if facts else [],
    }
    return out, report
