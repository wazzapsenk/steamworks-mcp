"""Project scanners. Built in: Unity, and the optional Steam Build Pipeline plugin. Others (Godot, Unreal, …) can
register through the ``steamworks_mcp.scanners`` entry-point group."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

from steamworks_mcp.scanners.base import Scanner, ScanResult, ScanWarning
from steamworks_mcp.scanners.steam_build_pipeline import SteamBuildPipelineScanner
from steamworks_mcp.scanners.unity import UnityScanner


def all_scanners() -> list[Scanner]:
    scanners: list[Scanner] = [UnityScanner(), SteamBuildPipelineScanner()]
    for ep in entry_points(group="steamworks_mcp.scanners"):
        try:
            obj = ep.load()
            scanners.append(obj() if isinstance(obj, type) else obj)
        except Exception:  # a broken third-party plugin must not break scanning
            continue
    return scanners


def run_scanners(root: Path) -> list[ScanResult]:
    """Run every scanner that recognises the project. A failing scanner becomes a warning, not an error."""
    results = []
    for s in all_scanners():
        try:
            if not s.detect(root):
                continue
            results.append(s.scan(root))
        except Exception as exc:
            results.append(ScanResult(s.name, warnings=[ScanWarning("scanner_failed", f"{s.name}: {exc}")]))
    return results
