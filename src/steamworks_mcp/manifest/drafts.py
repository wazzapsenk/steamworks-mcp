"""Text alternatives, ``.steam-mcp/drafts/<field path>/<id>.json``.

Generators and reviews never overwrite a value: they store candidates here (with the strategy that produced them and
their rubric results). The user picks one with ``set_field(path, from_draft=id)``. Drafts are committed with the
game project so a team can compare them.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from steamworks_mcp.manifest.state import Source, now

DRAFT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class RubricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    check: Literal["deterministic", "llm_judged"]
    outcome: Literal["pass", "fail", "warn", "not_applicable"]
    message: str = ""
    fix: str = ""
    """A concrete suggestion when the rule is not met."""


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=DRAFT_ID.pattern)
    field: str
    """Field path this draft is for, e.g. ``store.short_description``."""
    value: Any
    strategy: str | None = None
    """How it was produced, e.g. ``fantasy``, ``mechanic``, ``situation_humor``, ``outline``, ``revision``."""
    source: Source = "generated"
    created_at: dt.datetime = Field(default_factory=now)
    rubric_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rubric: list[RubricResult] = Field(default_factory=list)
    based_on: str | None = None
    """Draft id or value hash this one revises."""
    status: Literal["candidate", "chosen", "rejected"] = "candidate"
    notes: str = ""
