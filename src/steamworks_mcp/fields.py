"""Field operations behind set_field / approve_fields / mark_applied."""

from __future__ import annotations

import difflib
import re
from typing import Any

from PIL import Image

from steamworks_mcp.interview.questions import (
    CORE_INPUTS,
    DESCRIBE,
    PREREQUISITES,
    coerce,
    prerequisite_answers,
    unit_ancestor,
)
from steamworks_mcp.localization.store import Localization, accept_sources
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import ManifestError, load_drafts, save_draft
from steamworks_mcp.manifest.state import Source, TransitionError, is_empty
from steamworks_mcp.project import Project, tracked_under
from steamworks_mcp.validate.store_text import TARGET_FIELDS, check_store_text

STORE_TEXT_FIELDS = set(TARGET_FIELDS.values())
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
WEB_ADDRESS = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
URL_FIELDS = re.compile(r"^store\.(support\.url|links\.(website|privacy_policy|online_manual|forums|social\.\w+))$")
IMAGE_FIELDS = re.compile(r"^assets\.(key_art|logo|overrides\.\w+)$")
TAGS_MIN, TAGS_MAX = 5, 20
"""Steam's user tags: at least 5 for the store page, 20 at most."""


class AnswerError(ValueError):
    """A value steamworks.yaml does not accept (the file itself is fine)."""


