"""Field operations behind set_field / approve_fields / mark_applied."""

from __future__ import annotations

from typing import Any

from steamworks_mcp.interview.questions import coerce, unit_ancestor
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import ManifestError, load_drafts, save_draft
from steamworks_mcp.manifest.state import Source, TransitionError, is_empty
from steamworks_mcp.project import Project, tracked_under
from steamworks_mcp.validate.store_text import TARGET_FIELDS, check_store_text

STORE_TEXT_FIELDS = set(TARGET_FIELDS.values())


def _store_findings(project: Project, changes: dict[str, Any]) -> list[dict[str, Any]]:
    texts = {k: v for k, v in changes.items() if k in STORE_TEXT_FIELDS and isinstance(v, str)}
    if not texts:
        return []
    lang = str(project.values().get("source_language") or "english")
    return [f.__dict__ for f in check_store_text({lang: texts})]


def set_fields(
    project: Project,
    changes: dict[str, Any],
    source: Source = "user",
    *,
    confidence: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Write values and record their state: the user's answers are approved, other sources are drafts.

    The values are applied together when they validate together; otherwise each one is tried on its own, and the
    ones that still fail are reported under ``not_saved`` while the others are kept.
    """
    if not changes:
        raise ValueError("Nothing to set: pass `field` + `value` or `values`.")
    for path in changes:
        if not fp.is_valid(path):
            raise fp.FieldPathError(f'"{path}" is not a field of steamworks.yaml (see get_spec_info("schema")).')
    coerced = {path: coerce(path, value) for path, value in changes.items()}
    findings = _store_findings(project, coerced)
    errors = [f for f in findings if f["severity"] == "error"]
    if errors and source != "user":
        raise ValueError(
            "Rejected, breaks Valve's store rules: "
            + "; ".join(f"[{e['rule_id']}] {e['field']}: {e['message']}" for e in errors)
        )
    errors_by_field: dict[str, str] = {}
    try:
        project.manifest.set_many(list(coerced.items()))
    except ManifestError:
        if len(coerced) == 1:
            raise
        # Answers are independent: keep the valid ones, report the rest.
        for path, value in list(coerced.items()):
            try:
                project.manifest.set(path, value)
            except ManifestError as exc:
                errors_by_field[path] = str(exc)
                del coerced[path]
        if not coerced:
            raise ManifestError("; ".join(errors_by_field.values())) from None
    values = project.values()
    recorded: dict[str, str] = {}
    for path in coerced:
        tracked = unit_ancestor(path) or path
        for p, v in tracked_under(project, tracked, fp.get(values, tracked)):
            fs = project.state.record_value(p, v, source, confidence=confidence, notes=notes)
            recorded[p] = fs.status
    out: dict[str, Any] = {"set": sorted(coerced), "status": recorded}
    if errors_by_field:
        out["not_saved"] = errors_by_field
    if findings:
        out["store_rule_findings"] = findings
    return out


def set_from_draft(project: Project, field: str, draft_id: str) -> dict[str, Any]:
    """The user picked a draft: write its value and approve it."""
    drafts = load_drafts(project.files, field)
    draft = next((d for d in drafts if d.id == draft_id), None)
    if draft is None:
        raise ValueError(f'No draft "{draft_id}" for {field}. Drafts: {", ".join(d.id for d in drafts) or "none"}.')
    out = set_fields(project, {field: draft.value}, draft.source)
    project.state.approve(field, fp.get(project.values(), field))
    out["status"][field] = "approved"
    for d in drafts:
        new_status = "chosen" if d.id == draft_id else ("rejected" if d.status == "chosen" else d.status)
        if new_status != d.status:
            save_draft(project.files, d.model_copy(update={"status": new_status}))
    return out


def _expand(project: Project, patterns: list[str]) -> list[str]:
    values = project.values()
    fields = [p for p, _ in fp.iter_fields(values)]
    out: list[str] = []
    for pat in patterns:
        if pat.startswith("checklist."):
            out.append(pat)
            continue
        matched = (
            [p for p in fields if fp.matches(pat, p) or p.startswith(pat + ".")]
            if "*" in pat or pat not in fields
            else [pat]
        )
        if not matched:
            raise fp.FieldPathError(f'"{pat}" matches no field')
        out += [m for m in matched if m not in out]
    return out


def approve_fields(project: Project, patterns: list[str]) -> dict[str, Any]:
    values = project.values()
    approved, skipped = [], {}
    for path in _expand(project, patterns):
        value = fp.get(values, path)
        if is_empty(value):
            skipped[path] = "empty: nothing to approve"
            continue
        project.state.approve(path, value)
        approved.append(path)
    return {"approved": approved, "skipped": skipped}


def mark_applied(project: Project, patterns: list[str], notes: str = "") -> dict[str, Any]:
    """The user confirms manual steps are done in Steamworks (``checklist.<rule id>``) or values are entered there."""
    values = project.values()
    applied, refused = [], {}
    for path in _expand(project, patterns):
        try:
            if path.startswith("checklist."):
                project.state.confirm_checklist(path, notes)
            else:
                project.state.mark_applied(path, fp.get(values, path))
            applied.append(path)
        except TransitionError as exc:
            refused[path] = str(exc)
    return {"applied": applied, "refused": refused}
