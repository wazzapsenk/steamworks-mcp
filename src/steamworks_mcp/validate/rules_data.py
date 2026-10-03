"""Schema of the bundled rule data: Valve store rules (``data/store_rules.yaml``), asset specs
(``data/asset_specs.yaml``) and the event calendar (``data/events.yaml``).

Store rules are layer 1 of the store-text checks: a broken Valve rule is an error, a Valve recommendation a warning.
``check`` says how a rule is verified:

* ``deterministic`` - code, with ``kind`` + ``params`` (see ``DETERMINISTIC_KINDS``)
* ``llm_judged``    - the host model judges it from a brief; results are reported separately
* ``manual``        - a person has to look (mostly images)
* ``info``          - background knowledge, never a finding
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DETERMINISTIC_KINDS = {
    "max_length": "params: max (characters), per language",
    "plain_text": "no BBCode tags, no HTML, no line breaks",
    "regex_forbidden": "params: patterns (case-insensitive), any match is a finding",
    "no_urls": "no http(s)/www addresses, bare domains, or [url] tags",
    "forbidden_bbcode": "params: tags",
    "allowed_bbcode": "params: tags; any other tag is a finding",
    "english_present": "the English version of the text is not empty",
    "min_items": "params: min",
    "max_items": "params: max",
    "media_max_bytes": "params: max_bytes, per embedded image/video file",
    "media_total_max_bytes": "params: max_bytes, all embedded media of one text",
    "media_formats": "params: formats (file extensions)",
    "media_max_duration": "params: seconds, per animation",
    "media_max_width": "params: max_width (pixels)",
}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoreRule(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    area: Literal[
        "text",
        "bbcode",
        "assets",
        "screenshots",
        "trailers",
        "tags",
        "languages",
        "reviews",
        "events",
        "cloud",
        "achievements",
    ]
    applies_to: list[str]
    """What it applies to: short_description, about_this_game, header_capsule, screenshots, …"""
    check: Literal["deterministic", "llm_judged", "manual", "info"]
    kind: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"]
    rule: str
    source_doc: str
    quote: str | None = None
    unverified: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def _kind(self) -> StoreRule:
        if self.check == "deterministic" and self.kind not in DETERMINISTIC_KINDS:
            raise ValueError(f"{self.id}: deterministic rules need a known kind, got {self.kind!r}")
        if self.check != "deterministic" and self.kind is not None:
            raise ValueError(f"{self.id}: only deterministic rules have a kind")
        if self.quote and len(self.quote.split()) > 25:
            raise ValueError(f"{self.id}: quote longer than 25 words")
        return self


class StoreRulesFile(Model):
    last_reviewed: str
    rules: list[StoreRule]


class AssetSpec(Model):
    id: str
    label: str
    group: Literal["store", "library", "icon", "event", "achievement"]
    width: int | None = None
    height: int | None = None
    sizes: list[str] = Field(default_factory=list)
    """Alternative accepted sizes, e.g. ["256x256", "512x512"]."""
    fit: Literal["exact", "width_or_height", "minimum"] = "exact"
    formats: list[str]
    required: bool
    gate: int | None = None
    composition: Literal["capsule", "art_only", "logo_only", "icon"] | None = None
    """How this tool derives it from key art + logo (never generated artwork)."""
    logo_scale: float | None = None
    safe_area: dict[str, int] | None = None
    notes: str | None = None
    source_doc: str
    unverified: bool = False


class MediaSpec(Model):
    id: str
    params: dict[str, Any]
    source_doc: str
    unverified: bool = False
    notes: str | None = None


class AssetSpecsFile(Model):
    last_reviewed: str
    assets: list[AssetSpec]
    media: list[MediaSpec]
    previous_sizes: dict[str, str] = Field(default_factory=dict)
    """Sizes from before the August 2024 change; images at these sizes are rejected."""


When = dt.datetime | dt.date
"""Valve gives most event times with a Pacific-time offset; sales are often date-only."""


class EventRequirement(Model):
    type: Literal["milestone", "deadline", "eligibility", "requirement"]
    rule: str
    date: When | None = None
    quote: str | None = None


class SteamEvent(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    kind: Literal["next_fest", "sale", "fest"]
    starts: When | None = None
    ends: When | None = None
    registration_deadline: When | None = None
    registration_url: str | None = None
    requirements: list[EventRequirement] = Field(default_factory=list)
    source: str
    last_verified: dt.date
    quote: str | None = None
    unverified: bool = False
    notes: str | None = None


class PlanningRule(Model):
    id: str
    rule: str
    value: Any = None
    source: str
    quote: str | None = None
    unverified: bool = False
    notes: str | None = None
    conflict: str | None = None
    """Another Valve page that says something different, with its quote."""


class EventsFile(Model):
    last_verified: dt.date
    stale_after_days: int = Field(ge=1)
    """Older data makes the event checks warn that the calendar needs refreshing."""
    events: list[SteamEvent]
    discount_rules: list[PlanningRule]
    planning_rules: list[PlanningRule]
