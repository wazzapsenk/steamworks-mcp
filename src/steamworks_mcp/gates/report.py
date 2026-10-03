"""Gap report: per gate, what blocks it, what is waiting for approval, and what to do next."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path
from typing import Any

from steamworks_mcp.gates.engine import RuleResult, evaluate_gates, gate_files
from steamworks_mcp.interview.questions import pending_fields
from steamworks_mcp.manifest.state import State


def gap_report(
    values: dict[str, Any],
    state: State,
    root: Path,
    gate: int | None = None,
    *,
    browser: bool = False,
    today: dt.date | None = None,
    include_info: bool = False,
) -> dict[str, Any]:
    gates = None if gate is None else [gate]
    results = evaluate_gates(values, state, root, gates, browser=browser, today=today)
    out_gates = []
    for g in gate_files():
        if gates is not None and g.gate not in gates:
            continue
        rs = [r for r in results if r.gate == g.gate]
        blocking = [r for r in rs if r.blocking]
        out: dict[str, Any] = {
            "gate": g.gate,
            "title": g.title,
            "ready": not blocking,
            "counts": dict(Counter(r.status for r in rs)),
            "blocking": [r.as_dict() for r in blocking],
            "recommended": [
                r.as_dict() for r in rs if not r.blocking and r.status in ("fail", "warn", "review", "todo")
            ],
            "passed": [r.rule.id for r in rs if r.status in ("pass", "done")],
        }
        if include_info:
            out["info"] = [
                {"id": r.rule.id, "what": r.rule.description, "source": r.rule.source_doc}
                for r in rs
                if r.status == "info"
            ]
        out_gates.append(out)
    to_fill = pending_fields(results, values, state)
    to_approve = sorted({p for r in results for p in r.needs_approval})
    needs_review = sorted(p for p, fs in state.fields.items() if fs.status == "needs_review")
    return {
        "gates": out_gates,
        "fields_to_fill": len(to_fill),
        "fields_to_approve": to_approve,
        "fields_changed_after_approval": needs_review,
        "next_steps": next_steps(results, len(to_fill), to_approve, values),
    }


def next_steps(results: list[RuleResult], to_fill: int, to_approve: list[str], values: dict[str, Any]) -> list[str]:
    steps = []
    if to_fill:
        steps.append(f"Ask the user: call start_interview ({to_fill} field(s) to fill or confirm).")
    if to_approve:
        shown = ", ".join(to_approve[:8]) + (" …" if len(to_approve) > 8 else "")
        steps.append(f"Show these drafts to the user and, if they agree, call approve_fields: {shown}.")
    store = values.get("store") or {}
    if not store.get("short_description") or not store.get("about"):
        steps.append("Write the store texts with generate(section='store_short') and generate(section='store_long').")
    if any(r.rule.check.kind == "asset" and r.status == "todo" for r in results):
        steps.append("Produce the store and library images from your art with prepare_images.")
    manual = [
        r for r in results if r.status == "todo" and r.rule.check.kind == "checklist" and r.rule.severity == "required"
    ]
    if manual:
        first = manual[0]
        steps.append(
            f"{len(manual)} manual step(s) in Steamworks, starting with gate {first.gate}: {first.rule.title}. "
            f"When done, confirm with mark_applied(['checklist.{first.rule.id}'])."
        )
    if not steps:
        steps.append("Nothing blocking: export_package for the next gate, or apply what can be applied.")
    return steps
