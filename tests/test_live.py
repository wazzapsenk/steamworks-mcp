"""Live market numbers and the review study, against a fake Steam store (no network)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from steamworks_mcp import present
from steamworks_mcp.generate import text as gen_text
from steamworks_mcp.manifest.io import ManifestFile, ProjectFiles
from steamworks_mcp.references import live
from steamworks_mcp.references import reviews as review_study
from steamworks_mcp.references.fetch import ReferenceFetcher

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "example-game"


def details(appid: int, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "developers": ["Dev"],
        "publishers": ["Pub"],
        "release_date": {"coming_soon": False, "date": "Mar 1, 2025"},
        "is_free": False,
        "genres": [{"description": "Action"}, {"description": "Indie"}],
        "categories": [
            {"description": "Single-player"},
            {"description": "Online Co-op"},
            {"description": "Steam Cloud"},
        ],
        "platforms": {"windows": True, "mac": False, "linux": True},
        "supported_languages": "English<strong>*</strong>, French, German"
        "<br><strong>*</strong>languages with full audio support",
        "achievements": {"total": 30},
        **extra,
    }


DETAILS = {
    1: details(1, "Fort Brawl"),
    2: details(2, "Pillow Siege"),
    3: details(3, "Blanket Wars", is_free=True),
}
PRICES = {  # country -> appid -> (currency, full cents)
    "us": {1: ("USD", 1999), 2: ("USD", 999), 3: None},
    "tr": {1: ("USD", 799), 2: ("USD", 399), 3: None},
    "de": {1: ("EUR", 1999), 2: ("EUR", 999), 3: None},
}
REVIEWS = {1: (900, 100), 2: (300, 100), 3: (50, 50)}
PLAYERS = {1: 1200, 2: 40, 3: 7}
TEXTS = {
    "positive": "Couch co-op with friends is pure chaos and the pillow physics never get old after many hours",
    "negative": "Online matchmaking keeps dropping me from lobbies and the servers lag every single evening",
}


@pytest.fixture
def requests() -> list[httpx.Request]:
    return []


@pytest.fixture
def fetcher(tmp_path: Path, requests: list[httpx.Request]) -> ReferenceFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if "storesearch" in url:
            items = [{"type": "app", "id": a, "name": d["name"]} for a, d in DETAILS.items()]
            items.insert(0, {"type": "app", "id": 9, "name": "Fort Brawl Soundtrack"})
            return httpx.Response(200, json={"total": 4, "items": items})
        if "appdetails" in url and q.get("filters") == "price_overview":
            out: dict[str, Any] = {}
            for a in q["appids"].split(","):
                p = PRICES[q["cc"]][int(a)]
                overview = {"currency": p[0], "initial": p[1], "final": p[1], "discount_percent": 0} if p else None
                out[a] = {"success": True, "data": {"price_overview": overview} if overview else []}
            return httpx.Response(200, json=out)
        if "appdetails" in url:
            return httpx.Response(200, json={q["appids"]: {"success": True, "data": DETAILS[int(q["appids"])]}})
        if "appreviews" in url:
            appid = int(urlparse(url).path.rsplit("/", 1)[1])
            pos, neg = REVIEWS[appid]
            body: dict[str, Any] = {
                "success": 1,
                "query_summary": {
                    "review_score_desc": "Very Positive",
                    "total_positive": pos,
                    "total_negative": neg,
                    "total_reviews": pos + neg,
                },
            }
            if q.get("num_per_page") != "0":
                kind = q["review_type"]
                body["reviews"] = [
                    {
                        "recommendationid": str(i),
                        "author": {"steamid": "76561190000000001", "personaname": "someone", "playtime_at_review": 600},
                        "voted_up": kind == "positive",
                        "votes_up": 3,
                        "timestamp_created": 1700000000,
                        "language": "english",
                        "review": f"{TEXTS[kind]} ({i})",
                    }
                    for i in range(int(q["num_per_page"]))
                ]
            return httpx.Response(200, json=body)
        if "GetNumberOfCurrentPlayers" in url:
            return httpx.Response(200, json={"response": {"player_count": PLAYERS[int(q["appid"])], "result": 1}})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ReferenceFetcher(tmp_path / "cache", client=client, sleep=lambda _: None)


@pytest.fixture
def game(tmp_path: Path) -> Path:
    shutil.copytree(EXAMPLE, tmp_path / "game")
    return tmp_path / "game"


def test_lookup_by_name_prefers_the_exact_match(fetcher: ReferenceFetcher) -> None:
    found = live.lookup(fetcher, "fort brawl")
    g = found["game"]
    assert g["appid"] == 1 and g["price_usd"] == {"full": 19.99, "now": 19.99, "discount_percent": 0}
    assert g["reviews"] == {"score": "Very Positive", "total": 1000, "positive_share": 0.9}
    assert g["players_now"] == 1200 and g["modes"] == ["Single-player", "Online Co-op"]
    assert g["features"] == ["Steam Cloud"] and g["platforms"] == ["windows", "linux"] and g["languages"] == 3
    assert {m["appid"] for m in found["other_matches"]} == {9, 2, 3}
    shown = present.store_lookup(found)
    assert shown["summary"] == "Fort Brawl: $19.99, Very Positive (1,000 reviews, 90% positive), 1,200 playing now."
    assert "| Playing now | 1,200 |" in shown["display"]


def test_live_numbers_are_cached(fetcher: ReferenceFetcher, requests: list[httpx.Request]) -> None:
    live.lookup(fetcher, "2")
    before = len(requests)
    live.lookup(fetcher, "https://store.steampowered.com/app/2/Pillow_Siege/")
    assert len(requests) == before


def test_compare_overview(fetcher: ReferenceFetcher) -> None:
    out = live.compare(fetcher, [1, 2, 3])
    o = out["overview"]
    assert o["median_price_usd"] == 14.99 and o["free_games"] == 1 and o["median_reviews"] == 400
    assert o["modes"]["Online Co-op"] == 1.0
    assert present.compare_games(out)["summary"].startswith("Compared 3 games: median price $14.99")


def test_price_brief_turns_the_base_price_into_regional_prices(fetcher: ReferenceFetcher, game: Path) -> None:
    values = ManifestFile.load(game / "steamworks.yaml").values()
    values["pricing"] = {"base_price_usd": 15}
    out = live.price_brief(fetcher, values, [1, 2, 3], ["tr", "de"])
    assert out["us_full_prices"]["median"] == 14.99 and out["us_full_prices"]["free_or_unavailable"] == 1
    regions = {r["country"]: r for r in out["regions"]}
    assert regions["tr"]["per_usd"] == 0.4 and regions["tr"]["for_base_price"] == 5.99
    assert regions["de"]["currency"] == "EUR" and regions["de"]["for_base_price"] == 15.0
    assert out["base_price_from"] == "pricing.base_price_usd"
    shown = present.price_brief(out)
    assert "| Turkey | USD | 0.4 | $5.99 | 2 |" in shown["display"]


def test_estimate_sales_lists_its_assumptions(fetcher: ReferenceFetcher) -> None:
    values = {"pricing": {"base_price_usd": 10, "launch_discount_percent": 20}}
    out = live.estimate_sales(fetcher, values, [1], wishlists=10000)
    assert out["games"][0]["copies"] == {"low": 20000, "typical": 30000, "high": 60000}
    own = out["this_game"]
    assert own["first_week_copies"] == {"low": 500, "typical": 1000, "high": 2000}
    assert own["first_week_gross_usd"]["typical"] == 8000 and own["first_week_after_steam_cut_usd"]["typical"] == 5600
    assert "not a forecast" in present.estimate_sales(out)["summary"]


def test_review_study_keeps_labels_not_reviews(fetcher: ReferenceFetcher, game: Path) -> None:
    files = ProjectFiles(game)
    started = review_study.start(fetcher, files, [1, 2, 3], per_kind=3)
    assert [g["appid"] for g in started["games"]] == [1, 2, 3]
    assert set(started["games"][0]["positive"][0]) == {"hours", "text"}
    cache = json.dumps([json.loads(p.read_text("utf-8")) for p in (fetcher.cache_dir / "1").glob("reviews_*.json")])
    assert "76561190000000001" not in cache and "someone" not in cache
    with pytest.raises(ValueError, match="unknown theme"):
        review_study.save(fetcher, files, [{"appid": 1, "praised": ["fun"], "insight": "Players love the chaos."}])
    with pytest.raises(ValueError, match="repeats a review"):
        review_study.save(
            fetcher,
            files,
            [{"appid": 1, "praised": ["co_op_friends"], "insight": "They say the pillow physics never get old."}],
        )
    notes = [
        {"appid": a, "praised": ["co_op_friends", "core_loop"], "criticized": ["multiplayer_online"],
         "insight": "Players come for chaotic sessions with friends and leave over unstable online play."}
        for a in (1, 2, 3)
    ]  # fmt: skip
    saved = review_study.save(fetcher, files, notes)
    assert saved["praised"][0] == {"theme": "co_op_friends", "share": 1.0}
    stored = (game / ".steam-mcp" / "market" / "reviews.json").read_text("utf-8")
    assert TEXTS["positive"][:30] not in stored
    brief = gen_text.brief(ManifestFile.load(game / "steamworks.yaml").values(), files, "store_short")
    assert (
        brief["players"]["status"] == "studied" and brief["players"]["criticized"][0]["theme"] == "multiplayer_online"
    )
    assert present.save_review_study(saved)["summary"].startswith("Review study saved: players praise co op friends")


def test_launch_watch_compares_with_the_last_check(fetcher: ReferenceFetcher, game: Path) -> None:
    files = ProjectFiles(game)
    values = {"apps": {"main": {"appid": 1}}}
    first = live.launch_watch(fetcher, values, files)
    assert first["checks_so_far"] == 1 and "since_last_check" not in first
    REVIEWS[1] = (950, 100)
    try:
        second = live.launch_watch(fetcher, values, files)
    finally:
        REVIEWS[1] = (900, 100)
    assert second["since_last_check"]["new_reviews"] == 50
    assert len((game / ".steam-mcp" / "market" / "launch.jsonl").read_text("utf-8").splitlines()) == 2
    assert "+50 reviews since the last check" in present.launch_watch(second)["summary"]


def test_peers_need_games_or_a_market_study(game: Path) -> None:
    with pytest.raises(ValueError, match="study_market"):
        live.peers(ProjectFiles(game), None)
    assert live.peers(None, [3, 3, 1]) == [3, 1]
