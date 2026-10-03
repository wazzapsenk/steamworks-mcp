"""Schema of the release-gate files, ``data/gates/gate_{0..3}.yaml``.

Each rule says *what* Steam needs at a gate, *how* it is checked against ``steamworks.yaml`` (``check``), *how*
it gets done in Steamworks (``execution_mode``), and *where Valve says so* (``source_doc`` + ``quote``). Rules that
come from this tool's own policy rather than from Valve have ``origin: tool``.

Check kinds:

* ``present``     every field (or pattern match) is non-empty
* ``any_present`` at least one of the fields is non-empty
* ``answered``    a yes/no field has been answered (true or false)
* ``confirmed``   a yes/no field is true (the user confirmed it)
* ``min_items``   a list/dict field has at least ``min`` entries
* ``range``       a number field lies within ``min``/``max``
* ``date_gap``    ``to`` is at least ``min_days`` after ``from`` (``from`` may be ``today``)
* ``asset``       an image asset exists at the right size (see ``data/asset_specs.yaml``)
* ``screenshots`` enough screenshots of the minimum size
* ``store_rules`` store text passes ``data/store_rules.yaml``
* ``crosscheck``  a named consistency check implemented in code
* ``checklist``   nothing to read in the values file: the user confirms it with ``mark_applied("checklist.<id>")``
* ``info``        guidance only, never blocks
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["required", "recommended", "optional"]
ExecutionMode = Literal["API", "BROWSER", "ARTIFACT", "MANUAL"]
AppKind = Literal["game", "demo", "playtest"]

RULE_ID = re.compile(r"^[a-z0-9_]+$")

CROSSCHECKS = {
    "ai_disclosure_complete": "If AI is used, the pre-/live-generated descriptions (and guardrails) are filled.",
    "sysreqs_per_platform": "Every OS in store.platforms has minimum requirements; no requirements for other OSes.",
    "depot_per_platform": "Every OS in store.platforms has a depot (or an all-OS depot) and a launch option.",
    "store_categories_match_config": "Categories claimed on the store page match what is configured and used in code.",
    "store_translations_complete": "Every target language has translations of the store texts, none stale.",
    "branches_password_protected": "Private beta branches have a password before a build is set live on them.",
    "release_date_lock": "Within two weeks of the planned date the date can no longer be changed in Steamworks.",
    "weekday_release": "The planned release date is not on a weekend.",
    "next_fest_timing": "A chosen Next Fest edition fits the release plan (not released before it ends, demo ready).",
    "autocloud_override_root_all_os": "A root that has overrides is set for all OSes, as Steamworks requires.",
}
"""Named crosschecks rules may use. Implementations live in the validators (one per name)."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(Model):
    """The rule only applies when this holds (all conditions of a rule must hold)."""

    field: str
    op: Literal["is_true", "is_not_true", "empty", "not_empty", "contains", "not_contains", "equals"]
    value: Any = None


class Present(Model):
    kind: Literal["present"]
    fields: list[str] = Field(min_length=1)


class AnyPresent(Model):
    kind: Literal["any_present"]
    fields: list[str] = Field(min_length=2)


class Answered(Model):
    kind: Literal["answered"]
    fields: list[str] = Field(min_length=1)


class Confirmed(Model):
    kind: Literal["confirmed"]
    fields: list[str] = Field(min_length=1)


class MinItems(Model):
    kind: Literal["min_items"]
    field: str
    min: int = Field(ge=1)


class Range(Model):
    kind: Literal["range"]
    field: str
    min: float | None = None
    max: float | None = None


class DateGap(Model):
    kind: Literal["date_gap"]
    from_: str = Field(alias="from")
    """A date field path, or ``today``."""
    to: str
    min_days: int = Field(ge=0)
    business_days: bool = False


class AssetCheck(Model):
    kind: Literal["asset"]
    asset: str


class Screenshots(Model):
    kind: Literal["screenshots"]
    min_count: int = Field(ge=1)
    min_width: int
    min_height: int
    aspect: str | None = None


class StoreRules(Model):
    kind: Literal["store_rules"]
    rules: list[str] = Field(default_factory=list)
    """Ids from data/store_rules.yaml; empty means every rule that applies."""


class Crosscheck(Model):
    kind: Literal["crosscheck"]
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class Checklist(Model):
    kind: Literal["checklist"]


class Info(Model):
    kind: Literal["info"]


Check = Annotated[
    Present
    | AnyPresent
    | Answered
    | Confirmed
    | MinItems
    | Range
    | DateGap
    | AssetCheck
    | Screenshots
    | StoreRules
    | Crosscheck
    | Checklist
    | Info,
    Field(discriminator="kind"),
]


class GateRule(Model):
    id: str = Field(pattern=RULE_ID.pattern)
    title: str
    description: str
    severity: Severity
    check: Check
    when: list[Condition] = Field(default_factory=list)
    applies_to: list[AppKind] = Field(default=["game"])
    execution_mode: ExecutionMode
    """How it gets done in Steamworks."""
    fallback_mode: ExecutionMode | None = None
    """Used when ``execution_mode`` is BROWSER and the opt-in browser mode is off."""
    where: str | None = None
    """Where in Steamworks (page / tab / button)."""
    rule: str | None = None
    """The concrete rule or value, in words."""
    origin: Literal["valve", "tool"] = "valve"
    source_doc: str | None = None
    quote: str | None = None
    """Verbatim supporting sentence from ``source_doc`` (at most 25 words)."""
    notes: str | None = None
    unverified: bool = False
    steamworks_checklist: str | None = None
    """The item of the release checklists on the app's Steamworks landing page this rule stands for, e.g.
    "Your Store Presence / Support Info"."""

    @model_validator(mode="after")
    def _sources(self) -> GateRule:
        if self.origin == "valve" and not self.source_doc:
            raise ValueError(f"{self.id}: Valve rules need a source_doc")
        if self.source_doc and not self.source_doc.startswith("https://partner.steamgames.com/"):
            raise ValueError(f"{self.id}: source_doc must be a partner.steamgames.com page")
        if self.quote and len(self.quote.split()) > 25:
            raise ValueError(f"{self.id}: quote longer than 25 words")
        if self.execution_mode == "BROWSER" and self.fallback_mode is None:
            raise ValueError(f"{self.id}: BROWSER rules need a fallback_mode")
        if isinstance(self.check, Crosscheck) and self.check.name not in CROSSCHECKS:
            raise ValueError(f"{self.id}: unknown crosscheck {self.check.name!r}")
        return self


class GateFile(Model):
    gate: int = Field(ge=0, le=3)
    title: str
    summary: str
    last_reviewed: str
    """Date the rules were last checked against Valve's docs (YYYY-MM-DD)."""
    rules: list[GateRule]

    def field_refs(self) -> list[str]:
        """Every field path or pattern the rules read (for schema consistency checks)."""
        refs: list[str] = []
        for r in self.rules:
            refs += [c.field for c in r.when]
            c = r.check
            if isinstance(c, (Present, AnyPresent, Answered, Confirmed)):
                refs += c.fields
            elif isinstance(c, (MinItems, Range)):
                refs.append(c.field)
            elif isinstance(c, DateGap):
                refs += [p for p in (c.from_, c.to) if p != "today"]
        return refs
