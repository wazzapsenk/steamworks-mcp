"""One shape for every tool result, so every assistant shows the same thing in the same way.

Every tool returns its own data plus four keys, always first:

- ``outcome``: ``ok``, ``needs_input`` (the user has to answer or choose), ``needs_confirmation`` (a dry run waits
  for the user's OK), ``partial`` (some of it worked, see the data) or ``refused`` (nothing was done, see ``next``).
- ``summary``: one plain sentence for the user, without field paths or tool names where possible.
- ``next``: what to do next, as instructions for the assistant (may be empty).
- ``display``: Markdown to show the user as it is (may be empty). Tables keep the same columns and order in every
  client; an assistant that talks to the user in another language translates the words and keeps the layout.

Errors use the same keys (``outcome: "error"``), as the text of the tool error.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from functools import cache
from typing import Any, Literal

from steamworks_mcp.gates.engine import gate_files

Outcome = Literal["ok", "needs_input", "needs_confirmation", "partial", "refused", "error"]
ENVELOPE = ("outcome", "summary", "next", "display")
MAX_ROWS = 25

MODES = {
    "API": "automatic (Web API)",
    "BROWSER": "automatic (Steamworks site)",
    "ARTIFACT": "file to upload",
    "MANUAL": "by hand in Steamworks",
}
ITEM_STATUS = {
    "fail": "missing",
    "review": "waiting for your OK",
    "todo": "to do",
    "unknown": "cannot check yet",
    "warn": "check",
}


def result(
    data: dict[str, Any],
    summary: str,
    *,
    outcome: Outcome = "ok",
    next: str | Sequence[str] | None = None,
    display: str = "",
) -> dict[str, Any]:
    """``data`` with the envelope in front. ``next`` defaults to the data's own ``next``."""
    steps = steps_of(data.get("next") if next is None else next)
    rest = {k: v for k, v in data.items() if k not in ENVELOPE}
    return {"outcome": outcome, "summary": summary, "next": steps, "display": display.strip(), **rest}


def error(message: str, hint: str | None) -> str:
    """The text of a tool error, in the same shape as a result."""
    body = {"outcome": "error", "summary": message, "next": steps_of(hint), "display": ""}
    return json.dumps(body, ensure_ascii=False)


def steps_of(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


# ---------------------------------------------------------------------------------------------------- formatting


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def fmt(value: Any, limit: int = 60) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        text = ", ".join(fmt(v, limit) for v in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def cell(value: Any, limit: int = 60) -> str:
    return fmt(value, limit).replace("|", "\\|")


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], limit: int = MAX_ROWS) -> str:
    rows = list(rows)
    if not rows:
        return ""
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"\n…and {len(rows) - limit} more.")
    return "\n".join(lines)


def section(title: str, body: str) -> str:
    return f"**{title}**\n\n{body}" if body else ""


def join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


def gate_label(gate: int, title: str | None = None) -> str:
    return f"Step {gate} · {title or gate_titles().get(gate, 'release')}"


@cache
def gate_titles() -> dict[int, str]:
    return {g.gate: g.title for g in gate_files()}


# ---------------------------------------------------------------------------------------------------- project


def scan(data: dict[str, Any], what: str = "The scan") -> dict[str, Any]:
    """init_project's and scan_project's scan, and generate's deterministic drafts (the same merge report)."""
    if not data.get("scanners") and "message" in data:
        message = str(data.pop("message"))
        return result(data, message, next="Answer the interview instead: start_interview.")
    applied, conflicts = data.get("applied") or [], data.get("conflicts") or []
    warnings = data.get("warnings") or []
    if applied:
        summary = f"{what} found {plural(len(applied), 'value')}, saved as drafts for you to review"
    else:
        summary = f"{what} found nothing new"
    if conflicts:
        summary += f"; {plural(len(conflicts), 'value')} differ from what you already approved"
    if warnings:
        summary += f"; {plural(len(warnings), 'warning')}"
    steps = []
    if applied:
        steps.append("Show the user what was found; approve_fields only what they confirm.")
    if conflicts:
        steps.append("For each conflict ask the user which value is right and save it with set_field.")
    display = join(
        section(
            "Found",
            table(
                ["Field", "Value", "Confidence"],
                [[a.get("field"), a.get("value"), f"{a.get('confidence', 0):.0%}"] for a in applied],
            ),
        ),
        section(
            "Differs from your approved value",
            table(
                ["Field", "Approved", "Found"],
                [[c.get("field"), c.get("current"), c.get("scanned", c.get("proposed"))] for c in conflicts],
            ),
        ),
        section("Warnings", "\n".join(f"- {fmt(w.get('message', w), 200)}" for w in warnings)),
    )
    return result(data, summary + ".", next=steps, display=display)


