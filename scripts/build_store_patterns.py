"""Measure the store pages of Steam's popular new releases; write src/steamworks_mcp/data/store_patterns.json.

    uv run python scripts/build_store_patterns.py              # up to 80 games
    uv run python scripts/build_store_patterns.py --limit 40
    uv run python scripts/build_store_patterns.py --refresh    # ignore the cached store data

Public store endpoints only (no key): the "Popular New Releases" search list and /api/appdetails, at most one request
per second. Raw store data stays in the local reference cache (default ~/.steamworks-mcp/cache/references, or
$STEAMWORKS_MCP_CACHE). The data file holds derived measurements per Steam genre and overall, the recording date and
the sampled app ids; never text from the pages.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from steamworks_mcp.references import default_cache_dir
from steamworks_mcp.references.anticopy import find_overlaps
from steamworks_mcp.references.fetch import ReferenceFetcher
from steamworks_mcp.references.patterns import MAX_GAMES, build, sample

OUT = Path(__file__).resolve().parents[1] / "src" / "steamworks_mcp" / "data" / "store_patterns.json"


def main() -> int:
    refresh = "--refresh" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else MAX_GAMES
    fetcher = ReferenceFetcher(default_cache_dir(), min_interval=1.0)
    pages, raw = sample(fetcher, min(limit, MAX_GAMES), refresh=refresh)
    result = build(pages, dt.date.today())
    # Belt and braces: the committed numbers must not carry any run of the pages' own texts. The source line and
    # the app ids are public and left out of the check (a page may link to the Steam store).
    leaks = find_overlaps(result.model_dump_json(exclude={"source", "appids"}), raw, min_run=4)
    if leaks:
        print(f"the patterns repeat page text ({' '.join(leaks[0].words)}); not written")
        return 1
    OUT.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    o = result.overall
    print(
        f"{o.games} games, {len(result.genres)} genre groups ({', '.join(result.genres)}). Overall: short "
        f"{o.short_description.chars.median:.0f} chars, About {o.about.words.median:.0f} words, headers on "
        f"{o.about.with_headers_share:.0%} of pages, {o.media.screenshots.median:.0f} screenshots"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