def check_answers(
    project: Project, values: dict[str, Any], known_tags: set[str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Answers the schema accepts but Steam would not: ``(refused, warnings)``. Refused values are clearly wrong (not
    an e-mail address, not an image); warnings are saved and say what is still missing. A web address without
    ``https://`` gets it added in ``values``. ``known_tags``: Steam's tag names in lower case, when cached."""
    refused: dict[str, str] = {}
    warnings: list[str] = []
    for path, value in list(values.items()):
        if is_empty(value):
            continue
        if path == "store.support.email" and not EMAIL.match(str(value)):
            refused[path] = f"'{value}' is not an e-mail address."
        elif URL_FIELDS.match(path) and isinstance(value, str):
            if "://" not in value and " " not in value.strip() and "." in value:
                values[path] = value = "https://" + value.strip()
            if not WEB_ADDRESS.match(value):
                refused[path] = f"'{value}' is not a web address (like https://example.com)."
        elif IMAGE_FIELDS.match(path):
            file = project.files.root / str(value)
            if not file.is_file():
                warnings.append(f"{path}: {value} is not in the game folder yet; put it there before making images.")
            else:
                try:
                    with Image.open(file) as image:
                        image.verify()
                except (OSError, ValueError):
                    refused[path] = f"{value} is not an image file (PNG or JPG)."
        elif path == "store.tags" and isinstance(value, list):
            if len(value) > TAGS_MAX:
                refused[path] = f"Steam allows {TAGS_MAX} tags at most; {len(value)} given."
                continue
            if len(value) < TAGS_MIN:
                warnings.append(f"store.tags: Steam needs at least {TAGS_MIN} tags; {len(value)} given, add more.")
            for tag in [t for t in value if known_tags and str(t).lower() not in known_tags]:
                close = difflib.get_close_matches(str(tag).lower(), sorted(known_tags or ()), n=1)
                hint = f" (did you mean '{close[0]}'?)" if close else ""
                warnings.append(f"store.tags: '{tag}' is not one of Steam's tags{hint}.")
    return refused, warnings


def _store_findings(project: Project, changes: dict[str, Any]) -> list[dict[str, Any]]:
    texts = {k: v for k, v in changes.items() if k in STORE_TEXT_FIELDS and isinstance(v, str)}
    if not texts:
        return []
    lang = str(project.values().get("source_language") or "english")
    return [f.__dict__ for f in check_store_text({lang: texts}, english_fallback=False)]


def set_fields(
    project: Project,
    changes: dict[str, Any],
    source: Source = "user",
    *,
    confidence: float | None = None,
    notes: str | None = None,
    known_tags: set[str] | None = None,
) -> dict[str, Any]:
    """Write values and record their state: the user's answers are approved, other sources are drafts.

    The values are applied together when they validate together; otherwise each one is tried on its own, and the
    ones that still fail are reported under ``not_saved`` while the others are kept. Answers Steam would not take
    (see :func:`check_answers`) are refused or saved with ``warnings``; the user's own store text that breaks a Valve
    rule is saved as needs_review.
    """
    if not changes:
        raise ValueError("Nothing to set: pass `field` + `value` or `values`.")
    changes = dict(changes)
    if DESCRIBE in changes:
        raise AnswerError(
            f"{DESCRIBE} is not saved as it is. Fill the game inputs you can from it ("
            + ", ".join(CORE_INPUTS)
            + ") with set_field(values=..., source='generated'), then call start_interview so the user confirms them."
        )
    if PREREQUISITES in changes:
        try:
            done = prerequisite_answers(changes.pop(PREREQUISITES))
        except ValueError as exc:
            raise AnswerError(str(exc)) from exc
        now = project.values()  # steps answered before keep their answer unless named now
        changes = {**{p: v for p, v in done.items() if v or fp.get(now, p) is None}, **changes}
    for path in changes:
        if not fp.is_valid(path):
            raise fp.FieldPathError(f'"{path}" is not a field of steamworks.yaml (see get_spec_info("schema")).')
    before = project.values()
    coerced = {path: coerce(path, value, fp.get(before, path)) for path, value in changes.items()}
    if isinstance(coerced.get("target_languages"), list):  # the source language is never a translation target
        written_in = coerced.get("source_language") or before.get("source_language")
        coerced["target_languages"] = [lang for lang in coerced["target_languages"] if lang != written_in]
    findings = _store_findings(project, coerced)
    errors = [f for f in findings if f["severity"] == "error"]
    if errors and source not in ("user", "steamworks"):  # what Steam already has is imported as it is
        raise ValueError(
            "Rejected, breaks Valve's store rules: "
            + "; ".join(f"[{e['rule_id']}] {e['field']}: {e['message']}" for e in errors)
        )
    errors_by_field: dict[str, str] = {}
    warnings: list[str] = []
    if source in ("user", "generated"):  # what Steam or the scan found is kept as it is
        errors_by_field, warnings = check_answers(project, coerced, known_tags)
        for path in errors_by_field:
            del coerced[path]
        if not coerced:
            raise AnswerError("Not saved: " + " ".join(errors_by_field.values()))
    try:
        project.manifest.set_many(list(coerced.items()))
    except ManifestError as exc:
        if len(coerced) == 1 and not errors_by_field:
            raise AnswerError(f"Not saved: {exc}") from exc
        # Answers are independent: keep the valid ones, report the rest.
        for path, value in list(coerced.items()):
            try:
                project.manifest.set(path, value)
            except ManifestError as one:
                errors_by_field[path] = str(one)
                del coerced[path]
        if not coerced:
            raise AnswerError("Not saved: " + "; ".join(errors_by_field.values())) from None
    values = project.values()
    recorded: dict[str, str] = {}
    for path in coerced:
        tracked = unit_ancestor(path) or path
        for p, v in tracked_under(project, tracked, fp.get(values, tracked)):
            fs = project.state.record_value(p, v, source, confidence=confidence, notes=notes)
            recorded[p] = fs.status
    for path in {e["field"] for e in errors if source == "user"} & set(recorded):
        # the user's own text that breaks a Valve rule is kept, but not as approved: they decide after seeing why
        project.state.fields[path].status = recorded[path] = "needs_review"
    out: dict[str, Any] = {"set": sorted(coerced), "status": recorded}
    if errors_by_field:
        out["not_saved"] = errors_by_field
    if warnings:
        out["warnings"] = warnings
    if findings:
        out["store_rule_findings"] = findings
    return out


def set_from_draft(project: Project, field: str, draft_id: str) -> dict[str, Any]:
    """The user picked a draft: write its value and approve it."""
    drafts = load_drafts(project.files, field)
    draft = next((d for d in drafts if d.id == draft_id), None)
    if draft is None:
        raise ValueError(f'No draft "{draft_id}" for {field}. Drafts: {", ".join(d.id for d in drafts) or "none"}.')
    if draft.strategy == "outline":
        # An approved outline is a plan for the text, not the text: mark it chosen, write nothing.
        for d in drafts:
            if d.strategy == "outline":
                new = "chosen" if d.id == draft_id else ("rejected" if d.status == "chosen" else d.status)
                if new != d.status:
                    save_draft(project.files, d.model_copy(update={"status": new}))
        return {"outline_approved": draft_id, "next": "generate(section='store_long', stage='text')"}
    out = set_fields(project, {field: draft.value}, draft.source)
    project.state.approve(field, fp.get(project.values(), field))
    out["status"][field] = "approved"
    for d in drafts:
        new_status = "chosen" if d.id == draft_id else ("rejected" if d.status == "chosen" else d.status)
        if new_status != d.status:
            save_draft(project.files, d.model_copy(update={"status": new_status}))
    return out


def _translations(project: Project) -> dict[str, str]:
    """``localization.<lang>.<key>`` -> translated text, for every target language."""
    loc = Localization(project.files.root)
    return {
        f"localization.{lang}.{key}": text
        for lang in project.values().get("target_languages") or []
        for key, text in loc.read(lang).items()
    }


def _value(project: Project, path: str, translations: dict[str, str]) -> Any:
    return translations.get(path) if path.startswith("localization.") else fp.get(project.values(), path)


def _expand(project: Project, patterns: list[str]) -> list[str]:
    values = project.values()
    fields = [p for p, _ in fp.iter_fields(values)]
    out: list[str] = []
    for pat in patterns:
        if pat.startswith("checklist."):
            out.append(pat)
            continue
        if pat.startswith("localization."):
            keys = list(_translations(project))
            matched = [k for k in keys if k == pat or k.startswith(pat.rstrip("*").rstrip(".") + ".")]
            if not matched:
                raise fp.FieldPathError(f'"{pat}" matches no translation')
            out += [m for m in matched if m not in out]
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
    translations = _translations(project)
    approved, skipped = [], {}
    for path in _expand(project, patterns):
        value = _value(project, path, translations)
        if is_empty(value):
            skipped[path] = "empty: nothing to approve"
            continue
        project.state.approve(path, value)
        approved.append(path)
    if translations_ok := [p for p in approved if p.startswith("localization.")]:
        # Approving a translation whose source changed means it still fits: it is no longer stale.
        accept_sources(project.values(), project.files.root, translations_ok)
    return {"approved": approved, "skipped": skipped}


def mark_applied(project: Project, patterns: list[str], notes: str = "") -> dict[str, Any]:
    """The user confirms manual steps are done in Steamworks (``checklist.<rule id>``) or values are entered there."""
    translations = _translations(project)
    applied, refused = [], {}
    for path in _expand(project, patterns):
        try:
            if path.startswith("checklist."):
                project.state.confirm_checklist(path, notes)
            else:
                project.state.mark_applied(path, _value(project, path, translations))
            applied.append(path)
        except TransitionError as exc:
            refused[path] = str(exc)
    return {"applied": applied, "refused": refused}