def init_project(data: dict[str, Any]) -> dict[str, Any]:
    created = data.get("created") or []
    summary = "Started tracking the game" if created else "steamworks.yaml already existed; nothing was overwritten"
    steps = []
    display = ""
    if isinstance(data.get("scan"), dict):
        scanned = scan(dict(data["scan"]))
        summary += ". " + scanned["summary"]
        steps += scanned["next"]
        display = scanned["display"]
    else:
        summary += "."
    if data.get("gitignore_suggestions"):
        steps.append(
            "Suggest these lines for the project's .gitignore (this server never edits it): "
            + ", ".join(data["gitignore_suggestions"])
        )
    steps.append("Call gap_report to see what each release step still needs.")
    return result(data, summary, next=steps, display=display)


def gap_report(data: dict[str, Any]) -> dict[str, Any]:
    gates = data.get("gates") or []
    open_gates = [g for g in gates if not g.get("ready")]
    to_fill, to_approve = data.get("fields_to_fill", 0), data.get("fields_to_approve") or []
    if not open_gates:
        summary = "Nothing blocks " + (
            gate_label(gates[0]["gate"], gates[0]["title"]) if len(gates) == 1 else "any release step"
        )
    else:
        first = open_gates[0]
        summary = (
            f"{'Next: ' if len(gates) > 1 else ''}{gate_label(first['gate'], first['title'])} has "
            f"{plural(len(first.get('blocking') or []), 'open item')}"
        )
        extra = []
        if to_fill:
            extra.append(plural(to_fill, "answer") + " to give")
        if to_approve:
            extra.append(plural(len(to_approve), "value") + " to approve")
        if extra:
            summary += " (" + ", ".join(extra) + ")"
    parts = []
    for g in gates:
        blocking = g.get("blocking") or []
        head = f"{gate_label(g['gate'], g['title'])}: " + (
            "ready" if not blocking else plural(len(blocking), "item") + " open"
        )
        rows = [
            [
                ITEM_STATUS.get(i.get("status", ""), i.get("status")),
                i.get("title"),
                MODES.get(i.get("mode", ""), i.get("mode")),
                i.get("where"),
            ]
            for i in blocking
        ]
        recommended = len(g.get("recommended") or [])
        tail = f"\n\nAlso {plural(recommended, 'recommended item')} (not blocking)." if recommended else ""
        parts.append(f"**{head}**" + ("\n\n" + table(["Status", "What", "How", "Where"], rows) if rows else "") + tail)
    return result(data, summary + ".", display=join(*parts))


def start_interview(data: dict[str, Any]) -> dict[str, Any]:
    questions = data.get("questions") or []
    remaining = data.get("remaining_after_these", 0)
    if not questions:
        message = data.pop("message", "Nothing left to ask.")
        return result(data, "Nothing left to ask for this step.", next=message)
    summary = plural(len(questions), "question") + " for you"
    if remaining:
        summary += f" ({remaining} more after these)"
    lines = []
    for n, q in enumerate(questions, 1):
        line = f"{n}. {q.get('question')}"
        if q.get("options"):
            labels = q.get("option_labels") or {}
            line += " Options: " + ", ".join(str(labels.get(o, o)) for o in q["options"]) + "."
        if q.get("suggestion") is not None:
            line += f" Suggested: **{fmt(q['suggestion'])}**"
            if q.get("suggestion_source"):
                line += f" (from {q['suggestion_source']})"
            line += "."
        lines.append(line)
    steps = [
        "Ask the user these questions in chat (they may answer in their own words), then save the answers with "
        "set_field(path, values={<question id>: <answer>})."
    ]
    if data.get("saved_from_form"):
        summary = f"Saved {plural(len(data['saved_from_form']), 'answer')} from the form; " + summary
    return result(data, summary + ".", outcome="needs_input", next=steps, display="\n".join(lines))


