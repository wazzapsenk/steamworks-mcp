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


def cached_texts(cache_dir: Path, appids: list[int] | None = None) -> dict[str, str]:
    """Raw store texts and achievement texts of reference games found in the local cache (no network).

    Used only by the anti-copy check; these texts never leave the machine.
    """
    out: dict[str, str] = {}
    ids = appids if appids is not None else [g.appid for g in catalog()]
    for appid in ids:
        details = cache_dir / str(appid) / "appdetails.json"
        if details.exists():
            data = json.loads(details.read_text(encoding="utf-8")).get("data", {})
            out[f"{appid}:short"] = str(data.get("short_description", ""))
            out[f"{appid}:about"] = str(data.get("about_the_game") or data.get("detailed_description") or "")
        texts = cache_dir / str(appid) / "achievement_texts.json"
        if texts.exists():
            rows = json.loads(texts.read_text(encoding="utf-8")).get("data", [])
            out[f"{appid}:achievements"] = "\n".join(f"{r.get('name', '')}. {r.get('description', '')}" for r in rows)
    return out


def tags_for_game(values: dict[str, object]) -> list[str]:
    """Matching tags derived from the game's own genres and player modes (e.g. "co-op party" -> coop_party)."""
    import re

    game = values.get("game") if isinstance(values.get("game"), dict) else {}
    assert isinstance(game, dict)
    tags = set()
    for g in game.get("genres") or []:
        norm = re.sub(r"[^a-z0-9]+", "_", str(g).lower().replace("co-op", "coop")).strip("_")
        tags.add(norm)
        if "coop" in norm or "party" in norm:
            tags.add("coop_party")
    players = game.get("players") or {}
    if isinstance(players, dict):
        if players.get("online_coop"):
            tags.add("online_coop")
        if players.get("online_pvp"):
            tags.add("online_pvp")
    return sorted(tags)
