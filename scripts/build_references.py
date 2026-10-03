"""Fetch the catalog's reference games (into the local cache) and write their derived analysis into the package.

    uv run python scripts/build_references.py            # all catalog games
    uv run python scripts/build_references.py 3527290    # one game
    uv run python scripts/build_references.py --refresh  # ignore the cache

Only derived measurements are written to src/steamworks_mcp/data/references/analysis/. Raw store texts stay in the
cache (default ~/.steamworks-mcp/cache/references, or $STEAMWORKS_MCP_CACHE). Set STEAM_WEB_API_KEY to include
hidden-achievement shares.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from steamworks_mcp.references import catalog, default_cache_dir
from steamworks_mcp.references.analyze import analyze
from steamworks_mcp.references.anticopy import find_overlaps
from steamworks_mcp.references.fetch import ReferenceFetcher

OUT = Path(__file__).resolve().parents[1] / "src" / "steamworks_mcp" / "data" / "references" / "analysis"


def main() -> int:
    refresh = "--refresh" in sys.argv
    wanted = {int(a) for a in sys.argv[1:] if a.isdigit()}
    fetcher = ReferenceFetcher(default_cache_dir(), web_api_key=os.environ.get("STEAM_WEB_API_KEY") or None)
    OUT.mkdir(parents=True, exist_ok=True)
    for game in catalog():
        if wanted and game.appid not in wanted:
            continue
        details = fetcher.appdetails(game.appid, refresh=refresh)
        pct = fetcher.achievement_percentages(game.appid, refresh=refresh)
        texts = fetcher.achievement_texts(game.appid, refresh=refresh)
        schema = fetcher.schema(game.appid, refresh=refresh)
        result = analyze(
            game.appid,
            details.data,
            pct.data,
            texts.data,
            schema.data if schema else None,
            details.fetched_at.date(),
            game.tags,
        )
        text = result.model_dump_json(indent=2) + "\n"
        # Belt and braces: the committed analysis must not carry any run of the game's own texts.
        raw = {
            "short": details.data.get("short_description", ""),
            "about": details.data.get("about_the_game", ""),
            **{f"ach{i}": f"{t['name']} {t['description']}" for i, t in enumerate(texts.data)},
        }
        leaks = find_overlaps(result.model_dump_json(exclude={"name"}), raw, min_run=4)  # the name itself is public
        if leaks:
            print(f"{game.name}: analysis repeats source text ({leaks[0].words}); not written")
            return 1
        (OUT / f"{game.appid}.json").write_text(text, encoding="utf-8", newline="\n")
        s, a = result.store, result.achievements
        print(
            f"{game.name}: short {s.short_description.chars} chars, about {s.about.words} words / "
            f"{s.about.paragraphs} paragraphs / {s.about.animations} animations, {s.screenshots} screenshots, "
            f"{a.count} achievements ({a.api_name_style})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
