"""Market study: peers by store tags, labels from the model, the saved study and the briefs built on it.

Every sample text is written for a fictional game.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp import Client

from steamworks_mcp.config import Config
from steamworks_mcp.generate import text as gen_text
from steamworks_mcp.manifest.io import ManifestFile, ProjectFiles
from steamworks_mcp.references import market
from steamworks_mcp.references.anticopy import find_overlaps
from steamworks_mcp.references.fetch import ReferenceFetcher
from steamworks_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "example-game"
TODAY = dt.date(2026, 10, 3)
TAGS = [
    {"tagid": 492, "name": "Indie"},
    {"tagid": 1685, "name": "Co-op"},
    {"tagid": 3843, "name": "Online Co-Op"},
    {"tagid": 3955, "name": "Physics"},
]
SHORTS = {
    1: "Four raccoons, one dumpster, zero plans. Team up online and pull off heists across a sleeping city.",
    4: "Sail a leaky submarine with up to three friends and patch every hole before the squid notices.",
    7: "Become the last lighthouse keeper on a cursed coast and keep the lamp burning through the storm.",
}


def page(appid: int, *, kind: str = "game", released: str = "Mar 5, 2026", coming_soon: bool = False) -> dict[str, Any]:
    return {
        "type": kind,
        "name": f"Peer {appid}",
        "short_description": SHORTS.get(appid, "A fictional game about stacking crates on a moving train."),
        "about_the_game": (
            '<img src="https://cdn.example/a.gif?t=1"><h2 class="bb_tag">Plan the job</h2>'
            "<p>Scout rooftops, pick the lock and argue about the loot while the guard dog snores.</p>"
            '<ul class="bb_ul"><li>Online co-op for 1-4 players</li><li>Hand-drawn city</li></ul>'
            '<img src="https://cdn.example/b.jpg">'
        ),
        "supported_languages": "English, German",
        "genres": [{"description": "Action"}, {"description": "Indie"}],
        "release_date": {"coming_soon": coming_soon, "date": released},
        "screenshots": [{}, {}],
        "movies": [{}],
    }


DETAILS = {
    1: page(1),
    2: page(2, kind="dlc"),
    3: page(3, released="Jan 1, 2019"),
    4: page(4, released="12 Aug, 2025"),
    5: page(5, coming_soon=True, released="Coming soon"),
    7: page(7),
    8: page(8),
}
SEARCHES = {
    ("popularnew", "3955,1685"): [1, 2, 3],
    ("topsellers", "3955,1685"): [4, 1, 5, 1000000],
    ("popularnew", "3955"): [7],
    ("topsellers", "3955"): [1, 8],
}


def fetcher(tmp_path: Path, calls: list[httpx.Request]) -> ReferenceFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        if "populartags" in str(request.url):
            return httpx.Response(200, json=TAGS)
        if "search/results" in str(request.url):
            ids = SEARCHES.get((q["filter"], q.get("tags", "")), [])
            items = [{"name": str(i), "logo": f"https://cdn.example/steam/apps/{i}/capsule.jpg"} for i in ids]
            return httpx.Response(200, json={"desc": "", "items": items})
        if "appdetails" in str(request.url):
            appid = int(q["appids"])
            return httpx.Response(200, json={str(appid): {"success": True, "data": DETAILS[appid]}})
        return httpx.Response(404)

    return ReferenceFetcher(tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(handler)),
                            sleep=lambda _: None)  # fmt: skip


@pytest.fixture
def game(tmp_path: Path) -> Path:
    shutil.copytree(EXAMPLE, tmp_path / "game")
    return tmp_path / "game"


def values(game: Path) -> dict[str, Any]:
    return ManifestFile.load(game / "steamworks.yaml").values()


def note(appid: int, **changes: Any) -> dict[str, Any]:
    base = {
        "appid": appid,
        "short_opening": "genre_and_players",
        "short_moves": ["genre_and_players", "twist"],
        "about_shape": "media_blocks",
        "about_sections": ["hook", "core_loop", "players_modes", "feature_list"],
        "tone": "playful",
        "technique": "Puts the player count up front and lets one silly image carry the hook.",
    }
    return {**base, **changes}


# ---------------------------------------------------------------------------------------------- peers


def test_peers_come_from_both_lists_with_the_most_specific_tags(tmp_path: Path, game: Path) -> None:
    calls: list[httpx.Request] = []
    peers, searched, _ = market.find_peers(fetcher(tmp_path, calls), values(game), limit=3, today=TODAY)
    # Physics (the only genre tag Steam knows here) with Co-op first, then Physics alone for the third peer.
    assert searched == ["Physics", "Co-op"]
    assert [p.appid for p in peers] == [1, 4, 7]
    assert peers[0].lists == ["popular_new", "top_seller"] and peers[1].lists == ["top_seller"]
    assert peers[1].released == "2025-08-12"
    searches = [c for c in calls if "search/results" in str(c.url)]
    first = parse_qs(urlparse(str(searches[0].url)).query)
    assert first["tags"] == ["3955,1685"] and first["category1"] == ["998"] and first["sort_by"] == ["Released_DESC"]
    # Not a DLC, a game from 2019, an unreleased page or the game itself (apps.main.appid 1000000).
    asked = {int(parse_qs(urlparse(str(c.url)).query)["appids"][0]) for c in calls if "appdetails" in str(c.url)}
    assert 1000000 not in asked and {2, 3, 5} <= asked


def test_search_sets_put_genre_tags_before_modes_and_broad_tags_last() -> None:
    sets = market.search_sets(["Co-op", "Indie", "Physics", "Online Co-Op", "Building"])
    assert sets == [
        ["Physics", "Building"],
        ["Physics", "Co-op"],
        ["Physics"],
        ["Co-op", "Online Co-Op"],
        ["Co-op"],
    ]
    assert market.search_sets(["Co-op"]) == [["Co-op"]]
    assert market.search_sets(["Indie", "Casual"]) == [["Indie", "Casual"], ["Indie"]]


def test_peers_report_tags_steam_does_not_know(tmp_path: Path, game: Path) -> None:
    v = values(game)
    _, _, unknown = market.find_peers(fetcher(tmp_path, []), v, limit=3, today=TODAY)
    assert set(unknown) == {"Building", "Funny", "Party Game"}
    with pytest.raises(ValueError, match="Made Up are not Steam tags"):
        market.find_peers(fetcher(tmp_path, []), v, tags=["Made Up"], today=TODAY)
    v["store"]["tags"] = []
    v["store"]["genres"] = []
    v["game"]["genres"] = []
    with pytest.raises(ValueError, match=r"set store.tags"):
        market.find_peers(fetcher(tmp_path, []), v, today=TODAY)


def test_store_tags_are_cached(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    f = fetcher(tmp_path, calls)
    assert f.store_tags()["co-op"] == 1685
    assert f.store_tags()["online co-op"] == 3843
    assert sum("populartags" in str(c.url) for c in calls) == 1


def test_readable_about_keeps_structure() -> None:
    text = market.readable(page(1)["about_the_game"])
    assert text.splitlines() == [
        "[GIF]",
        "## Plan the job",
        "Scout rooftops, pick the lock and argue about the loot while the guard dog snores.",
        "- Online co-op for 1-4 players",
        "- Hand-drawn city",
        "[image]",
    ]
    assert market.readable("word " * 1000, limit=50).endswith("…(cut)")


# ---------------------------------------------------------------------------------------------- study


def test_start_returns_pages_and_saves_only_ids(tmp_path: Path, game: Path) -> None:
    files = ProjectFiles(game)
    out = market.start(fetcher(tmp_path, []), values(game), files, limit=3, today=TODAY)
    assert out["status"] == "label_these" and [p["appid"] for p in out["pages"]] == [1, 4, 7]
    assert out["pages"][0]["short_description"] == SHORTS[1] and "## Plan the job" in out["pages"][0]["about"]
    assert set(out["vocabulary"]) == {"short_opening", "short_moves", "about_shape", "about_sections", "tone"}
    pending = (files.state_dir / "market" / "pending.json").read_text(encoding="utf-8")
    assert "raccoons" not in pending and "Scout rooftops" not in pending
    assert market.studied_appids(files) == [1, 4, 7]


def test_save_checks_every_note_before_saving(tmp_path: Path, game: Path) -> None:
    files = ProjectFiles(game)
    f = fetcher(tmp_path, [])
    with pytest.raises(ValueError, match="study_market"):
        market.save(f, files, [note(1)])
    market.start(f, values(game), files, limit=3, today=TODAY)
    bad = [
        note(1, short_opening="clickbait"),
        note(4, about_sections=["hook", "pricing"]),
        note(9),
        note(7, technique="It says scout rooftops, pick the lock right away."),
    ]
    with pytest.raises(ValueError) as err:
        market.save(f, files, bad)
    message = str(err.value)
    assert "clickbait" in message and "pricing" in message and "9: not one of the pages" in message
    assert "repeats the page (scout rooftops pick the lock)" in message
    with pytest.raises(ValueError, match="at least 3"):
        market.save(f, files, [note(1)])
    assert market.load_study(files) is None


def test_saved_study_has_labels_and_numbers_but_no_page_text(tmp_path: Path, game: Path) -> None:
    files = ProjectFiles(game)
    f = fetcher(tmp_path, [])
    market.start(f, values(game), files, limit=3, today=TODAY)
    out = market.save(
        f,
        files,
        [
            note(1),
            note(4, about_sections=["hook", "players_modes", "core_loop"]),
            note(7, short_opening="player_fantasy", short_moves=["player_fantasy", "stakes"], tone="dark",
                 about_shape="prose", about_sections=["world_story", "hook"]),
        ],
        today=TODAY,
    )  # fmt: skip
    assert out["saved"] == 3 and out["study"]["most_common_opening"] == "genre_and_players (67%)"
    study = market.load_study(files)
    assert study is not None
    assert [(s.label, s.share) for s in study.openings] == [("genre_and_players", 0.67), ("player_fantasy", 0.33)]
    assert study.first_moves[0].label == "genre_and_players > twist"
    assert study.about_order == ["hook", "players_modes", "core_loop"]  # world_story and feature_list: one page each
    assert study.numbers.games == 3 and study.numbers.about.with_lists_share == 1.0
    raw = (files.state_dir / "market" / "study.json").read_text(encoding="utf-8")
    texts = {f"{a}:{k}": t for a in (1, 4, 7) for k, t in market.page_texts(DETAILS[a]).items()}
    assert not find_overlaps(json.dumps(json.loads(raw)["notes"]), texts, min_run=4)
    assert "raccoons" not in raw and "Scout rooftops" not in raw


# ---------------------------------------------------------------------------------------------- briefs


def studied(tmp_path: Path, game: Path) -> ProjectFiles:
    files = ProjectFiles(game)
    f = fetcher(tmp_path, [])
    market.start(f, values(game), files, limit=3, today=TODAY)
    market.save(f, files, [note(1), note(4), note(7, short_opening="situation")], today=TODAY)
    return files


def test_briefs_without_a_study_offer_one(game: Path) -> None:
    b = gen_text.brief(values(game), ProjectFiles(game), "store_short")
    assert set(b["strategies"]) == {"fantasy", "mechanic", "situation_humor"}
    assert b["market"]["status"] == "not_studied" and "study_market" in b["market"]["tip"]


def test_briefs_build_on_the_study(tmp_path: Path, game: Path) -> None:
    files = studied(tmp_path, game)
    short = gen_text.brief(values(game), files, "store_short")
    assert short["market"]["status"] == "studied" and short["market"]["openings"][0]["label"] == "genre_and_players"
    assert short["market"]["openings"][0]["means"].startswith("Names the genre")
    assert (
        "market_common" in short["strategies"]
        and "67% open with genre_and_players" in (short["strategies"]["market_common"])
    )
    # The rarest opening the game's answers support (twist from game.hook, never used by the peers).
    assert short["strategies"]["market_contrast"].startswith("Stand out: only 0% of the studied games open with")
    assert short["task"].startswith("Write 5 short descriptions")
    long = gen_text.brief(values(game), files, "store_long", "outline")
    assert long["market"]["about_order"] and long["market"]["about_shapes"][0]["label"] == "media_blocks"
    assert "numbers" in long["market"] and "techniques" in long["market"]


def test_drafts_are_checked_against_the_studied_pages(tmp_path: Path, game: Path) -> None:
    files = studied(tmp_path, game)
    copied = "Our game: sail a leaky submarine with up to three friends and patch every hole in it today."
    with pytest.raises(ValueError, match="copied from a reference game"):
        gen_text.save_text_draft(
            values(game), files, "store.short_description", copied, "market_common", tmp_path / "cache"
        )


@pytest.mark.anyio
async def test_market_tools_and_prompt(tmp_path: Path, game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock = fetcher(tmp_path, [])
    monkeypatch.setattr("steamworks_mcp.server.ReferenceFetcher", lambda *a, **k: mock)
    config = Config(workspace_root=tmp_path, cache_dir=tmp_path / "cache")
    async with Client(create_server(config)) as client:
        started = await client.call_tool("study_market", {"path": "game", "games": 3})
        assert not started.is_error and started.structured_content is not None
        ids = [p["appid"] for p in started.structured_content["pages"]]
        saved = await client.call_tool("save_market_study", {"path": "game", "notes": [note(i) for i in ids]})
        assert not saved.is_error and saved.structured_content and saved.structured_content["saved"] == 3
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert "market_research" in prompts
        text = (await client.get_prompt("market_research", {"path": "game"})).messages[0].content.text  # type: ignore[union-attr]
        assert "study_market" in text and "save_market_study" in text
        brief = await client.call_tool("generate", {"path": "game", "section": "store_short"})
        assert brief.structured_content and brief.structured_content["market"]["status"] == "studied"