def set_field(data: dict[str, Any]) -> dict[str, Any]:
    if "outline_approved" in data:
        return result(data, "Outline picked. The full text can be written from it now.")
    statuses = data.get("status") or {}
    approved = sum(1 for s in statuses.values() if s in ("approved", "applied"))
    drafts = len(statuses) - approved
    parts = []
    if approved:
        parts.append(f"{approved} approved")
    if drafts:
        parts.append(plural(drafts, "draft") + " to review")
    summary = f"Saved {plural(len(data.get('set') or []), 'value')}" + (" (" + ", ".join(parts) + ")" if parts else "")
    not_saved = data.get("not_saved") or {}
    findings = data.get("store_rule_findings") or []
    display = join(
        section("Not saved", table(["Field", "Why"], [[k, v] for k, v in not_saved.items()])),
        section(
            "Store rule warnings",
            table(
                ["Field", "Rule", "Message"], [[f.get("field"), f.get("rule_id"), f.get("message")] for f in findings]
            ),
        ),
    )
    if not_saved:
        summary += f"; {plural(len(not_saved), 'value')} not saved"
    return result(data, summary + ".", outcome="partial" if not_saved else "ok", display=display)


def approve_fields(data: dict[str, Any]) -> dict[str, Any]:
    approved, skipped = data.get("approved") or [], data.get("skipped") or {}
    summary = f"Approved {plural(len(approved), 'value')}"
    if skipped:
        summary += f"; skipped {len(skipped)}"
    display = section("Skipped", table(["Field", "Why"], [[k, v] for k, v in skipped.items()]))
    outcome: Outcome = "ok" if approved or not skipped else "refused"
    return result(data, summary + ".", outcome=outcome, display=display)


def mark_applied(data: dict[str, Any]) -> dict[str, Any]:
    applied, refused = data.get("applied") or [], data.get("refused") or {}
    summary = f"Marked {plural(len(applied), 'item')} as done in Steamworks"
    if refused:
        summary += f"; {len(refused)} could not be marked"
    outcome: Outcome = "partial" if applied and refused else ("refused" if refused else "ok")
    display = section("Not marked", table(["Item", "Why"], [[k, v] for k, v in refused.items()]))
    return result(data, summary + ".", outcome=outcome, display=display)


# ---------------------------------------------------------------------------------------------------- texts


TEXTS = {
    "store_short": "the short description",
    "store_long": "the About This Game text",
    "achievements": "the achievement texts",
}


def generate(data: dict[str, Any], section_name: str, stage: str | None) -> dict[str, Any]:
    if section_name not in TEXTS:
        made = scan(data, "Drafting")
        if section_name == "builds" and isinstance(data.get("scripts"), dict) and "not_yet" not in data["scripts"]:
            made["summary"] = made["summary"][:-1] + "; the SteamPipe build scripts were written."
        return made
    status = data.get("status")
    what = TEXTS[section_name]
    if status == "needs_input":
        missing = data.get("missing") or []
        return result(
            data,
            f"Some answers are needed before writing {what}: {fmt(missing, 200)}.",
            outcome="needs_input",
        )
    if status == "needs_outline":
        return result(data, "An outline has to be written and approved before the full text.", outcome="needs_input")
    if section_name == "store_long" and stage != "text":
        steps = (
            "Write two or three outlines from this brief, save each with save_draft(strategy='outline'), and let the "
            "user pick one."
        )
    elif section_name == "achievements":
        steps = (
            "Write the missing names, descriptions and icon briefs, save them with set_field(source='generated') "
            "and validate(section='achievements')."
        )
    else:
        steps = "Write the variants the brief asks for, save each with save_draft, then show them to the user."
    return result(data, f"Brief for {what} is ready: the assistant writes it, you choose.", next=steps)


def save_draft(data: dict[str, Any]) -> dict[str, Any]:
    score = data.get("rubric_score")
    summary = f"Draft {data.get('draft_id')} saved"
    if score is not None:
        summary += (
            f" (rubric score {score:.0%})" if isinstance(score, float) and score <= 1 else f" (rubric score {score})"
        )
    findings = data.get("rubric") or []
    display = section(
        "Rubric",
        table(
            ["Rule", "Result", "Suggestion"],
            [[r.get("rule_id"), r.get("outcome"), r.get("fix") or r.get("message")] for r in findings],
        ),
    )
    return result(data, summary + ".", display=display)


