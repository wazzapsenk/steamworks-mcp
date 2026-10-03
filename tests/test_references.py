"""Reference fetcher, analyzer, anti-copy rule and style guides. All sample texts are written for fictional games."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from steamworks_mcp.data import load_yaml
from steamworks_mcp.references import bundled_analysis, catalog, matching
from steamworks_mcp.references.analyze import about_stats, achievement_kind, analyze, api_name_style, short_stats
from steamworks_mcp.references.anticopy import MIN_RUN, find_overlaps, is_original, words
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher, parse_community_achievements
from steamworks_mcp.style_guides import all_guides, for_tags

REF = (
    "[h2]Stack the sofa[/h2]Pillow Fort Panic lets you and three friends build a blanket fort out of every cushion "
    "in the house before midnight raiders arrive."
)

# ------------------------------------------------------------------------------------------------ anti-copy


def test_eight_word_run_is_flagged_seven_is_not() -> None:
    eight = "you and three friends build a blanket fort"
    seven = "you and three friends build a blanket"
    assert len(words(eight)) == MIN_RUN
    assert not is_original(f"In this one {eight} tonight.", {"ref": REF})
    assert is_original(f"In this one {seven} somewhere else entirely.", {"ref": REF})


def test_formatting_case_and_punctuation_do_not_hide_copies() -> None:
    sneaky = "<b>YOU</b> and three friends, build a [i]blanket[/i] fort -- out of every cushion!"
    overlaps = find_overlaps(sneaky, {"ref": REF})
    assert overlaps and overlaps[0].reference == "ref"
    assert overlaps[0].length == 12  # the maximal run, not just 8


def test_overlaps_across_references_longest_first() -> None:
    refs = {"a": "one two three four five six seven eight", "b": "alpha beta gamma delta epsilon zeta eta theta iota"}
    text = "x alpha beta gamma delta epsilon zeta eta theta iota y one two three four five six seven eight"
    found = find_overlaps(text, refs)
    assert [o.reference for o in found] == ["b", "a"]


# ------------------------------------------------------------------------------------------------ fetcher

APPDETAILS: dict[str, Any] = {
    "name": "Pillow Fort Panic",
    "short_description": "Build blanket forts with up to 4 friends! Then you defend them from pillow raiders.",
    "about_the_game": (
        '<img src="https://cdn.example/a.gif"><p class="bb_paragraph">Grab cushions and stack them.</p>'
        '<h2 class="bb_tag">Modes</h2><ul class="bb_ul"><li>Online co-op</li><li>Solo</li><li>Endless</li></ul>'
        "First line of a long paragraph that keeps going.<br><br>Second paragraph here.<br>"
        '<video><source src="https://cdn.example/b.webm"></video>'
    ),
    "screenshots": [{}, {}, {}],
    "movies": [{}],
    "categories": [
        {"description": "Online Co-op"},
        {"description": "Steam Achievements"},
        {"description": "Online Co-op"},
    ],
    "genres": [{"description": "Casual"}, {"description": "Early Access"}],
    "supported_languages": "English<strong>*</strong>, German, French<br><strong>*</strong>languages with full audio",
    "platforms": {"windows": True, "mac": False, "linux": True},
    "price_overview": {"currency": "USD", "initial": 999},
    "release_date": {"date": "May 12, 2027"},
    "achievements": {"total": 3},
}

COMMUNITY_PAGE = """
<div class="achieveRow "><div class="achievePercent">51.2%</div><div class="achieveTxt"><h3>Blanket Architect</h3>
<h5>Survive your first midnight raid.</h5></div></div>
<div class="achieveRow "><div class="achievePercent">8.5%</div><div class="achieveTxt"><h3>Cushion &amp; Co</h3>
<h5>Collect 10 golden cushions.</h5></div></div>
<div class="achieveRow "><div class="achievePercent">0.4%</div><div class="achieveTxt"><h3>It Was the Cat</h3>
<h5>Fall off your own fort.</h5></div></div>
"""


def transport(calls: list[httpx.Request], responses: dict[str, list[httpx.Response]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        for key, queue in responses.items():
            if key in str(request.url):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def fetcher(
    tmp_path: Path, calls: list[httpx.Request], responses: dict[str, list[httpx.Response]], **kw: Any
) -> ReferenceFetcher:
    sleeps: list[float] = []
    f = ReferenceFetcher(
        tmp_path, client=httpx.Client(transport=transport(calls, responses)), sleep=sleeps.append, **kw
    )
    f.sleeps = sleeps  # type: ignore[attr-defined]
    return f


def test_appdetails_is_cached_with_its_fetch_time(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    ok = httpx.Response(200, json={"1000000": {"success": True, "data": APPDETAILS}})
    f = fetcher(tmp_path, calls, {"appdetails": [ok]})
    first = f.appdetails(1000000)
    second = f.appdetails(1000000)
    assert len(calls) == 1
    assert first.data["name"] == second.data["name"] == "Pillow Fort Panic"
    assert "cc=us" in str(calls[0].url)
    cached = json.loads((tmp_path / "1000000" / "appdetails.json").read_text(encoding="utf-8"))
    assert cached["fetched_at"] and cached["source"].startswith("https://store.steampowered.com/")


def test_old_cache_is_refetched(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    ok = httpx.Response(200, json={"1000000": {"success": True, "data": APPDETAILS}})
    f = fetcher(tmp_path, calls, {"appdetails": [ok]}, max_age_days=0)
    f.appdetails(1000000)
    f.appdetails(1000000)
    assert len(calls) == 2


def test_unavailable_app_is_an_error(tmp_path: Path) -> None:
    f = fetcher(tmp_path, [], {"appdetails": [httpx.Response(200, json={"1000000": {"success": False}})]})
    with pytest.raises(FetchError, match="not available"):
        f.appdetails(1000000)


def test_rate_limit_retries_with_retry_after(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    responses = {
        "appdetails": [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(503),
            httpx.Response(200, json={"1000000": {"success": True, "data": APPDETAILS}}),
        ]
    }
    f = fetcher(tmp_path, calls, responses)
    assert f.appdetails(1000000).data["name"] == "Pillow Fort Panic"
    assert len(calls) == 3
    assert 7.0 in f.sleeps  # type: ignore[attr-defined]


def test_gives_up_after_retries(tmp_path: Path) -> None:
    f = fetcher(tmp_path, [], {"appdetails": [httpx.Response(500)]}, retries=2)
    with pytest.raises(FetchError, match="3 attempts"):
        f.appdetails(1000000)


def test_achievement_sources(tmp_path: Path) -> None:
    pct = {"achievementpercentages": {"achievements": [{"name": "ACH_FIRST_FORT", "percent": "51.2"}]}}
    calls: list[httpx.Request] = []
    f = fetcher(
        tmp_path,
        calls,
        {
            "GetGlobalAchievementPercentages": [httpx.Response(200, json=pct)],
            "steamcommunity": [httpx.Response(200, text=COMMUNITY_PAGE)],
        },
    )
    assert f.achievement_percentages(1000000).data == [{"name": "ACH_FIRST_FORT", "percent": 51.2}]
    texts = f.achievement_texts(1000000).data
    assert [t["name"] for t in texts] == ["Blanket Architect", "Cushion & Co", "It Was the Cat"]
    assert f.schema(1000000) is None  # no key, no request
    assert not any("GetSchemaForGame" in str(c.url) for c in calls)


def test_no_public_achievements(tmp_path: Path) -> None:
    f = fetcher(tmp_path, [], {"GetGlobalAchievementPercentages": [httpx.Response(403, json={})]})
    assert f.achievement_percentages(1000000).data == []


def test_web_api_key_never_reaches_the_cache(tmp_path: Path) -> None:
    schema = {"game": {"availableGameStats": {"achievements": [{"name": "ACH_FIRST_FORT", "hidden": 0}]}}}
    f = fetcher(tmp_path, [], {"GetSchemaForGame": [httpx.Response(200, json=schema)]}, web_api_key="ABCDEF0123456789")
    assert f.schema(1000000) is not None
    assert "ABCDEF0123456789" not in (tmp_path / "1000000" / "schema.json").read_text(encoding="utf-8")


def test_requests_to_one_host_are_spaced(tmp_path: Path) -> None:
    ok = httpx.Response(
        200, json={"1": {"success": True, "data": APPDETAILS}, "2": {"success": True, "data": APPDETAILS}}
    )
    f = fetcher(tmp_path, [], {"appdetails": [ok]}, min_interval=5)
    f.appdetails(1)
    f.appdetails(2)
    assert any(s > 4 for s in f.sleeps)  # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------------ analysis


def test_about_structure() -> None:
    s = about_stats(APPDETAILS["about_the_game"])
    assert s.structure == ["animation", "paragraph", "heading", "list", "paragraph", "animation"]
    assert (s.headings, s.lists, s.list_items, s.animations, s.images) == (1, 1, 3, 2, 0)
    assert s.paragraphs == 3
    assert s.words_before_first_media == 0


def test_short_description_stats() -> None:
    s = short_stats(APPDETAILS["short_description"])
    assert s.mentions_players and s.has_numbers
    assert (s.sentences, s.exclamations, s.second_person) == (2, 1, 1)


@pytest.mark.parametrize(
    ("description", "kind"),
    [
        ("Survive your first midnight raid.", "progression"),
        ("Collect 10 golden cushions.", "collection"),
        ("Win a raid without losing a single pillow.", "skill"),
        ("Fall off your own fort.", "secret_funny"),
        ("Meet the cat.", "other"),
    ],
)
def test_achievement_kinds(description: str, kind: str) -> None:
    assert achievement_kind(description) == kind


def test_api_name_styles() -> None:
    assert api_name_style(["ACH_WIN", "ACH_FIRST_FORT"]) == "UPPER_SNAKE"
    assert api_name_style(["FirstFort", "TenForts"]) == "PascalCase"
    assert api_name_style(["ACH_01", "ACH_02"]) == "numbered"
    assert api_name_style([]) == "none"


def test_analyze_keeps_only_derived_data() -> None:
    texts = parse_community_achievements(COMMUNITY_PAGE)
    pct = [{"name": "ACH_A", "percent": 51.2}, {"name": "ACH_B", "percent": 8.5}, {"name": "ACH_C", "percent": 0.4}]
    a = analyze(1000000, APPDETAILS, pct, texts, None, dt.date(2026, 10, 3), ["coop_party"])
    assert a.store.categories == ["Online Co-op", "Steam Achievements"]
    assert (a.store.languages_interface, a.store.languages_full_audio) == (3, 1)
    assert a.store.price_usd == 9.99 and a.store.early_access and a.store.platforms == ["linux", "windows"]
    assert a.achievements.count == 3 and a.achievements.share_under_1_percent == 0.33
    assert a.achievements.hidden_share is None
    assert a.steam_features["online_coop"] and not a.steam_features["cloud"]
    dumped = a.model_dump_json(exclude={"name"})
    sources = {"short": APPDETAILS["short_description"], "about": APPDETAILS["about_the_game"]}
    sources |= {t["name"]: t["description"] for t in texts}
    assert not find_overlaps(dumped, sources, min_run=4)  # 3-word runs: Steam's own labels such as Online Co-op


# ------------------------------------------------------------------------------------------------ bundled data


def test_catalog_and_matching() -> None:
    assert len(catalog()) == 4
    assert {g.appid for g in matching(["coop_party"])} <= {g.appid for g in catalog()}
    assert matching(["racing"]) == []


@pytest.mark.parametrize("appid", [g.appid for g in catalog()])
def test_bundled_analysis_is_derived_only(appid: int) -> None:
    a = bundled_analysis(appid)
    assert a is not None and a.appid == appid

    def strings(v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        if isinstance(v, dict):
            return [s for x in v.values() for s in strings(x)]
        if isinstance(v, list):
            return [s for x in v for s in strings(x)]
        return []

    data = a.model_dump(mode="json", exclude={"name"})
    long = [s for s in strings(data) if len(s.split()) > 4]
    assert not long, long


def test_style_guides() -> None:
    guides = all_guides()
    assert guides
    known = {g.appid for g in catalog()}
    for g in guides:
        assert set(g.meta.references) <= known
    assert for_tags(["coop_party", "online_coop"]) is not None
    assert for_tags(["racing"]) is None
    assert load_yaml("languages.yaml")
