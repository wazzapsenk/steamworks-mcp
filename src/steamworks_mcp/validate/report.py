"""``validate``: Valve rules, the rubric and the crosschecks over what is in steamworks.yaml (and translations).

Review mode never changes a value. A better version is saved as a new draft (``strategy="revision"``) for the user
to compare and pick.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from steamworks_mcp.data import load_yaml
from steamworks_mcp.gates.engine import evaluate_gates
from steamworks_mcp.generate.deterministic import code_definitions
from steamworks_mcp.generate.text import guide_for
from steamworks_mcp.localization.store import Localization, status
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.state import State
from steamworks_mcp.validate import rubric
from steamworks_mcp.validate.store_text import check_store_text

LANGUAGE_INDEPENDENT = {
    "long_paragraph_length",
    "long_feature_list",
    "long_media_count",
    "long_first_media_early",
    "long_placeholders_left",
}
FIELDS = {"short": "store.short_description", "long": "store.about"}


def _banned_phrases(lang: str) -> list[str]:
    try:
        data = load_yaml(f"style_guides/_banned_phrases/{lang}.yaml")
    except (FileNotFoundError, OSError):
        return []
    return [str(p) for p in (data or {}).get("phrases", [])]


def store_texts(values: dict[str, Any], root: Path) -> dict[str, dict[str, str]]:
    lang = str(values.get("source_language") or "english")
    texts = {lang: {f: fp.get(values, f) or "" for f in FIELDS.values()}}
    loc = Localization(root)
    for target in values.get("target_languages") or []:
        data = loc.read(target)
        texts[target] = {f: data.get(f, "") for f in FIELDS.values()}
    return {k: {f: t for f, t in v.items() if t} for k, v in texts.items()}


def validate_store(
    values: dict[str, Any], root: Path, llm_judgements: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    source = str(values.get("source_language") or "english")
    texts = store_texts(values, root)
    valve = [f.__dict__ for f in check_store_text(texts)]
    for lang, fields in texts.items():
        for phrase in _banned_phrases(lang):
            for field, text in fields.items():
                if phrase.lower() in text.lower():
                    valve.append(
                        {
                            "rule_id": "banned_phrase",
                            "severity": "warning",
                            "field": field,
                            "language": lang,
                            "message": f'"{phrase}"',
                            "excerpt": "",
                        }
                    )
    guide = guide_for(values)
    rub: dict[str, Any] = {}
    questions: list[dict[str, str]] = []
    for section, field in FIELDS.items():
        current = texts.get(source, {}).get(field)
        if not current:
            rub[section] = {"status": "missing", "next": f"generate(section='store_{section}')"}
            continue
        results, qs, score = rubric.evaluate(section, current, values, guide, final=True, root=root)  # type: ignore[arg-type]
        rub[section] = {
            "score": score,
            "findings": [r.model_dump() for r in results if r.outcome in ("fail", "warn")],
            "passed": [r.rule_id for r in results if r.outcome == "pass"],
        }
        questions += qs
    translations: dict[str, list[dict[str, Any]]] = {}
    for lang, fields in texts.items():
        if lang == source or not fields.get(FIELDS["long"]):
            continue
        results, _, _ = rubric.evaluate("long", fields[FIELDS["long"]], values, guide, final=True)
        found = [r.model_dump() for r in results if r.rule_id in LANGUAGE_INDEPENDENT and r.outcome in ("fail", "warn")]
        if found:
            translations[lang] = found
    out: dict[str, Any] = {
        "valve_rules": {
            "errors": [v for v in valve if v["severity"] == "error"],
            "warnings": [v for v in valve if v["severity"] != "error"],
        },
        "rubric": rub,
        "translations": translations,
        "never_overwritten": "Nothing was changed. Save an improved version with save_draft(strategy='revision') "
        "and let the user pick it.",
    }
    if llm_judgements:
        known = {q["rule_id"] for q in questions}
        out["llm_judged"] = [
            {k: j.get(k) for k in ("rule_id", "field", "outcome", "note")}
            for j in llm_judgements
            if j.get("rule_id") in known and j.get("outcome") in ("pass", "fail", "warn")
        ]
        out["llm_judged_unmatched"] = [j.get("rule_id") for j in llm_judgements if j.get("rule_id") not in known]
    elif questions:
        out["judge_these"] = {
            "questions": questions,
            "how": "Judge each question for the current text, then call validate again with "
            "llm_judgements=[{rule_id, field, outcome: pass|warn|fail, note}].",
        }
    return out


def validate_achievements(values: dict[str, Any], root: Path) -> dict[str, Any]:
    achievements = values.get("achievements") or []
    problems: list[dict[str, Any]] = []
    for a in achievements:
        for key, limit in (("name", 64), ("description", 160)):
            text = a.get(key)
            if not text:
                problems.append({"id": a["id"], "severity": "error", "message": f"{key} missing"})
            elif len(text) > limit:
                problems.append(
                    {
                        "id": a["id"],
                        "severity": "warning",
                        "message": f"{key} is {len(text)} characters; Steam shows about {limit}",
                    }
                )
        if not a.get("icon"):
            problems.append({"id": a["id"], "severity": "error", "message": "icon missing"})
        elif not (root / a["icon"]).is_file():
            problems.append({"id": a["id"], "severity": "error", "message": f"icon not found: {a['icon']}"})
    if len(achievements) > 100:
        problems.append(
            {
                "id": "*",
                "severity": "warning",
                "message": f"{len(achievements)} achievements; new apps are limited to 100 until they qualify for more",
            }
        )
    _, code = code_definitions(values, root)
    return {"count": len(achievements), "problems": problems, "code": code}


def validate_project(
    values: dict[str, Any],
    state: State,
    root: Path,
    section: str,
    *,
    browser: bool = False,
    llm_judgements: list[dict[str, Any]] | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    if section == "store":
        return {"store": validate_store(values, root, llm_judgements)}
    if section == "achievements":
        return {"achievements": validate_achievements(values, root)}
    if section == "localization":
        return {"localization": [s.__dict__ for s in status(values, root)]}
    if section != "all":
        raise ValueError('section must be "store", "achievements", "localization" or "all".')
    results = evaluate_gates(values, state, root, browser=browser, today=today)
    problems = [
        {
            "gate": r.gate,
            "id": r.rule.id,
            "severity": r.rule.severity,
            "status": r.status,
            "message": r.message or r.rule.description,
        }
        for r in results
        if r.status in ("fail", "warn")
    ]
    return {
        "store": validate_store(values, root, llm_judgements),
        "achievements": validate_achievements(values, root),
        "localization": [s.__dict__ for s in status(values, root)],
        "rules": problems,
    }
