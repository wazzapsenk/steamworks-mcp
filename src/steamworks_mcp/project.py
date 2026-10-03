"""Project-level operations behind the tools: open/save a game project, ``init_project``, merging scan results."""

from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import (
    ROOT_GITIGNORE_SUGGESTIONS,
    STATE_GITIGNORE,
    ManifestError,
    ManifestFile,
    ProjectFiles,
    atomic_write,
    load_state,
    new_manifest_text,
    save_state,
)
from steamworks_mcp.manifest.state import Evidence, State, Status, is_empty
from steamworks_mcp.scanners.base import Finding, ScanResult

USER_OWNED: set[Status] = {"approved", "applied", "needs_review"}
"""Statuses whose value belongs to the user: scans and generators report conflicts instead of overwriting."""

ITEM_FIELD = {"achievements": "name", "stats": "display_name", "leaderboards": "display_name"}
"""Field of a keyed-list item that carries scan evidence for the item itself."""


@dataclass
class Project:
    files: ProjectFiles
    manifest: ManifestFile
    state: State

    @classmethod
    def open(cls, root: Path) -> Project:
        files = ProjectFiles(root)
        manifest = ManifestFile.load(files.manifest)
        state = load_state(files)
        state.reconcile(manifest.values())
        return cls(files, manifest, state)

    def values(self) -> dict[str, Any]:
        return self.manifest.values()

    def save(self) -> None:
        self.manifest.save()
        save_state(self.files, self.state)


# ----------------------------------------------------------------------------------------------- init


def init_project(root: Path, name: str | None = None, appid: int | None = None) -> dict[str, Any]:
    """Create ``steamworks.yaml`` and ``.steam-mcp/`` (never overwrites). Returns what was created."""
    files = ProjectFiles(root)
    created, existing = [], []
    state_existed = files.state_file.exists()
    if files.manifest.exists():
        existing.append(files.manifest.name)
    else:
        atomic_write(files.manifest, new_manifest_text(name, appid))
        created.append(files.manifest.name)
    if not files.state_gitignore.exists():
        atomic_write(files.state_gitignore, STATE_GITIGNORE)
        created.append(".steam-mcp/.gitignore")
    project = Project.open(root)
    if files.manifest.name in created:
        for path, value in (("game.name", name), ("apps.main.appid", appid)):
            if value is not None:
                project.state.record_value(path, value, "user", notes="given to init_project")
    project.save()
    (existing if state_existed else created).append(".steam-mcp/state.json")
    return {"created": created, "existing": existing, "gitignore_suggestions": gitignore_suggestions(root)}


def gitignore_suggestions(root: Path) -> list[str]:
    """Lines the project's own .gitignore should have. Suggested only; the server never edits that file."""
    gi = root / ".gitignore"
    if gi.exists():
        lines = {line.strip() for line in gi.read_text(encoding="utf-8", errors="replace").splitlines()}
        return [line for line in ROOT_GITIGNORE_SUGGESTIONS if line not in lines]
    return list(ROOT_GITIGNORE_SUGGESTIONS) if (root / ".git").exists() else []


# ----------------------------------------------------------------------------------------------- scan merge


@dataclass
class MergeReport:
    applied: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    scanners: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _combine(findings: list[Finding]) -> list[Finding]:
    """One finding per field: the most confident value, with the evidence of every finding that agrees with it."""
    by_field: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        by_field.setdefault((f.field, f.kind), []).append(f)
    out = []
    for group in by_field.values():
        best = max(group, key=lambda f: f.confidence)
        evidence = [e for f in group if f.value == best.value for e in f.evidence]
        out.append(Finding(best.field, best.value, best.confidence, evidence[:20], best.kind, best.note))
    return out


def _item_leaf(path: str) -> str:
    head = fp.split(path)[0]
    return f"{path}.{ITEM_FIELD.get(head, 'name')}"


