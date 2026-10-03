"""Genre style guides (``data/style_guides/<id>.md``): a machine-readable front-matter block (genre overrides of the
base rubric) followed by guidance written for people and for the model. The files are edited by hand."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML


class Range(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None


class StyleGuideMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    status: str = "draft"
    applies_to_tags: list[str]
    references: list[int] = Field(default_factory=list)
    short_description: dict[str, Any] = Field(default_factory=dict)
    long_description: dict[str, Any] = Field(default_factory=dict)
    achievements: dict[str, Any] = Field(default_factory=dict)
    store: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class StyleGuide:
    meta: StyleGuideMeta
    body: str


def _dir() -> resources.abc.Traversable:
    return resources.files("steamworks_mcp.data").joinpath("style_guides")


def parse(text: str) -> StyleGuide:
    if not text.startswith("---"):
        raise ValueError("a style guide starts with a '---' front-matter block")
    _, front, body = text.split("---", 2)
    return StyleGuide(StyleGuideMeta.model_validate(YAML(typ="safe").load(front)), body.strip() + "\n")


@cache
def all_guides() -> tuple[StyleGuide, ...]:
    files = sorted((p for p in _dir().iterdir() if p.name.endswith(".md") and not p.name.startswith("_")), key=str)
    return tuple(parse(p.read_text(encoding="utf-8")) for p in files)


def guide(guide_id: str) -> StyleGuide | None:
    return next((g for g in all_guides() if g.meta.id == guide_id), None)


def for_tags(tags: list[str]) -> StyleGuide | None:
    """The guide sharing the most tags with the game, if any."""
    scored = [(len(set(tags) & set(g.meta.applies_to_tags)), g) for g in all_guides()]
    best = max(scored, key=lambda x: x[0], default=(0, None))
    return best[1] if best[0] > 0 else None
