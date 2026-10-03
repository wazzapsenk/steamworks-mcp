"""``status``: where a game stands on its way to Steam, or which games the workspace holds. The place to start."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from steamworks_mcp.gates.report import gap_report
from steamworks_mcp.localization.store import report as localization_report
from steamworks_mcp.project import Project

MANIFEST = "steamworks.yaml"
SKIP_DIRS = {
    ".git",
    ".steam-mcp",
    "node_modules",
    "Library",
    "Temp",
    "Logs",
    "obj",
    "bin",
    "Build",
    "Builds",
    "Intermediate",
    "Saved",
    "DerivedDataCache",
    ".godot",
    ".import",
    ".venv",
}
MAX_DEPTH = 3
COUNTED = ("pass", "done", "fail", "warn", "review", "todo", "unknown")


def engine_of(folder: Path) -> str | None:
    if (folder / "ProjectSettings" / "ProjectVersion.txt").is_file():
        return "Unity"
    if (folder / "project.godot").is_file():
        return "Godot"
    if any(folder.glob("*.uproject")):
        return "Unreal"
    return None


def _walk(root: Path, depth: int = 0) -> list[Path]:
    out = [root]
    if depth >= MAX_DEPTH or (root / MANIFEST).is_file() or engine_of(root):
        return out  # a game's own subfolders are not other games
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS)
    except OSError:
        return out
    for child in children:
        out += _walk(child, depth + 1)
    return out


def workspace(root: Path) -> dict[str, Any]:
    """The games under the workspace root: tracked ones (with steamworks.yaml) and engine projects not tracked yet."""
    tracked, untracked = [], []
    for folder in _walk(root):
        rel = folder.relative_to(root).as_posix() if folder != root else "."
        if (folder / MANIFEST).is_file():
            name = None
            try:
                name = Project.open(folder).values().get("game", {}).get("name")
            except Exception:  # a broken manifest still counts as a tracked game
                name = None
            tracked.append({"path": rel, "name": name, "engine": engine_of(folder)})
        elif engine := engine_of(folder):
            untracked.append({"path": rel, "engine": engine})
    return {"workspace_root": str(root), "games": tracked, "not_tracked_yet": untracked}


def project(project: Project, *, browser: bool) -> dict[str, Any]:
    """Progress per release step, the values' statuses, translations, and the one thing to do next."""
    values = project.values()
    report = gap_report(values, project.state, project.files.root, browser=browser)
    steps = []
    for g in report["gates"]:
        counts = {k: v for k, v in g["counts"].items() if k in COUNTED}
        total = sum(counts.values())
        done = counts.get("pass", 0) + counts.get("done", 0)
        steps.append(
            {
                "gate": g["gate"],
                "title": g["title"],
                "ready": g["ready"],
                "open": len(g["blocking"]),
                "done": done,
                "total": total,
            }
        )
    statuses = Counter(fs.status for fs in project.state.fields.values())
    loc = localization_report(values, project.files.root)
    store = values.get("store") or {}
    return {
        "game": (values.get("game") or {}).get("name"),
        "path": project.files.root.name,
        "steps": steps,
        "values": {
            "to_fill": report["fields_to_fill"],
            "drafts": statuses.get("draft", 0),
            "changed_after_approval": statuses.get("needs_review", 0),
            "approved": statuses.get("approved", 0),
            "applied": statuses.get("applied", 0),
        },
        "store_text": {
            "short_description": bool(store.get("short_description")),
            "about": bool(store.get("about")),
        },
        "translations": {
            "languages": [entry["language"] for entry in loc["languages"]],
            "to_translate": loc["to_translate"],
        },
        "next": report["next"][:1],
    }
