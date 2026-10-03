"""Named consistency checks used by the gate rules (``check: {kind: crosscheck, name: …}``)."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

from steamworks_mcp.data import load_yaml
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.state import is_empty
from steamworks_mcp.validate.rules_data import EventsFile

Outcome = Literal["pass", "fail", "warn", "unknown"]


@dataclass
class CheckContext:
    values: dict[str, Any]
    root: Path
    today: dt.date


@dataclass
class CheckResult:
    status: Outcome
    message: str = ""
    fields: list[str] = field(default_factory=list)
    """Fields the user should look at."""


def _get(ctx: CheckContext, path: str) -> Any:
    return fp.get(ctx.values, path)


def _date(v: Any) -> dt.date | None:
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


@cache
def events() -> EventsFile:
    return EventsFile.model_validate(load_yaml("events.yaml"))


def scan_facts(root: Path, scanner: str = "unity") -> dict[str, Any]:
    p = root / ".steam-mcp" / "scan" / f"{scanner}.json"
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")).get("facts", {}))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------------------------------- checks


def ai_disclosure_complete(ctx: CheckContext) -> CheckResult:
    uses = _get(ctx, "content.ai.uses_ai")
    if uses is None:
        return CheckResult(
            "fail", "Say whether AI tools were used to make the game (never assumed).", ["content.ai.uses_ai"]
        )
    if not uses:
        return CheckResult("pass")
    pre, live = _get(ctx, "content.ai.pre_generated"), _get(ctx, "content.ai.live_generated")
    if is_empty(pre) and is_empty(live):
        return CheckResult(
            "fail",
            "Describe how AI-made content is used (pre-generated and/or live-generated).",
            ["content.ai.pre_generated", "content.ai.live_generated"],
        )
    if not is_empty(live) and is_empty(_get(ctx, "content.ai.guardrails")):
        return CheckResult(
            "fail", "Live-generated AI content needs a description of its guardrails.", ["content.ai.guardrails"]
        )
    return CheckResult("pass")


def sysreqs_per_platform(ctx: CheckContext) -> CheckResult:
    platforms = _get(ctx, "store.platforms") or []
    if not platforms:
        return CheckResult("fail", "Tick the operating systems the game supports first.", ["store.platforms"])
    missing = [
        f"store.system_requirements.{p}.minimum"
        for p in platforms
        if is_empty(_get(ctx, f"store.system_requirements.{p}.minimum"))
    ]
    if missing:
        return CheckResult(
            "fail", f"Minimum system requirements missing for: {', '.join(p.split('.')[2] for p in missing)}.", missing
        )
    extra = [
        p
        for p in ("windows", "macos", "linux")
        if p not in platforms and not is_empty(_get(ctx, f"store.system_requirements.{p}"))
    ]
    if extra:
        return CheckResult("warn", f"Requirements filled for unsupported OS: {', '.join(extra)}.", ["store.platforms"])
    return CheckResult("pass")


def depot_per_platform(ctx: CheckContext) -> CheckResult:
    platforms = _get(ctx, "store.platforms") or []
    if not platforms:
        return CheckResult("fail", "Tick the supported operating systems first.", ["store.platforms"])
    depots = _get(ctx, "apps.main.builds.depots") or []
    options = _get(ctx, "apps.main.installation.launch_options") or []
    problems = []
    for p in platforms:
        if not any(d.get("os") in (p, "all") for d in depots):
            problems.append(f"no depot for {p}")
        if not any(o.get("os") in (p, "all") for o in options):
            problems.append(f"no launch option for {p}")
    if problems:
        return CheckResult(
            "fail", "; ".join(problems) + ".", ["apps.main.builds.depots", "apps.main.installation.launch_options"]
        )
    return CheckResult("pass")


CATEGORY_CHECKS: list[tuple[str, str, Callable[[CheckContext], bool]]] = [
    ("Steam Achievements", "achievements", lambda c: bool(_get(c, "achievements"))),
    ("Steam Leaderboards", "leaderboards", lambda c: bool(_get(c, "leaderboards"))),
    (
        "Steam Cloud",
        "apps.main.cloud.enabled",
        lambda c: bool(_get(c, "apps.main.cloud.enabled")) or bool(_get(c, "apps.main.cloud.auto_cloud")),
    ),
    (
        "Full controller support",
        "game.platform_features.controller",
        lambda c: _get(c, "game.platform_features.controller") == "full",
    ),
    (
        "Partial Controller Support",
        "game.platform_features.controller",
        lambda c: _get(c, "game.platform_features.controller") == "partial",
    ),
    (
        "Remote Play Together",
        "game.platform_features.remote_play_together",
        lambda c: _get(c, "game.platform_features.remote_play_together") is True,
    ),
    ("Online Co-op", "game.players.online_coop", lambda c: _get(c, "game.players.online_coop") is True),
    ("Online PvP", "game.players.online_pvp", lambda c: _get(c, "game.players.online_pvp") is True),
]


def store_categories_match_config(ctx: CheckContext) -> CheckResult:
    claimed = {c.lower() for c in _get(ctx, "store.categories") or []}
    problems, warnings, fields = [], [], ["store.categories"]
    for category, path, configured in CATEGORY_CHECKS:
        has = configured(ctx)
        if category.lower() in claimed and not has:
            problems.append(f'"{category}" is claimed but {path} is not set up')
            fields.append(path)
        elif has and category.lower() not in claimed:
            warnings.append(f'{path} is set up but the store page does not list "{category}"')
    facts = scan_facts(ctx.root)
    in_code = set(facts.get("code_achievements") or [])
    defined = {a["id"] for a in _get(ctx, "achievements") or []}
    if in_code - defined:
        problems.append(f"code unlocks achievements that are not defined: {', '.join(sorted(in_code - defined))}")
    if facts and defined - in_code and facts.get("steam_sdk"):
        warnings.append(f"defined but never unlocked in code: {', '.join(sorted(defined - in_code))}")
    if problems:
        return CheckResult("fail", "; ".join(problems + warnings) + ".", fields)
    if warnings:
        return CheckResult("warn", "; ".join(warnings) + ".", fields)
    return CheckResult("pass")


def store_translations_complete(ctx: CheckContext) -> CheckResult:
    from steamworks_mcp.localization.store import status  # local import: localization imports validate

    problems = []
    for st in status(ctx.values, ctx.root):
        store_keys = [k for k in st.missing + st.stale if k.startswith(("store.", "release.early_access_answers."))]
        if store_keys:
            stale = [k for k in store_keys if k in st.stale]
            problems.append(
                f"{st.language}: {len(store_keys)} store text(s) "
                + ("stale" if stale and len(stale) == len(store_keys) else "missing or stale")
            )
    if problems:
        return CheckResult(
            "fail", "; ".join(problems[:10]) + ". Use localization_pending / localization_set.", ["target_languages"]
        )
    return CheckResult("pass")


def branches_password_protected(ctx: CheckContext) -> CheckResult:
    open_branches = [b["name"] for b in _get(ctx, "apps.main.builds.branches") or [] if not b.get("password_protected")]
    if open_branches:
        return CheckResult(
            "warn",
            f"Branches without a password: {', '.join(open_branches)}. Anyone can opt in to them.",
            ["apps.main.builds.branches"],
        )
    return CheckResult("pass")


def release_date_lock(ctx: CheckContext) -> CheckResult:
    planned = _date(_get(ctx, "release.planned_date"))
    if planned is None:
        return CheckResult("unknown", "No planned release date yet.", ["release.planned_date"])
    days = (planned - ctx.today).days
    if days < 0:
        return CheckResult("warn", f"The planned date {planned} is in the past.", ["release.planned_date"])
    if days <= 14:
        return CheckResult(
            "warn",
            f"{days} days to release: the date can no longer be changed in Steamworks.",
            ["release.planned_date"],
        )
    return CheckResult("pass", f"{days} days to release; the date stays editable until two weeks before.")


def weekday_release(ctx: CheckContext) -> CheckResult:
    planned = _date(_get(ctx, "release.planned_date"))
    if planned is None:
        return CheckResult("unknown", "No planned release date yet.", ["release.planned_date"])
    if planned.weekday() >= 5:
        return CheckResult(
            "warn",
            f"{planned} is a {planned.strftime('%A')}; Valve advises a weekday release.",
            ["release.planned_date"],
        )
    return CheckResult("pass")


def next_fest_timing(ctx: CheckContext) -> CheckResult:
    data = events()
    by_id = {e.id: e for e in data.events}
    problems, warnings = [], []
    if (ctx.today - data.last_verified).days > data.stale_after_days:
        warnings.append(f"the event calendar was last verified on {data.last_verified}; refresh data/events.yaml")
    planned = _date(_get(ctx, "release.planned_date"))
    cs = _date(_get(ctx, "release.coming_soon_since"))
    for eid in _get(ctx, "release.events") or []:
        ev = by_id.get(eid)
        if ev is None:
            problems.append(f'unknown event "{eid}"')
            continue
        if ev.kind != "next_fest":
            continue
        start, end = _date(ev.starts and str(ev.starts)[:10]), _date(ev.ends and str(ev.ends)[:10])
        deadline = _date(ev.registration_deadline and str(ev.registration_deadline)[:10])
        if start is None or end is None:
            warnings.append(f"{ev.name}: dates not announced yet")
            continue
        if planned and planned <= end:
            problems.append(f"{ev.name}: the game must not release before the fest ends ({end})")
        if deadline and deadline < ctx.today:
            problems.append(f"{ev.name}: registration closed on {deadline}")
        if not cs or cs >= start:
            warnings.append(f"{ev.name}: the store page must be public (Coming Soon) before {start}")
        if not _get(ctx, "apps.demo.appid"):
            warnings.append(f"{ev.name}: a playable demo must be live when the fest starts (no demo app yet)")
    if problems:
        return CheckResult("fail", "; ".join(problems + warnings) + ".", ["release.events", "release.planned_date"])
    if warnings:
        return CheckResult("warn", "; ".join(warnings) + ".", ["release.events"])
    return CheckResult("pass")


def autocloud_override_root_all_os(ctx: CheckContext) -> CheckResult:
    paths = _get(ctx, "apps.main.cloud.auto_cloud") or []
    bad = sorted(
        {
            o["root"]
            for o in _get(ctx, "apps.main.cloud.overrides") or []
            if any(p["root"] == o["root"] and p.get("os", "all") != "all" for p in paths)
        }
    )
    if bad:
        return CheckResult(
            "fail", f"Roots with overrides must be set for all OSes: {', '.join(bad)}.", ["apps.main.cloud.auto_cloud"]
        )
    return CheckResult("pass")


CHECKS: dict[str, Callable[[CheckContext], CheckResult]] = {
    "ai_disclosure_complete": ai_disclosure_complete,
    "sysreqs_per_platform": sysreqs_per_platform,
    "depot_per_platform": depot_per_platform,
    "store_categories_match_config": store_categories_match_config,
    "store_translations_complete": store_translations_complete,
    "branches_password_protected": branches_password_protected,
    "release_date_lock": release_date_lock,
    "weekday_release": weekday_release,
    "next_fest_timing": next_fest_timing,
    "autocloud_override_root_all_os": autocloud_override_root_all_os,
}
