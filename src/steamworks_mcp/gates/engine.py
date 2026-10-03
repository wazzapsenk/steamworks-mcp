"""Evaluate the gate rules against a project (values + state + files on disk)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from steamworks_mcp.capabilities import capabilities
from steamworks_mcp.data import data_files, load_yaml
from steamworks_mcp.gates.models import (
    Answered,
    AssetCheck,
    Checklist,
    Condition,
    Confirmed,
    Crosscheck,
    DateGap,
    GateFile,
    GateRule,
    Info,
    MinItems,
    Present,
    Range,
    Screenshots,
    StoreRules,
)
from steamworks_mcp.languages import is_api_code
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.state import ExecutionMode, State, is_empty
from steamworks_mcp.validate.crosschecks import CHECKS, CheckContext
from steamworks_mcp.validate.rules_data import AssetSpec, AssetSpecsFile
from steamworks_mcp.validate.store_text import check_store_text

RuleStatus = Literal["pass", "fail", "warn", "review", "todo", "done", "unknown", "not_applicable", "info"]
IMAGE_EXT = (".png", ".jpg", ".jpeg")
CHECKLIST_PREFIX = "checklist."


@dataclass
class RuleResult:
    rule: GateRule
    gate: int
    status: RuleStatus
    mode: ExecutionMode
    message: str = ""
    missing: list[str] = field(default_factory=list)
    needs_approval: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.rule.severity == "required" and self.status in ("fail", "review", "todo", "unknown")

    def as_dict(self) -> dict[str, Any]:
        r = self.rule
        out: dict[str, Any] = {
            "id": r.id,
            "title": r.title,
            "status": self.status,
            "severity": r.severity,
            "mode": self.mode,
        }
        for key, value in (
            ("message", self.message),
            ("missing", self.missing),
            ("needs_approval", self.needs_approval),
        ):
            if value:
                out[key] = value
        if self.status in ("fail", "todo", "unknown", "review", "warn"):
            out["what"] = r.description
            if r.where:
                out["where"] = r.where
            if r.rule:
                out["rule"] = r.rule
        if r.source_doc:
            out["source"] = r.source_doc
        if r.unverified:
            out["unverified"] = True
        return out


@cache
def gate_files() -> tuple[GateFile, ...]:
    return tuple(GateFile.model_validate(load_yaml(f)) for f in data_files("gates"))


@cache
def asset_specs() -> dict[str, AssetSpec]:
    return {a.id: a for a in AssetSpecsFile.model_validate(load_yaml("asset_specs.yaml")).assets}


def checklist_path(rule_id: str) -> str:
    return CHECKLIST_PREFIX + rule_id


# ---------------------------------------------------------------------------------------------------- helpers


def _expand(values: dict[str, Any], path: str) -> list[str]:
    """Concrete tracked paths for a pattern (or the path itself)."""
    if "*" not in path:
        return [path]
    return [p for p, _ in fp.iter_fields(values) if fp.matches(path, p)]


def _holds(c: Condition, values: dict[str, Any]) -> bool:
    v = fp.get(values, c.field)
    match c.op:
        case "is_true":
            return v is True
        case "is_not_true":
            return v is not True
        case "empty":
            return is_empty(v)
        case "not_empty":
            return not is_empty(v)
        case "contains":
            return isinstance(v, (list, dict)) and c.value in v
        case "not_contains":
            return not (isinstance(v, (list, dict)) and c.value in v)
        case "equals":
            return bool(v == c.value)
    return False


def _date(v: Any) -> dt.date | None:
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _business_days(a: dt.date, b: dt.date) -> int:
    days, step = 0, a
    while step < b:
        step += dt.timedelta(days=1)
        if step.weekday() < 5:
            days += 1
    return days


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, ValueError):
        return None


def _fits(spec: AssetSpec, size: tuple[int, int]) -> bool:
    w, h = size
    if spec.sizes:
        return f"{w}x{h}" in spec.sizes
    if spec.fit == "width_or_height":
        return (w == spec.width and h <= (spec.height or h)) or (h == spec.height and w <= (spec.width or w))
    if spec.fit == "minimum":
        return w >= (spec.width or 0) and h >= (spec.height or 0)
    return (w, h) == (spec.width, spec.height)


# ---------------------------------------------------------------------------------------------------- evaluation


class Evaluator:
    def __init__(
        self, values: dict[str, Any], state: State, root: Path, *, browser: bool = False, today: dt.date | None = None
    ):
        self.values = values
        self.state = state
        self.root = root
        self.browser = browser
        self.today = today or dt.date.today()
        self.ctx = CheckContext(values, root, self.today)
        self._store_findings: list[Any] | None = None

    def mode(self, rule: GateRule) -> ExecutionMode:
        if rule.execution_mode == "BROWSER" and not self.browser:
            return rule.fallback_mode or "MANUAL"
        return rule.execution_mode

    def _approval(self, paths: list[str]) -> list[str]:
        out = []
        for p in paths:
            fs = self.state.fields.get(p)
            if fs is not None and fs.status in ("draft", "needs_review") and not is_empty(fp.get(self.values, p)):
                out.append(p)
        return out

    def evaluate(self, rule: GateRule, gate: int) -> RuleResult:
        res = RuleResult(rule, gate, "pass", self.mode(rule))
        if not all(_holds(c, self.values) for c in rule.when):
            res.status = "not_applicable"
            return res
        c = rule.check
        if isinstance(c, Info):
            res.status = "info"
        elif isinstance(c, Checklist):
            fs = self.state.fields.get(checklist_path(rule.id))
            res.status = "done" if fs is not None and fs.status == "applied" else "todo"
            res.fields = [checklist_path(rule.id)]
        elif isinstance(c, (Present, Answered, Confirmed)):
            paths = [p for f in c.fields for p in _expand(self.values, f)]
            res.fields = paths
            for p in paths:
                v = fp.get(self.values, p)
                if (isinstance(c, Present) and is_empty(v)) or (isinstance(c, Answered) and v is None):
                    res.missing.append(p)
                elif isinstance(c, Confirmed) and v is not True:
                    res.missing.append(p)
                    if v is False:
                        res.message = f"{p} is answered 'no'."
        elif isinstance(c, MinItems):
            v = fp.get(self.values, c.field)
            res.fields = [c.field]
            n = len(v) if isinstance(v, (list, dict)) else 0
            if n < c.min:
                res.missing.append(c.field)
                res.message = f"{n} of at least {c.min}."
        elif isinstance(c, Range):
            v = fp.get(self.values, c.field)
            res.fields = [c.field]
            if v is None:
                res.missing.append(c.field)
            else:
                num = float(Decimal(str(v)))
                if (c.min is not None and num < c.min) or (c.max is not None and num > c.max):
                    res.status, res.message = (
                        "fail",
                        f"{v} is outside {c.min if c.min is not None else '…'}-{c.max if c.max is not None else '…'}.",
                    )
        elif isinstance(c, DateGap):
            res.fields = [p for p in (c.from_, c.to) if p != "today"]
            start = self.today if c.from_ == "today" else _date(fp.get(self.values, c.from_))
            end = _date(fp.get(self.values, c.to))
            res.missing = [p for p, d in ((c.from_, start), (c.to, end)) if p != "today" and d is None]
            if start and end:
                gap = _business_days(start, end) if c.business_days else (end - start).days
                if gap < c.min_days:
                    res.status, res.message = "fail", f"{gap} days; at least {c.min_days} needed."
        elif isinstance(c, AssetCheck):
            self._asset(c.asset, res)
        elif isinstance(c, Screenshots):
            self._screenshots(c, res)
        elif isinstance(c, StoreRules):
            self._store_rules(c, res)
        elif isinstance(c, Crosscheck):
            out = CHECKS[c.name](self.ctx)
            res.status = "pass" if out.status == "pass" else out.status
            res.message, res.fields = out.message, out.fields
        if res.missing and res.status == "pass":
            res.status = "fail"
        if res.status == "pass" and (pending := self._approval(res.fields)):
            res.status, res.needs_approval = "review", pending
        if res.status == "fail" and rule.severity != "required":
            res.status = "warn"
        return res

    def _asset(self, asset_id: str, res: RuleResult) -> None:
        spec = asset_specs()[asset_id]
        override = fp.get(self.values, f"assets.overrides.{asset_id}")
        if override:
            res.fields = [f"assets.overrides.{asset_id}"]
            size = _image_size(self.root / override)
            if size is None:
                res.status, res.message = "fail", f"{override} is missing or not an image."
            elif not _fits(spec, size):
                res.status, res.message = (
                    "fail",
                    f"{override} is {size[0]}x{size[1]}; {spec.label} needs {spec.width}x{spec.height}.",
                )
            return
        need = {
            "capsule": ["assets.key_art", "assets.logo"],
            "art_only": ["assets.key_art"],
            "logo_only": ["assets.logo"],
            "icon": ["assets.logo"],
        }.get(spec.composition or "", [])
        res.fields = need
        missing = [
            p
            for p in need
            if is_empty(fp.get(self.values, p)) or not (self.root / str(fp.get(self.values, p))).is_file()
        ]
        if not need or missing:
            res.missing = missing or [f"assets.overrides.{asset_id}"]
            what = " and ".join(missing) if missing else "a hand-made image"
            res.message = f"{spec.label}: provide {what} (or assets.overrides.{asset_id})."
        else:
            res.status, res.message = "todo", f"{spec.label} will be derived from your art by prepare_images."

    def _screenshots(self, c: Screenshots, res: RuleResult) -> None:
        folder = self.root / str(fp.get(self.values, "assets.screenshots_dir") or "store/screenshots")
        res.fields = ["assets.screenshots_dir"]
        files = sorted(p for p in folder.glob("*") if p.suffix.lower() in IMAGE_EXT) if folder.is_dir() else []
        # Localized variants (shot1_japanese.png) replace a base screenshot in one language; they do not count.
        files = [p for p in files if not ("_" in p.stem and is_api_code(p.stem.rsplit("_", 1)[1]))]
        good, small = 0, []
        for p in files:
            size = _image_size(p)
            if size and size[0] >= c.min_width and size[1] >= c.min_height and abs(size[0] / size[1] - 16 / 9) < 0.02:
                good += 1
            else:
                small.append(p.name)
        if good < c.min_count:
            res.status = "fail"
            res.message = (
                f"{good} usable screenshot(s) in {folder.name}/, {c.min_count} needed "
                f"(min {c.min_width}x{c.min_height}, 16:9)."
            )
            if small:
                res.message += f" Not usable: {', '.join(small[:5])}."

    def _store_rules(self, c: StoreRules, res: RuleResult) -> None:
        texts = {
            str(self.values.get("source_language") or "english"): {
                "store.short_description": fp.get(self.values, "store.short_description") or "",
                "store.about": fp.get(self.values, "store.about") or "",
            }
        }
        res.fields = ["store.short_description", "store.about"]
        findings = check_store_text(texts, c.rules or None)
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        if errors or warnings:
            res.status = "fail" if errors else "warn"
            res.message = " ".join(f"[{f.rule_id}] {f.field}: {f.message}" for f in (errors + warnings)[:6])


def evaluate_gates(
    values: dict[str, Any],
    state: State,
    root: Path,
    gates: list[int] | None = None,
    *,
    browser: bool = False,
    today: dt.date | None = None,
) -> list[RuleResult]:
    ev = Evaluator(values, state, root, browser=browser, today=today)
    return [ev.evaluate(rule, g.gate) for g in gate_files() if gates is None or g.gate in gates for rule in g.rules]


def capability_mode(area: str, browser: bool) -> ExecutionMode:
    return next(a for a in capabilities().areas if a.id == area).mode(browser)
