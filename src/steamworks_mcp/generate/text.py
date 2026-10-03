"""Text generation: the server writes a brief, the host model writes the text, the server checks and stores it.

* ``store_short``: three variants, each with its own hook strategy (fantasy, mechanic, situation_humor).
* ``store_long``: ``stage="outline"`` first; ``stage="text"`` only after the user approved an outline. The text uses
  ``[GIF: what it shows]`` placeholders, which also become a shot list.
* ``achievements``: names, descriptions and icon briefs for achievements that lack them.

Briefs carry Valve's rules (layer 1), the rubric (layer 2) with the genre guide's overrides (layer 3), and only
*derived* measurements of matching reference games, never their text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.drafts import DRAFT_ID, Draft, RubricResult
from steamworks_mcp.manifest.io import ProjectFiles, atomic_write, load_drafts, save_draft
from steamworks_mcp.manifest.state import Source, is_empty
from steamworks_mcp.references import bundled_analysis, cached_texts, catalog, matching, tags_for_game
from steamworks_mcp.references.anticopy import MIN_RUN, find_overlaps
from steamworks_mcp.style_guides import StyleGuide, for_tags
from steamworks_mcp.validate import rubric
from steamworks_mcp.validate.store_text import check_store_text, store_rules

STRATEGIES = {
    "fantasy": "Lead with the fantasy: who the players get to be and what that feels like.",
    "mechanic": "Lead with the core mechanic: the concrete thing players do that no other game does quite like this.",
    "situation_humor": "Lead with a situation: a typical funny or tense moment a group of friends will recognise.",
}
REQUIRED_INPUTS = [
    "game.name",
    "game.pitch",
    "game.genres",
    "game.core_loop",
    "game.usps",
    "game.target_audience",
    "game.tone",
]
SHORT_FIELD, LONG_FIELD = "store.short_description", "store.about"
ALLOWED_TAGS = "[h2] [h3] [b] [i] [u] [p] [list] [olist] [*] [hr] [quote]"


def missing_inputs(values: dict[str, Any]) -> list[str]:
    out = [p for p in REQUIRED_INPUTS if is_empty(fp.get(values, p))]
    if is_empty(fp.get(values, "game.players")):
        out.append("game.players")
    return out


def guide_for(values: dict[str, Any]) -> StyleGuide | None:
    return for_tags(tags_for_game(values))


def reference_ids(values: dict[str, Any], limit: int = 2) -> list[int]:
    explicit = list(fp.get(values, "game.reference_appids") or [])
    if explicit:
        return explicit[:limit]
    return [g.appid for g in matching(tags_for_game(values), limit)]


def _reference_summaries(values: dict[str, Any], section: str) -> list[dict[str, Any]]:
    out = []
    names = {g.appid: g.name for g in catalog()}
    for appid in reference_ids(values):
        a = bundled_analysis(appid)
        if a is None:
            continue
        item: dict[str, Any] = {"game": names.get(appid, str(appid)), "appid": appid}
        if section == "short":
            item["short_description"] = a.store.short_description.model_dump()
        elif section == "long":
            item["about"] = a.store.about.model_dump()
        else:
            item["achievements"] = a.achievements.model_dump()
        out.append(item)
    return out


def _game_inputs(values: dict[str, Any]) -> dict[str, Any]:
    game = dict(values.get("game") or {})
    game.pop("engine", None)
    store = values.get("store") or {}
    return {"game": game, "store": {k: store.get(k) for k in ("platforms", "tags", "categories", "genres")}}


def _valve_rules(field: str) -> list[str]:
    target = "short_description" if field == SHORT_FIELD else "about_this_game"
    return [
        r.rule
        for r in store_rules()
        if target in r.applies_to and r.check in ("deterministic", "llm_judged") and r.severity == "error"
    ]


def _rubric_rules(section: str, guide: StyleGuide | None) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "why": r.rationale,
            "do": r.fix,
            **({"params": r.params} if r.params and r.kind not in ("word_list_max",) else {}),
        }
        for r in rubric.rules_for(guide)
        if r.applies_to in (section, "both") and r.stage != "final"
    ]


def chosen_outline(files: ProjectFiles) -> Draft | None:
    return next((d for d in load_drafts(files, LONG_FIELD) if d.strategy == "outline" and d.status == "chosen"), None)


def brief(values: dict[str, Any], files: ProjectFiles, section: str, stage: str | None = None) -> dict[str, Any]:
    if section in ("store_short", "store_long") and (missing := missing_inputs(values)):
        return {
            "status": "needs_input",
            "missing": missing,
            "next": "Ask the user with start_interview, then call generate again.",
        }
    guide = guide_for(values)
    common: dict[str, Any] = {
        "inputs": _game_inputs(values),
        "style_guide": {"id": guide.meta.id, "guide": guide.body} if guide else None,
        "anti_copy": f"Never reuse {MIN_RUN} or more consecutive words from another game's store page or achievements.",
    }
    if section == "store_short":
        return {
            "status": "ready",
            "task": "Write three short descriptions for the Steam store page, one per strategy.",
            "strategies": STRATEGIES,
            "format": "Plain text, 160-300 characters, 2-3 sentences, no formatting, no links.",
            "valve_rules": _valve_rules(SHORT_FIELD),
            "rubric": _rubric_rules("short", guide),
            "references": _reference_summaries(values, "short"),
            **common,
            "submit": "save_draft(path, field='store.short_description', value=<text>, strategy=<strategy>) "
            "once per variant.",
        }
    if section == "store_long" and (stage or "outline") == "outline":
        return {
            "status": "ready",
            "task": "Propose an outline for 'About This Game' as a numbered list of blocks.",
            "format": "One line per block: 'hook line', '[GIF: what it shows]', 'paragraph: what it says', "
            "'heading: …', 'list: item; item; item'. Short paragraph + GIF blocks; one 3-6 item feature list.",
            "rubric": _rubric_rules("long", guide),
            "references": _reference_summaries(values, "long"),
            **common,
            "submit": "save_draft(path, field='store.about', value=<outline>, strategy='outline'). The user approves "
            "it with set_field(path, field='store.about', from_draft=<id>); then "
            "generate(section='store_long', stage='text').",
        }
    if section == "store_long":
        outline = chosen_outline(files)
        if outline is None:
            return {
                "status": "needs_outline",
                "next": "generate(section='store_long', stage='outline') and get the user's approval first.",
            }
        return {
            "status": "ready",
            "task": "Write 'About This Game' following the approved outline.",
            "outline": outline.value,
            "format": f"Steam BBCode using only {ALLOWED_TAGS}. Put [GIF: what it shows] where a clip goes; "
            "keep paragraphs short; no links, no other games, no fake Steam buttons or prices.",
            "valve_rules": _valve_rules(LONG_FIELD),
            "rubric": _rubric_rules("long", guide),
            "references": _reference_summaries(values, "long"),
            **common,
            "submit": "save_draft(path, field='store.about', value=<bbcode>, strategy='text').",
        }
    if section == "achievements":
        todo = [
            {k: v for k, v in a.items() if k in ("id", "name", "description", "hidden", "progress", "category")}
            for a in values.get("achievements") or []
            if not a.get("name") or not a.get("description")
        ]
        return {
            "status": "ready" if todo else "nothing_to_do",
            "task": "Write a display name, a description and an icon brief for each achievement below.",
            "achievements": todo,
            "format": "Names: 1-4 words. Descriptions: one sentence that says how to unlock it (about 7 words, "
            "a number "
            "when there is a threshold). Hidden achievements may stay vague. Icon brief: one line for the artist.",
            "targets": (guide.meta.achievements if guide else {}),
            "references": _reference_summaries(values, "achievements"),
            **common,
            "submit": "set_field(path, values={'achievements.<ID>.name': …, 'achievements.<ID>.description': …}, "
            "source='generated'). Icon briefs go in your reply to the user (the tool never draws artwork).",
        }
    raise ValueError(f'Unknown text section "{section}".')


# ---------------------------------------------------------------------------------------------------- drafts


def _next_id(files: ProjectFiles, field: str, strategy: str | None) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", (strategy or "draft").lower()).strip("-") or "draft"
    taken = {d.id for d in load_drafts(files, field)}
    n = 1
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def save_text_draft(
    values: dict[str, Any],
    files: ProjectFiles,
    field: str,
    value: str,
    strategy: str | None,
    cache_dir: Path,
    source: Source = "generated",
    notes: str = "",
) -> dict[str, Any]:
    """Check a text the model wrote and store it as a draft. Valve-rule errors and copied passages are rejected."""
    if not fp.is_valid(field):
        raise fp.FieldPathError(f'"{field}" is not a field of steamworks.yaml')
    if not isinstance(value, str) or not value.strip():
        raise ValueError("The draft is empty.")
    is_outline = strategy == "outline"
    lang = str(values.get("source_language") or "english")
    if field in (SHORT_FIELD, LONG_FIELD) and not is_outline:
        errors = [f for f in check_store_text({lang: {field: value}}) if f.severity == "error"]
        if errors:
            raise ValueError(
                "Rejected, breaks Valve's store rules: " + "; ".join(f"[{e.rule_id}] {e.message}" for e in errors)
            )
    references = cached_texts(cache_dir)
    overlaps = find_overlaps(value, references)
    if overlaps:
        o = overlaps[0]
        raise ValueError(
            f'Rejected: {o.length} consecutive words copied from a reference game ("{" ".join(o.words[:12])}…"). '
            "Rewrite it in your own words."
        )
    results: list[RubricResult] = []
    questions: list[dict[str, str]] = []
    score: float | None = None
    if field in (SHORT_FIELD, LONG_FIELD) and not is_outline:
        section: rubric.Section = "short" if field == SHORT_FIELD else "long"
        results, questions, score = rubric.evaluate(section, value, values, guide_for(values))
    draft = Draft(
        id=_next_id(files, field, strategy),
        field=field,
        value=value,
        strategy=strategy,
        source=source,
        rubric=results,
        rubric_score=score,
        notes=notes,
    )
    assert DRAFT_ID.match(draft.id)
    save_draft(files, draft)
    out: dict[str, Any] = {
        "draft_id": draft.id,
        "field": field,
        "rubric_score": score,
        "rubric": [r.model_dump() for r in results if r.outcome != "pass"],
        "anti_copy": f"checked against {len(references)} cached reference texts"
        if references
        else "no reference texts cached yet (fetch_reference)",
    }
    if questions:
        out["judge_these"] = questions
    if field == LONG_FIELD and not is_outline:
        out["gif_shotlist"] = write_shotlist(files, draft)
    if is_outline:
        out["next"] = (
            f"Show the outline to the user; if approved: set_field(path, field='store.about', from_draft='{draft.id}')."
        )
    else:
        out["next"] = f"Show it to the user; to use it: set_field(path, field='{field}', from_draft='{draft.id}')."
    return out


def write_shotlist(files: ProjectFiles, draft: Draft) -> str | None:
    shots = re.findall(r"\[GIF:\s*([^\]]+)\]", str(draft.value))
    if not shots:
        return None
    path = files.draft_dir(draft.field) / f"{draft.id}.gif_shotlist.md"
    lines = [f"# GIFs to capture for draft {draft.id}", "", "Each clip: 3-12 seconds, gameplay only, 1170 px wide.", ""]
    lines += [f"{i}. {s.strip()}" for i, s in enumerate(shots, 1)]
    atomic_write(path, "\n".join(lines) + "\n")
    return path.relative_to(files.root).as_posix()
