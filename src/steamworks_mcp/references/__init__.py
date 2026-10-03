"""Reference games: catalog, public-data fetcher (local cache), derived analysis and the anti-copy check."""

from __future__ import annotations

import json
import os
from functools import cache
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from steamworks_mcp.references.analyze import ReferenceAnalysis


class CatalogGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appid: int
    name: str
    released: str
    tags: list[str]


def _data() -> resources.abc.Traversable:
    return resources.files("steamworks_mcp.data").joinpath("references")


@cache
def catalog() -> tuple[CatalogGame, ...]:
    raw = json.loads(_data().joinpath("games.json").read_text(encoding="utf-8"))
    return tuple(CatalogGame.model_validate(g) for g in raw["games"])


def matching(tags: list[str], limit: int = 3) -> list[CatalogGame]:
    """Catalog games that share the most tags with ``tags`` (at least one), best first."""
    wanted = set(tags)
    scored = [(len(wanted & set(g.tags)), g) for g in catalog()]
    return [g for n, g in sorted(scored, key=lambda x: -x[0]) if n > 0][:limit]


def bundled_analysis(appid: int) -> ReferenceAnalysis | None:
    f = _data().joinpath("analysis", f"{appid}.json")
    if not f.is_file():
        return None
    return ReferenceAnalysis.model_validate_json(f.read_text(encoding="utf-8"))


def default_cache_dir() -> Path:
    """Where raw reference data is cached on this machine (never inside a repository)."""
    base = os.environ.get("STEAMWORKS_MCP_CACHE")
    return (Path(base) if base else Path.home() / ".steamworks-mcp" / "cache") / "references"