def merge_scan(project: Project, results: list[ScanResult]) -> MergeReport:
    report = MergeReport(scanners=[r.scanner for r in results])
    scanned_at = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    for r in results:
        atomic_write(
            project.files.scan_dir / f"{r.scanner}.json",
            json.dumps(
                {
                    "scanned_at": scanned_at,
                    "findings": [asdict(f) for f in r.findings],
                    "warnings": [asdict(w) for w in r.warnings],
                    "facts": r.facts,
                },
                indent=2,
                ensure_ascii=False,
                default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o),
            )
            + "\n",
        )
        report.warnings += [
            {
                "scanner": r.scanner,
                "code": w.code,
                "message": w.message,
                "evidence": [e.model_dump() for e in w.evidence],
            }
            for w in r.warnings
        ]

    for f in _combine([f for r in results for f in r.findings]):
        try:
            _merge_one(project, f, report)
        except (ManifestError, fp.FieldPathError) as exc:
            report.rejected.append({"field": f.field, "value": f.value, "reason": str(exc)})
    return report


def _normalized(project: Project, path: str, value: Any) -> Any:
    """``value`` as it would be stored at ``path`` (defaults filled in), to compare with the current value."""
    probe = ManifestFile(project.manifest.path, copy.deepcopy(project.manifest.raw), project.manifest.manifest)
    probe.set(path, value)
    return fp.get(probe.values(), path)


def _merge_one(project: Project, f: Finding, report: MergeReport) -> None:
    values = project.values()
    if f.kind == "item":
        leaf = _item_leaf(f.field)
        if fp.get(values, f.field) is None:
            project.manifest.set(f.field, f.value)
            report.applied.append(
                {"field": f.field, "value": f.value, "confidence": f.confidence, "note": f.note or "added from code"}
            )
        if project.state.get(leaf).status == "missing":
            project.state.record_value(
                leaf, None, "scan", confidence=f.confidence, evidence=f.evidence, notes="seen in code"
            )
        return
    current = fp.get(values, f.field)
    scanned = _normalized(project, f.field, f.value)
    fs = project.state.fields.get(f.field)
    if fs is not None and fs.status in USER_OWNED:
        if current == scanned:
            _add_evidence(project, f.field, f.evidence)
            report.unchanged.append(f.field)
        else:
            report.conflicts.append(
                {
                    "field": f.field,
                    "current": current,
                    "scanned": f.value,
                    "status": fs.status,
                    "evidence": [e.model_dump() for e in f.evidence[:5]],
                    "note": f.note,
                }
            )
        return
    if current == scanned and (fs is not None or any(p.startswith(f.field + ".") for p in project.state.fields)):
        _add_evidence(project, f.field, f.evidence)
        report.unchanged.append(f.field)
        return
    project.manifest.set(f.field, f.value)
    stored = fp.get(project.values(), f.field)
    for path, value in tracked_under(project, f.field, stored):
        project.state.record_value(
            path, value, "scan", confidence=f.confidence, evidence=f.evidence, notes=f.note or None
        )
    report.applied.append({"field": f.field, "value": f.value, "confidence": f.confidence, "note": f.note})


def tracked_under(project: Project, path: str, stored: Any) -> list[tuple[str, Any]]:
    """Tracked fields at or below ``path`` (a list of objects is tracked per item field)."""
    fields = [(p, v) for p, v in fp.iter_fields(project.values()) if p == path or p.startswith(path + ".")]
    return fields or [(path, stored)]


def _add_evidence(project: Project, path: str, evidence: list[Evidence]) -> None:
    fs = project.state.fields.get(path)
    if fs is None or not evidence:
        return
    known = {(e.file, e.line) for e in fs.evidence}
    fs.evidence = fs.evidence + [e for e in evidence if (e.file, e.line) not in known][:20]


def describe_values(values: dict[str, Any]) -> int:
    """Number of tracked fields that have a value (for summaries)."""
    return sum(1 for _, v in fp.iter_fields(values) if not is_empty(v))
