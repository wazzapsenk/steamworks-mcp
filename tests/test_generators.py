"""Phase 5: images, localization, deterministic generators, SteamPipe scripts, the rubric, drafts and review.

Every sample text is written for a fictional game.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from PIL import Image

from steamworks_mcp.config import Config
from steamworks_mcp.export.preview import bbcode_to_html, store_preview
from steamworks_mcp.export.vdf import build_scripts
from steamworks_mcp.fields import approve_fields, set_fields
from steamworks_mcp.generate import deterministic as det
from steamworks_mcp.generate import text as gen_text
from steamworks_mcp.localization import store as loc
from steamworks_mcp.manifest.io import ManifestFile, ProjectFiles, load_drafts, load_state
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.manifest.state import State
from steamworks_mcp.media.images import prepare_achievement_icons, prepare_store_images, screenshot_report
from steamworks_mcp.project import Project
from steamworks_mcp.server import create_server
from steamworks_mcp.style_guides import guide
from steamworks_mcp.validate import rubric

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "example-game"

GOOD_SHORT = (
    "A co-op party game for up to four friends: build blanket forts out of every cushion in the house, then hold "
    "them against waves of midnight pillow raiders before the whole thing collapses."
)
GOOD_ABOUT = """[h2]Hold the fort[/h2]
[p]Grab cushions and stack a fort with up to 4 friends before midnight.[/p]
[GIF: four players stacking a sofa tower that wobbles]
[p]Rounds last 15-30 minutes, so one more raid always fits.[/p]
[GIF: pillow raiders bursting through the wall]
[p]Play together with Remote Play Together, with a controller or a mouse.[/p]
[GIF: the fort collapsing on a teammate]
[list][*]Online co-op for 1-4 players[*]Physics building without a grid[*]15-minute raids[/list]"""


def values(**game: Any) -> dict[str, Any]:
    v = ManifestFile.load(EXAMPLE / "steamworks.yaml").values()
    v["game"].update(game)
    return Manifest.model_validate(v).model_dump(mode="json")


def png(path: Path, size: tuple[int, int], color: tuple[int, int, int, int] = (200, 60, 60, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


# ------------------------------------------------------------------------------------------------ images


def test_store_images_have_exact_sizes_and_are_never_stretched(tmp_path: Path) -> None:
    png(tmp_path / "store" / "art" / "keyart.png", (3000, 2000))
    png(tmp_path / "store" / "art" / "logo.png", (800, 300), (255, 255, 255, 255))
    out = tmp_path / "out"
    results = {r.id: r for r in prepare_store_images(values(), tmp_path, out)}
    for asset_id, size in {
        "header_capsule": (920, 430),
        "small_capsule": (462, 174),
        "library_hero": (3840, 1240),
        "app_icon": (184, 184),
    }.items():
        with Image.open(tmp_path / results[asset_id].file) as im:  # type: ignore[operator]
            assert im.size == size
    with Image.open(tmp_path / results["library_logo"].file) as im:  # type: ignore[operator]
        assert im.width == 1280 or im.height == 720
    assert any("upscaled" in n for n in results["library_hero"].notes)  # 3000 wide art filling 3840
    assert any("cropped" in n for n in results["header_capsule"].notes)
    assert (out / "shortcut_icon.ico").exists() and (out / "preview.html").exists()
    with Image.open(tmp_path / results["app_icon"].file) as im:  # type: ignore[operator]
        assert im.format == "JPEG"


def test_missing_art_is_reported_not_invented(tmp_path: Path) -> None:
    results = {r.id: r for r in prepare_store_images(values(), tmp_path, tmp_path / "out")}
    assert results["header_capsule"].status == "skipped" and "assets.key_art" in results["header_capsule"].notes[0]


def test_achievement_icons_and_locked_versions(tmp_path: Path) -> None:
    v = values()
    png(tmp_path / "achievements" / "first_fort.png", (512, 512), (30, 200, 30, 255))
    out = {r.id: r for r in prepare_achievement_icons(v, tmp_path, tmp_path / "icons")}
    assert out["ACH_FIRST_FORT"].status == "generated"
    with Image.open(tmp_path / "icons" / "ACH_FIRST_FORT_locked.jpg") as im:
        r, g, b = im.convert("RGB").getpixel((10, 10))  # type: ignore[misc]
        assert abs(r - g) < 4 and abs(g - b) < 4 and r < 150  # grey and darker
    assert out["ACH_TEN_FORTS"].status == "skipped"


def test_localized_screenshots_do_not_count(tmp_path: Path) -> None:
    for name in ("a.png", "b.png", "b_german.png"):
        png(tmp_path / "store" / "screenshots" / name, (1920, 1080))
    report = screenshot_report(values(), tmp_path)
    assert report["count"] == 2
    assert [s["language"] for s in report["screenshots"]] == [None, None, "german"]


# ------------------------------------------------------------------------------------------------ localization


def test_translation_loop(tmp_path: Path) -> None:
    v = values()
    v["store"]["about"] = "[h2]Forts[/h2][p]Build a fort.[/p]"
    (tmp_path / "localization").mkdir()
    (tmp_path / "localization" / "glossary.yaml").write_text(
        "do_not_translate: [Pillow Fort Panic]\nterms:\n  fort: {german: Festung}\n"
    )
    state = State()
    pending = loc.pending(v, tmp_path, "german")
    assert {p["key"] for p in pending} >= {"store.short_description", "store.about", "achievements.ACH_FIRST_FORT.name"}
    about = next(p for p in pending if p["key"] == "store.about")
    assert about["glossary"]["use_terms"] == {"fort": "Festung"}
    out = loc.set_translations(
        v,
        tmp_path,
        state,
        "german",
        {
            "store.about": "[h2]Festungen[/h2]Baue eine Festung.",  # a tag went missing
            "store.short_description": "x" * 301,
            "achievements.ACH_FIRST_FORT.name": "Deckenarchitekt",
            "achievements.ACH_FIRST_FORT.description": "Überstehe deinen ersten Überfall um Mitternacht.",
        },
    )
    assert "BBCode" in out["rejected"]["store.about"]
    assert "300" in out["rejected"]["store.short_description"]
    assert out["saved"] == ["achievements.ACH_FIRST_FORT.name", "achievements.ACH_FIRST_FORT.description"]
    assert state.get("localization.german.achievements.ACH_FIRST_FORT.name").status == "draft"
    st = loc.status(v, tmp_path, ["german"])[0]
    assert "achievements.ACH_FIRST_FORT.name" not in st.missing
    v["achievements"][0]["name"] = "Blanket Engineer"  # source changed -> translation stale
    st = loc.status(v, tmp_path, ["german"])[0]
    assert "achievements.ACH_FIRST_FORT.name" in st.stale


def test_early_access_answers_are_translated_too(tmp_path: Path) -> None:
    v = values()
    v["release"]["early_access"] = None
    v["release"]["early_access_answers"]["why"] = "We want groups to shape the raid modes with us."
    pending = {p["key"]: p for p in loc.pending(v, tmp_path, "german", limit=100)}
    why = pending["release.early_access_answers.why"]
    assert "Why Early Access?" in why["context"] and why["format"] == "plain"
    assert not any(k.startswith("release.") and k != "release.early_access_answers.why" for k in pending)
    v["release"]["early_access"] = False  # answers of a game that is not in Early Access are not translated
    assert "release.early_access_answers.why" not in {p["key"] for p in loc.pending(v, tmp_path, "german", 100)}


def test_a_changed_source_sends_its_translations_back_to_review(tmp_path: Path) -> None:
    shutil.copytree(EXAMPLE, tmp_path / "game")
    project = Project.open(tmp_path / "game")
    key = "achievements.ACH_FIRST_FORT.name"
    loc.set_translations(project.values(), project.files.root, project.state, "german", {key: "Deckenarchitekt"})
    approve_fields(project, [f"localization.german.{key}"])
    project.save()
    assert Project.open(tmp_path / "game").state.get(f"localization.german.{key}").status == "approved"

    set_fields(project, {key: "Blanket Engineer"})  # the source text changes
    project.save()
    project = Project.open(tmp_path / "game")
    assert project.state.get(f"localization.german.{key}").status == "needs_review"

    approve_fields(project, [f"localization.german.{key}"])  # the user says the translation still fits
    project.save()
    project = Project.open(tmp_path / "game")
    assert project.state.get(f"localization.german.{key}").status == "approved"
    assert key not in loc.status(project.values(), project.files.root, ["german"])[0].stale


def test_status_report_names_the_next_language(tmp_path: Path) -> None:
    v = values()
    out = loc.report(v, tmp_path)
    assert out["source_language"] == "english" and out["next"] == "localization_pending(path, language='german')"
    assert out["to_translate"] == 3 * out["texts"]
    v["target_languages"] = []
    assert "No target languages" in loc.report(v, tmp_path)["next"]


def test_glossary_warnings(tmp_path: Path) -> None:
    v = values()
    (tmp_path / "localization").mkdir()
    (tmp_path / "localization" / "glossary.yaml").write_text(
        "do_not_translate: [Pillow Fort Panic]\nterms:\n  fort: {german: Festung}\n"
    )
    v["achievements"][0]["description"] = "Build a fort in Pillow Fort Panic."
    out = loc.set_translations(
        v,
        tmp_path,
        State(),
        "german",
        {"achievements.ACH_FIRST_FORT.description": "Baue eine Burg in Kissenburg Panik."},
    )
    warns = " ".join(out["warnings"]["achievements.ACH_FIRST_FORT.description"])
    assert "Pillow Fort Panic" in warns and "Festung" in warns


def test_source_and_unknown_languages_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="source language"):
        loc.set_translations(values(), tmp_path, State(), "english", {})
    with pytest.raises(ValueError, match="target_languages"):
        loc.set_translations(values(), tmp_path, State(), "japanese", {})


# ------------------------------------------------------------------------------- deterministic generators


def test_cloud_builds_requirements(tmp_path: Path) -> None:
    v = Manifest.model_validate(
        {"game": {"engine": {"name": "unity"}}, "store": {"platforms": ["windows", "linux"]}}
    ).model_dump(mode="json")
    cloud = {f.field: f.value for f in det.cloud(v, tmp_path)}
    assert cloud["apps.main.cloud.byte_quota"] == det.DEFAULT_BYTE_QUOTA
    depots = det.builds(v, tmp_path)[0].value
    assert [d["content_root"] for d in depots] == ["Builds/Windows", "Builds/Linux"]
    v["apps"]["main"]["builds"]["depots"] = depots
    (tmp_path / "Builds" / "Windows").mkdir(parents=True)
    (tmp_path / "Builds" / "Windows" / "game.bin").write_bytes(b"0" * 2_000_000)
    reqs = {f.field: f.value for f in det.requirements(v, tmp_path)}
    assert reqs["store.system_requirements.windows.minimum"]["os"].startswith("Windows")
    assert "storage" in reqs["store.system_requirements.windows.minimum"]


def test_steampipe_scripts(tmp_path: Path) -> None:
    v = values()
    assert "depot_id" in str(build_scripts(v, tmp_path, tmp_path / ".steam-mcp" / "exports" / "gate_2" / "steam"))
    v["apps"]["main"]["builds"]["depots"][0]["depot_id"] = 1000002
    v["apps"]["main"]["builds"]["set_live_on"] = "default"
    scripts = build_scripts(v, tmp_path, tmp_path / ".steam-mcp" / "exports" / "gate_2" / "steam")
    assert isinstance(scripts, dict)
    app = scripts["app_build_1000000.vdf"]
    assert '"SetLive" ""' in app  # never the default branch from a script
    depot = scripts["depot_build_1000002.vdf"]
    assert '"ContentRoot" "../../../../Builds/Windows/"' in depot and '"FileExclusion" "*.pdb"' in depot


# ------------------------------------------------------------------------------------------------ rubric


def results(section: str, text: str, v: dict[str, Any] | None = None, final: bool = False) -> dict[str, str]:
    res, _, _ = rubric.evaluate(section, text, v or values(), guide("coop_party"), final=final)  # type: ignore[arg-type]
    return {r.rule_id: r.outcome for r in res}


def test_good_texts_pass_the_rubric() -> None:
    assert set(results("short", GOOD_SHORT).values()) <= {"pass", "not_applicable"}
    out = results("long", GOOD_ABOUT)
    assert {k: v for k, v in out.items() if v not in ("pass", "not_applicable")} == {
        "long_length_range": "warn"
    }  # short sample


@pytest.mark.parametrize(
    ("section", "text", "rule"),
    [
        ("short", "Welcome to Pillow Fort Panic, a co-op party game for four friends.", "banned_openers"),
        ("short", "An epic, unique and immersive co-op party adventure for four friends.", "hollow_adjectives"),
        (
            "short",
            "Long ago the cushion kingdoms fell. Now a co-op party game for 4 friends rises.",
            "short_first_sentence_genre",
        ),
        ("short", "A party game about building forts. Up to four friends can join.", "short_first_sentence_players"),
        ("short", "A co-op party game for 4 friends with full PvP and leaderboards.", "claims_match_store"),
        ("long", "[p]" + "word " * 80 + "[/p][GIF: x][list][*]a[*]b[*]c[/list]", "long_paragraph_length"),
        ("long", "[p]Forts.[/p][GIF: x][list][*]a[*]b[/list]", "long_feature_list"),
        ("long", "[p]Forts for everyone.[/p][GIF: x][list][*]a[*]b[*]c[/list]", "long_mentions_features"),
        (
            "long",
            "[p]" + "Short words only here. " * 30 + "[/p][GIF: x][list][*]a[*]b[*]c[/list]",
            "long_first_media_early",
        ),
        ("long", "[p]Forts with 4 friends.[/p][list][*]a[*]b[*]c[/list]", "long_media_count"),
    ],
)
def test_rubric_rules_catch_bad_texts(section: str, text: str, rule: str) -> None:
    assert results(section, text)[rule] in ("warn", "fail")


def test_placeholders_only_flagged_for_final_text() -> None:
    assert "long_placeholders_left" not in results("long", GOOD_ABOUT)
    assert results("long", GOOD_ABOUT, final=True)["long_placeholders_left"] == "warn"


def test_genre_guide_overrides_thresholds() -> None:
    base = {r.id: r for r in rubric.rules_for(None)}
    genre = {r.id: r for r in rubric.rules_for(guide("coop_party"))}
    assert base["long_length_range"].params == {"min": 120, "max": 600}
    assert genre["long_length_range"].params == {"min": 120, "max": 400}
    assert genre["long_media_count"].params == {"min": 3, "max": 10}


# Rules accepted from docs/RUBRIC_PROPOSALS.md


@pytest.mark.parametrize(
    ("section", "text", "rule"),
    [
        (
            "short",
            "A co-op party game for four friends. Embark on a journey through a beautiful world.",
            "short_no_mood_filler",
        ),
        ("short", "A casual indie title for four friends who like to build.", "short_genre_specific"),
        ("short", "A co-op party game for four friends building forts. More maps to come.", "short_no_future_promises"),
        ("long", GOOD_SHORT + " [GIF: x][list][*]a[*]b[*]c[/list]", "long_opening_not_copy_of_short"),
        (
            "long",
            "[p]Pillows and blankets for four friends.[/p][GIF: x][list][*]a[*]b[*]c[/list]",
            "long_uses_genre_vocabulary",
        ),
        (
            "short",
            "A co-op party game for four friends: lower your TTK and stack DPS buffs.",
            "plain_language_no_jargon",
        ),
        (
            "long",
            "[p]We are a small indie team from a tiny town.[/p][GIF: x][list][*]a[*]b[*]c[/list]",
            "product_focused_not_studio",
        ),
    ],
)
def test_accepted_proposals_catch_bad_texts(section: str, text: str, rule: str) -> None:
    v = values()
    v["store"]["short_description"] = GOOD_SHORT
    assert results(section, text, v)[rule] == "warn"


def test_accepted_proposals_let_good_texts_pass() -> None:
    v = values()
    v["store"]["short_description"] = GOOD_SHORT
    short, long = results("short", GOOD_SHORT, v), results("long", GOOD_ABOUT, v)
    for rule in (
        "short_no_mood_filler",
        "short_genre_specific",
        "short_no_future_promises",
        "plain_language_no_jargon",
    ):
        assert short[rule] == "pass", rule
    for rule in ("long_opening_not_copy_of_short", "long_uses_genre_vocabulary", "product_focused_not_studio"):
        assert long[rule] == "pass", rule
    assert (
        results("short", "Duel online in PvP or team up in PvE; time to kill (TTK) is short.", v)[
            "plain_language_no_jargon"
        ]
        == "pass"
    )


def test_headers_rule_and_its_genre_switch() -> None:
    text = "[p]" + "Stack cushions with friends. " * 40 + "[/p]"
    base, _, _ = rubric.evaluate("long", text, values(), None)
    assert {r.rule_id: r.outcome for r in base}["long_headers_min"] == "warn"
    with_headers = "[h2]Build the fort[/h2]" + text + "[h2]Survive the raid[/h2][p]Hold on.[/p]"
    ok, _, _ = rubric.evaluate("long", with_headers, values(), None)
    assert {r.rule_id: r.outcome for r in ok}["long_headers_min"] == "pass"
    assert "long_headers_min" not in results("long", text)  # coop_party: headings are optional


def test_judged_proposals_become_questions() -> None:
    _, short_q, _ = rubric.evaluate("short", GOOD_SHORT, values(), None)
    _, long_q, _ = rubric.evaluate("long", GOOD_ABOUT, values(), None)
    asked = {q["rule_id"] for q in short_q + long_q}
    assert {
        "short_states_hook",
        "long_headers_are_core_loop_beats",
        "long_editions_explained",
        "product_focused_not_studio_review",
    } <= asked


def test_store_text_must_exist_in_supported_languages(tmp_path: Path) -> None:
    v = values()
    v["store"]["short_description"] = GOOD_SHORT
    res, _, _ = rubric.evaluate("short", GOOD_SHORT, v, None, final=True, root=tmp_path)
    found = {r.rule_id: r for r in res}["store_text_localized_for_supported_languages"]
    assert found.outcome == "warn" and "german" in found.message
    loc.Localization(tmp_path).write("german", {"store.short_description": GOOD_SHORT})
    res, _, _ = rubric.evaluate("short", GOOD_SHORT, v, None, final=True, root=tmp_path)
    assert (
        "untranslated in german" in {r.rule_id: r for r in res}["store_text_localized_for_supported_languages"].message
    )
    loc.Localization(tmp_path).write(
        "german", {"store.short_description": "Ein Koop-Partyspiel für bis zu vier Freunde."}
    )
    res, _, _ = rubric.evaluate("short", GOOD_SHORT, v, None, final=True, root=tmp_path)
    assert {r.rule_id: r.outcome for r in res}["store_text_localized_for_supported_languages"] == "pass"


def test_preview_html() -> None:
    html = bbcode_to_html(GOOD_ABOUT + "[url=https://x.example]site[/url]")
    assert "<h2>" in html and "<li>" in html and 'class="gif"' in html and "hidden-link" in html
    page = store_preview("Pillow Fort Panic", GOOD_SHORT, GOOD_ABOUT, 700)
    assert "top:700px" in page and "unverified" in page


# ------------------------------------------------------------------------------------------------ tools end to end


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def project(tmp_path: Path) -> tuple[Config, Path]:
    shutil.copytree(EXAMPLE, tmp_path / "game")
    cache = tmp_path / "cache"
    (cache / "3527290").mkdir(parents=True)
    ref = "Gather your crew and climb the frozen mountain before the storm closes in on every last one of you."
    (cache / "3527290" / "appdetails.json").write_text(
        json.dumps(
            {
                "fetched_at": "2026-10-03T00:00:00+00:00",
                "source": "x",
                "data": {"short_description": ref, "about_the_game": ""},
            }
        )
    )
    return Config(workspace_root=tmp_path, cache_dir=cache), tmp_path / "game"


async def call(config: Config, name: str, **args: Any) -> dict[str, Any]:
    async with Client(create_server(config)) as client:
        res = await client.call_tool(name, args)
    if res.is_error:
        raise AssertionError(getattr(res.content[0], "text", ""))
    assert res.structured_content is not None
    return dict(res.structured_content)


@pytest.mark.anyio
async def test_short_description_three_variants_then_pick(project: tuple[Config, Path]) -> None:
    config, game = project
    b = await call(config, "generate", path="game", section="store_short")
    assert b["status"] == "ready" and set(b["strategies"]) == {"fantasy", "mechanic", "situation_humor"}
    assert b["references"] and "short_description" in b["references"][0] and "text" not in json.dumps(b["references"])
    ids = []
    for strategy in b["strategies"]:
        out = await call(
            config, "save_draft", path="game", field="store.short_description", value=GOOD_SHORT, strategy=strategy
        )
        ids.append(out["draft_id"])
        assert out["rubric_score"] == 1.0 and "judge_these" in out
    assert ids == ["fantasy-1", "mechanic-1", "situation_humor-1"]
    with pytest.raises(AssertionError, match="copied from a reference"):
        await call(
            config,
            "save_draft",
            path="game",
            field="store.short_description",
            strategy="fantasy",
            value="Our game: gather your crew and climb the frozen mountain before the storm closes in.",
        )
    with pytest.raises(AssertionError, match="Valve"):
        await call(
            config,
            "save_draft",
            path="game",
            field="store.short_description",
            strategy="fantasy",
            value="Out now! Visit www.example.com",
        )
    chosen = await call(config, "set_field", path="game", field="store.short_description", from_draft="mechanic-1")
    assert chosen["status"]["store.short_description"] == "approved"
    drafts = {d.id: d.status for d in load_drafts(ProjectFiles(game), "store.short_description")}
    assert drafts == {"fantasy-1": "candidate", "mechanic-1": "chosen", "situation_humor-1": "candidate"}


@pytest.mark.anyio
async def test_briefs_build_on_the_interview_answers(project: tuple[Config, Path]) -> None:
    config, _ = project
    short = await call(config, "generate", path="game", section="store_short")
    answers = short["use_the_answers"]
    assert answers["game.players"]["answer"] == "solo, online co-op (1-4 players)"
    assert answers["game.hook"]["answer"].startswith("The fort is a pile of physics objects")
    assert "never name" in answers["game.comparable_games"]["use"]
    assert short["write_in"].startswith("English (english), the source language")
    assert "missing_recommended" not in short
    outline = await call(config, "generate", path="game", section="store_long", stage="outline")
    long = outline["use_the_answers"]
    assert (
        long["game.length"]["answer"] == "sessions of 15-30 minutes; runs: One night of three raid waves, 15-30 minutes"
    )
    assert "feature list" in long["game.launch_content"]["use"] and "game.progression" in long


def test_briefs_ask_for_missing_recommended_answers(tmp_path: Path) -> None:
    v = values(hook=None, fantasy=None, launch_content=[])
    b = gen_text.brief(v, ProjectFiles(tmp_path), "store_short")
    assert b["status"] == "ready" and b["missing_recommended"] == ["game.hook", "game.fantasy", "game.launch_content"]
    assert "game.hook" not in b["use_the_answers"]


def test_player_modes() -> None:
    assert gen_text.player_modes({"min": 1, "max": 1}) == "solo"
    assert gen_text.player_modes({"min": 2, "max": 8, "online_pvp": True, "local_pvp": True}) == (
        "online PvP, local PvP (2-8 players)"
    )
    assert gen_text.player_modes({}) is None


@pytest.mark.anyio
async def test_long_description_outline_first(project: tuple[Config, Path]) -> None:
    config, game = project
    before = await call(config, "generate", path="game", section="store_long", stage="text")
    assert before["status"] == "needs_outline"
    o = await call(
        config,
        "save_draft",
        path="game",
        field="store.about",
        value="1. hook line\n2. [GIF: stacking]\n3. list: a; b; c",
        strategy="outline",
    )
    approved = await call(config, "set_field", path="game", field="store.about", from_draft=o["draft_id"])
    assert approved["outline_approved"] == o["draft_id"]
    assert ManifestFile.load(game / "steamworks.yaml").manifest.store.about != "1. hook line"  # outline not written
    b = await call(config, "generate", path="game", section="store_long", stage="text")
    assert b["status"] == "ready" and "hook line" in b["outline"]
    t = await call(config, "save_draft", path="game", field="store.about", value=GOOD_ABOUT, strategy="text")
    assert t["gif_shotlist"] and (game / t["gif_shotlist"]).read_text("utf-8").count(". ") >= 3


@pytest.mark.anyio
async def test_review_never_overwrites(project: tuple[Config, Path]) -> None:
    config, game = project
    original = ManifestFile.load(game / "steamworks.yaml").manifest.store.short_description
    first = await call(config, "validate", path="game", section="store")
    assert first["store"]["rubric"]["short"]["score"] is not None
    questions = first["store"]["judge_these"]["questions"]
    judged = [{"rule_id": q["rule_id"], "field": q["field"], "outcome": "pass", "note": "ok"} for q in questions]
    second = await call(
        config,
        "validate",
        path="game",
        section="store",
        llm_judgements=[*judged, {"rule_id": "made_up", "outcome": "pass"}],
    )
    assert len(second["store"]["llm_judged"]) == len(questions) and second["store"]["llm_judged_unmatched"] == [
        "made_up"
    ]
    await call(
        config, "save_draft", path="game", field="store.short_description", value=GOOD_SHORT, strategy="revision"
    )
    assert ManifestFile.load(game / "steamworks.yaml").manifest.store.short_description == original


@pytest.mark.anyio
async def test_needs_game_inputs_before_writing(tmp_path: Path) -> None:
    (tmp_path / "g").mkdir()
    config = Config(workspace_root=tmp_path, cache_dir=tmp_path / "cache")
    await call(config, "init_project", path="g", scan=False)
    out = await call(config, "generate", path="g", section="store_short")
    assert out["status"] == "needs_input" and "game.pitch" in out["missing"]


@pytest.mark.anyio
async def test_deterministic_generate_and_localization_tools(project: tuple[Config, Path]) -> None:
    config, game = project
    builds = await call(config, "generate", path="game", section="builds")
    assert builds["scripts"] == {"not_yet": "apps.main.builds.depots has no depot with a depot_id"}
    await call(config, "set_field", path="game", values={"store.platforms": ["windows", "linux"]})
    reqs = await call(config, "generate", path="game", section="requirements")
    assert any(a["field"] == "store.system_requirements.linux.minimum" for a in reqs["applied"])
    state = load_state(ProjectFiles(game))
    assert state.get("store.system_requirements.linux.minimum").status == "draft"
    pending = await call(config, "localization_pending", path="game", language="german", limit=2)
    assert len(pending["entries"]) == 2
    saved = await call(
        config,
        "localization_set",
        path="game",
        language="german",
        translations={"achievements.ACH_FIRST_FORT.name": "Deckenarchitekt"},
    )
    assert saved["saved"] == ["achievements.ACH_FIRST_FORT.name"]
    ok = await call(config, "approve_fields", path="game", fields=["localization.german.*"])
    assert ok["approved"] == ["localization.german.achievements.ACH_FIRST_FORT.name"]
    status = await call(config, "localization_status", path="game")
    assert {s["language"] for s in status["languages"]} == {"german", "french", "schinese"}
    assert status["next"] == "localization_pending(path, language='german')"
    preview = await call(config, "preview_store", path="game")
    assert (game / preview["file"]).exists() and preview["fold_verified"] is False