def validate(data: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    store = data.get("store") or {}
    valve = store.get("valve_rules") or {}
    for f in (valve.get("errors") or []) + (valve.get("warnings") or []):
        rows.append([f.get("severity"), "Store text", f"{f.get('field')} ({f.get('language')})", f.get("message")])
    for name, rub in (store.get("rubric") or {}).items():
        for f in rub.get("findings") or []:
            rows.append([f.get("outcome"), f"Store text, {name}", f.get("rule_id"), f.get("fix") or f.get("message")])
        if rub.get("status") == "missing":
            rows.append(["missing", f"Store text, {name}", "—", "Not written yet."])
    for p in (data.get("achievements") or {}).get("problems") or []:
        rows.append([p.get("severity"), "Achievements", p.get("id"), p.get("message")])
    for r in data.get("rules") or []:
        rows.append([r.get("status"), gate_label(int(r.get("gate", 0))), r.get("id"), r.get("message")])
    errors = sum(1 for r in rows if r[0] in ("error", "fail", "missing"))
    warnings = len(rows) - errors
    if not rows:
        summary = "No problems found."
    else:
        summary = f"{plural(errors, 'problem')} to fix and {plural(warnings, 'thing')} to check."
    steps = []
    if store.get("judge_these"):
        steps.append(
            "Judge the questions in store.judge_these for the current text, then call validate again with "
            "llm_judgements=[{rule_id, field, outcome, note}]."
        )
    if rows:
        steps.append(
            "To fix a store text, save a better version with save_draft(strategy='revision'); nothing was changed."
        )
    localization = data.get("localization") or []
    display = join(
        table(["Severity", "Area", "Item", "Message"], rows, limit=40),
        section("Translations", localization_table(localization)) if isinstance(localization, list) else "",
    )
    return result(data, summary, next=steps, display=display)


def preview_store(data: dict[str, Any]) -> dict[str, Any]:
    return result(
        data,
        f"Store text preview written to {data.get('file')}.",
        next="Tell the user to open the file in a browser; the marked line is an estimate of the first screen.",
    )


def prepare_images(data: dict[str, Any]) -> dict[str, Any]:
    imgs = data.get("images") or []
    made = [i for i in imgs if i.get("status") in ("generated", "override")]
    skipped = [i for i in imgs if i.get("status") == "skipped"]
    icons = data.get("achievement_icons") or []
    summary = f"Made {plural(len(made), 'image')}"
    if icons:
        summary += f" and {plural(len(icons), 'achievement icon')}"
    if skipped:
        summary += f"; {len(skipped)} skipped"
    display = table(
        ["Image", "Size", "Result", "Notes"],
        [[i.get("label"), i.get("size"), i.get("status"), "; ".join(i.get("notes") or [])] for i in imgs],
    )
    steps = [f"Show the user the preview page: {data.get('preview')}."]
    if skipped:
        steps.append(
            "Skipped images need assets.key_art and assets.logo (or a hand-made override); ask the user for them."
        )
    return result(data, summary + ".", outcome="partial" if skipped and made else "ok", next=steps, display=display)


# ---------------------------------------------------------------------------------------------------- market


def fetch_reference(data: dict[str, Any]) -> dict[str, Any]:
    name = data.get("name") or f"app {data.get('appid')}"
    return result(data, f"Measured the store page and achievements of {name} (numbers only, no text kept).")


def study_market(data: dict[str, Any]) -> dict[str, Any]:
    pages = data.get("pages") or []
    if not pages:
        return result(data, "No close popular games were found for these tags.", outcome="needs_input")
    return result(
        data,
        f"Found {plural(len(pages), 'close popular game')} to study.",
        next="Label every page with the vocabulary and send the notes with save_market_study.",
        display=table(["Game", "App id"], [[p.get("name"), p.get("appid")] for p in pages]),
    )


def save_market_study(data: dict[str, Any]) -> dict[str, Any]:
    missing = data.get("not_labelled") or []
    summary = "Market study saved"
    if missing:
        summary += f"; {plural(len(missing), 'page')} still without labels"
    return result(data, summary + ".", outcome="partial" if missing else "ok")


# ---------------------------------------------------------------------------------------------------- localization


def localization_table(languages: list[dict[str, Any]]) -> str:
    return table(
        ["Language", "Translated", "Missing", "Out of date"],
        [
            [
                s.get("language"),
                f"{s.get('translated')}/{s.get('total')}",
                len(s.get("missing") or []),
                len(s.get("stale") or []),
            ]
            for s in languages
        ],
    )


def localization_status(data: dict[str, Any]) -> dict[str, Any]:
    languages = data.get("languages") or []
    todo = data.get("to_translate", 0)
    if not languages:
        summary = "No target languages yet: every text stays in the source language."
    elif todo:
        summary = f"{plural(todo, 'text')} to translate or update across {plural(len(languages), 'language')}."
    else:
        summary = f"Every translation is up to date in {plural(len(languages), 'language')}."
    return result(data, summary, display=localization_table(languages))


def localization_pending(data: dict[str, Any]) -> dict[str, Any]:
    entries = data.get("entries") or []
    if not entries:
        return result(data, f"Nothing left to translate into {data.get('language')}.")
    return result(
        data,
        f"{plural(len(entries), 'text')} to translate into {data.get('language')}.",
        next="Translate every entry and save the batch with localization_set; repeat until nothing is pending.",
    )


def localization_set(data: dict[str, Any]) -> dict[str, Any]:
    saved, rejected = data.get("saved") or [], data.get("rejected") or {}
    summary = f"Saved {plural(len(saved), 'translation')} as drafts"
    if rejected:
        summary += f"; {len(rejected)} rejected"
    rows = rejected.items() if isinstance(rejected, dict) else [[r, ""] for r in rejected]
    display = section("Rejected", table(["Text", "Why"], rows))
    outcome: Outcome = "partial" if rejected and saved else ("refused" if rejected else "ok")
    steps = (
        ["Fix and resend the rejected ones."]
        if rejected
        else ["Show the user the translations; approve_fields(['localization.<language>.*']) only after they agreed."]
    )
    return result(data, summary + ".", outcome=outcome, next=steps, display=display)


# ---------------------------------------------------------------------------------------------------- packages & Steam


def export_package(data: dict[str, Any]) -> dict[str, Any]:
    counts = data.get("status") or {}
    still_open = sum(counts.get(k, 0) for k in ("fail", "review", "todo", "unknown"))
    files = data.get("files") or []
    summary = f"Wrote {plural(len(files), 'file')} to {data.get('folder')}"
    summary += f"; {plural(still_open, 'item')} still open." if still_open else "; nothing is open."
    return result(
        data,
        summary,
        next=f"Tell the user to open {data.get('folder')}/CHECKLIST.md: it names the Steamworks page and field of "
        "every file.",
        display="\n".join(f"- {f}" for f in files[:MAX_ROWS]),
    )


def _changes_table(changes: list[dict[str, Any]]) -> str:
    return table(
        ["Area", "Change", "What", "Now", "After"],
        [[c.get("area"), c.get("action"), c.get("target"), c.get("before"), c.get("after")] for c in changes],
        limit=40,
    )


def steam_write(data: dict[str, Any]) -> dict[str, Any]:
    """apply, restore_snapshot and set_build_live."""
    if data.get("refused"):
        pending = data.get("not_approved") or []
        return result(
            data,
            "Nothing was written: some values are not approved yet.",
            outcome="refused",
            next=str(data["refused"]),
            display=section("Not approved yet", "\n".join(f"- {p}" for p in pending[:MAX_ROWS])),
        )
    if data.get("dry_run"):
        if "changes" in data:
            changes = data.get("changes") or []
            display = _changes_table(changes)
            n = len(changes)
        elif "build_id" in data:
            n = 1
            display = f"Set build {data['build_id']} live on the branch **{data.get('branch')}**."
        else:  # leaderboards
            create, delete = data.get("create") or [], data.get("delete") or []
            n = len(create) + len(delete)
            display = table(
                ["Change", "Leaderboard"],
                [["create", b.get("name")] for b in create] + [["delete", name] for name in delete],
            )
        if not n:
            return result(data, "Steamworks already matches; nothing to change.", next=[])
        summary = f"Dry run: {plural(n, 'change')} would be made in Steamworks. Nothing was written yet."
        if data.get("warning"):
            summary += " " + str(data["warning"])
        return result(data, summary, outcome="needs_confirmation", display=display)
    done = data.get("done") or []
    remaining = data.get("still_different") or []
    if data.get("action") == "set_build_live":
        return result(data, f"Build {data.get('build_id')} is live on the branch {data.get('branch')}.")
    summary = f"Wrote {plural(len(done), 'change')} to Steamworks"
    if data.get("publish"):
        summary += " as unpublished drafts; nothing was published"
    if isinstance(remaining, list) and remaining:
        summary += f"; {plural(len(remaining), 'change')} did not take"
    outcome: Outcome = "partial" if data.get("error") or (isinstance(remaining, list) and remaining) else "ok"
    steps = [str(data["error"])] if data.get("error") else []
    if data.get("publish"):
        steps.append("Tell the user to review and publish the changes in Steamworks themselves (Publish tab).")
    return result(data, summary + ".", outcome=outcome, next=steps, display=_changes_table(done))


def import_from_steamworks(data: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    fill, conflicts = data.get("fill") or [], data.get("conflicts") or []
    verb = "Would fill" if dry_run else "Filled"
    summary = f"{verb} {plural(len(fill), 'empty field')} from Steamworks"
    if conflicts:
        summary += f"; {plural(len(conflicts), 'field')} differ"
    steps = []
    if dry_run and fill:
        steps.append("Show the user what would be filled; save with dry_run=false after they agreed.")
    if conflicts:
        steps.append("For each difference ask the user which value is right and save it with set_field.")
    display = join(
        section("To fill" if dry_run else "Filled", "\n".join(f"- {f}" for f in fill[:MAX_ROWS])),
        section(
            "Differences",
            table(
                ["Field", "steamworks.yaml", "Steamworks"],
                [[c["field"], c["steamworks_yaml"], c["steamworks"]] for c in conflicts],
            ),
        ),
    )
    outcome: Outcome = "needs_confirmation" if dry_run and fill else "ok"
    return result(data, summary + ".", outcome=outcome, next=steps, display=display)


def steam_read(data: dict[str, Any], what: str) -> dict[str, Any]:
    return result(data, f"Read {what.replace('_', ' ')} from Steamworks; nothing was changed.")


def steamworks_open(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("consent_required"):
        return result(
            data,
            "Writing to the Steamworks site needs your agreement to these terms first.",
            outcome="needs_input",
            display=str(data.get("text") or ""),
        )
    if not data.get("logged_in"):
        return result(data, "Sign in to Steamworks in the browser window that opened.", outcome="needs_input")
    return result(data, "Steamworks is open and signed in.")


def restore_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    if "snapshots" in data:
        snapshots = data.get("snapshots") or []
        return result(
            data,
            f"{plural(len(snapshots), 'snapshot')} saved before earlier writes.",
            display="\n".join(f"- {s}" for s in snapshots[:MAX_ROWS]),
        )
    return steam_write(data)


def server_info(data: dict[str, Any]) -> dict[str, Any]:
    rows = [
        ["Store texts, achievements, images, checks, export packages", "yes", "always on"],
        ["Market study and reference games", "yes", "public store pages"],
        [
            "Builds, branches, leaderboards (Web API)",
            fmt(data.get("publisher_key_configured")),
            "STEAMWORKS_PUBLISHER_KEY",
        ],
        ["Build uploads (steamcmd)", fmt(data.get("steamcmd_configured")), "STEAMCMD_PATH, STEAMCMD_USERNAME"],
        ["Writing to the Steamworks site (BROWSER mode)", fmt(data.get("browser_mode")), "STEAM_MCP_BROWSER=1"],
        ["Hidden-achievement data of reference games", fmt(data.get("web_api_key_configured")), "STEAM_WEB_API_KEY"],
    ]
    return result(
        data,
        f"steamworks-mcp {data.get('version')}, working in {data.get('workspace_root')}.",
        display=table(["Feature", "Ready", "Turned on by"], rows),
    )
