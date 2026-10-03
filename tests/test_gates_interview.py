"""Gate rule engine, crosschecks, store text rules, interview and the field tools."""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.elicitation import render_elicitation_schema
from mcp_types import ElicitResult
from PIL import Image

from steamworks_mcp import spec_info as spec_info_module
from steamworks_mcp.config import Config
from steamworks_mcp.gates.engine import RuleResult, evaluate_gates
from steamworks_mcp.interview.forms import form_key, form_model
from steamworks_mcp.interview.questions import build_question, coerce, next_questions, pending_fields
from steamworks_mcp.manifest.drafts import Draft
from steamworks_mcp.manifest.io import ManifestFile, ProjectFiles, load_state, save_draft
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.manifest.state import State
from steamworks_mcp.references import patterns
from steamworks_mcp.server import create_server
from steamworks_mcp.validate.crosschecks import CHECKS, CheckContext
from steamworks_mcp.validate.store_text import check_store_text

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "example-game" / "steamworks.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "unity_min_project"
TODAY = dt.date(2026, 10, 3)


def example_values(**changes: Any) -> dict[str, Any]:
    values = ManifestFile.load(EXAMPLE).values()
    for path, value in changes.items():
        head, _, rest = path.partition(".")
        target = values[head]
        keys = rest.split(".") if rest else []
        for k in keys[:-1]:
            target = target[k]
        if keys:
            target[keys[-1]] = value
        else:
            values[head] = value
    return Manifest.model_validate(values).model_dump(mode="json")


def results(
    values: dict[str, Any], root: Path = EXAMPLE.parent, state: State | None = None, browser: bool = False
) -> dict[str, RuleResult]:
    s = state or State()
    if state is None:
        s.reconcile(values)
    return {r.rule.id: r for r in evaluate_gates(values, s, root, browser=browser, today=TODAY)}


# ------------------------------------------------------------------------------------------------ engine


def test_example_game_statuses() -> None:
    r = results(example_values())
    assert r["steam_direct_fee"].status == "pass"
    assert r["steam_tags"].status == "pass"
    assert r["content_survey_complete"].status == "fail" and "answered 'no'" in r["content_survey_complete"].message
    assert r["early_access_questionnaire"].status == "not_applicable"
    assert r["sign_nda_and_steam_distribution_agreement"].status == "todo"
    assert r["tax_verification_wait"].status == "info"
    assert r["dedicated_builder_account"].status == "warn"  # recommended rules never fail


def test_date_gap_and_range() -> None:
    r = results(example_values(**{"release.coming_soon_since": "2027-05-01"}))
    assert r["coming_soon_min_two_weeks"].status == "fail" and "11 days" in r["coming_soon_min_two_weeks"].message
    assert r["fee_waiting_period_elapsed"].status == "pass"
    cheap = results(example_values(**{"pricing.base_price_usd": "0.49"}))
    assert cheap["minimum_price"].status == "fail"
    f2p = results(example_values(**{"pricing.free_to_play": True, "pricing.base_price_usd": None}))
    assert f2p["minimum_price"].status == "not_applicable"


def test_drafts_make_a_rule_wait_for_review() -> None:
    values = example_values()
    s = State()
    s.reconcile(values)
    s.record_value("store.tags", values["store"]["tags"], "scan", confidence=0.5)
    r = results(values, state=s)
    assert r["steam_tags"].status == "review" and r["steam_tags"].needs_approval == ["store.tags"]


def test_checklist_items_are_confirmed_by_the_user() -> None:
    values = example_values()
    s = State()
    s.reconcile(values)
    s.confirm_checklist("checklist.sign_nda_and_steam_distribution_agreement")
    assert results(values, state=s)["sign_nda_and_steam_distribution_agreement"].status == "done"


def test_browser_rules_fall_back_when_browser_mode_is_off() -> None:
    off = results(example_values())["short_description"]
    on = results(example_values(), browser=True)["short_description"]
    assert (off.mode, on.mode) == ("ARTIFACT", "BROWSER")


def png(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (40, 90, 160)).save(path)


def test_asset_overrides_are_measured(tmp_path: Path) -> None:
    png(tmp_path / "art" / "header.png", (920, 430))
    png(tmp_path / "art" / "small.png", (460, 215))
    values = example_values(
        **{"assets.overrides": {"header_capsule": "art/header.png", "small_capsule": "art/small.png"}}
    )
    r = results(values, tmp_path)
    assert r["header_capsule"].status in ("pass", "review")
    assert r["small_capsule"].status == "fail" and "460x215" in r["small_capsule"].message


def test_assets_derivable_from_key_art_and_logo(tmp_path: Path) -> None:
    png(tmp_path / "store" / "art" / "keyart.png", (3840, 2160))
    png(tmp_path / "store" / "art" / "logo.png", (1600, 600))
    r = results(example_values(), tmp_path)
    assert r["main_capsule"].status == "todo" and "prepare_images" in r["main_capsule"].message


def test_screenshots_are_counted_by_size(tmp_path: Path) -> None:
    for i in range(4):
        png(tmp_path / "store" / "screenshots" / f"s{i}.png", (1920, 1080))
    png(tmp_path / "store" / "screenshots" / "small.png", (1280, 720))
    r = results(example_values(), tmp_path)["screenshots"]
    assert r.status == "fail" and "4 usable" in r.message and "small.png" in r.message
    png(tmp_path / "store" / "screenshots" / "s9.png", (2560, 1440))
    assert results(example_values(), tmp_path)["screenshots"].status in ("pass", "review")


# ------------------------------------------------------------------------------------------------ store text rules


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("x" * 301, "short_description_max_length"),
        ("Build forts with [b]friends[/b].", "short_description_plain_text"),
        ("Build forts.\nThen defend them.", "short_description_plain_text"),
        ("Now available: build forts with friends.", "short_description_no_time_based_text"),
        ("Join us at www.example.com to build forts.", "description_no_links"),
        ("Visit pillowfort dot com for more.", "description_no_implied_urls_or_qr"),
    ],
)
def test_short_description_rules(text: str, rule: str) -> None:
    findings = check_store_text({"english": {"store.short_description": text}})
    assert rule in {f.rule_id for f in findings}


def test_about_rules() -> None:
    about = "[h2]Forts[/h2][p]Build them.[/p][url=https://x.example]site[/url][marquee]no[/marquee]"
    ids = {f.rule_id for f in check_store_text({"english": {"store.about": about}})}
    assert {"url_tag_effectively_disallowed", "allowed_bbcode_tags", "description_no_links"} <= ids
    clean = "[h2]Forts[/h2][p]Build them with up to four friends.[/p][list][*]Co-op[/list]"
    assert not check_store_text({"english": {"store.about": clean}})


def test_english_fallback() -> None:
    findings = check_store_text({"german": {"store.short_description": "Baut Burgen."}})
    assert "english_fallback_required" in {f.rule_id for f in findings}


# ------------------------------------------------------------------------------------------------ crosschecks


def ctx(values: dict[str, Any], root: Path = EXAMPLE.parent, today: dt.date = TODAY) -> CheckContext:
    return CheckContext(values, root, today)


def test_sysreqs_and_depots_per_platform() -> None:
    v = example_values(**{"store.platforms": ["windows", "linux"]})
    assert CHECKS["sysreqs_per_platform"](ctx(v)).status == "fail"
    assert "no depot for linux" in CHECKS["depot_per_platform"](ctx(v)).message
    assert CHECKS["sysreqs_per_platform"](ctx(example_values())).status == "pass"


def test_categories_match_configuration(tmp_path: Path) -> None:
    v = example_values(
        **{"store.categories": ["Steam Achievements", "Steam Leaderboards", "Steam Cloud"], "achievements": []}
    )
    out = CHECKS["store_categories_match_config"](ctx(v))
    assert out.status == "fail" and "Steam Achievements" in out.message
    scan = tmp_path / ".steam-mcp" / "scan"
    scan.mkdir(parents=True)
    (scan / "unity.json").write_text('{"facts": {"steam_sdk": "steamworks.net", "code_achievements": ["ACH_SECRET"]}}')
    out = CHECKS["store_categories_match_config"](ctx(example_values(), tmp_path))
    assert out.status == "fail" and "ACH_SECRET" in out.message


def test_release_date_checks() -> None:
    saturday = example_values(**{"release.planned_date": "2026-10-10"})
    assert CHECKS["weekday_release"](ctx(saturday)).status == "warn"
    assert CHECKS["release_date_lock"](ctx(saturday)).status == "warn"
    assert CHECKS["release_date_lock"](ctx(example_values())).status == "pass"


def test_next_fest_timing() -> None:
    v = example_values(**{"release.events": ["next_fest_2027_02"], "release.planned_date": "2027-02-24"})
    out = CHECKS["next_fest_timing"](ctx(v))
    assert out.status == "fail" and "must not release before" in out.message
    stale = CHECKS["next_fest_timing"](
        ctx(example_values(**{"release.events": ["next_fest_2027_06"]}), today=dt.date(2027, 3, 1))
    )
    assert "refresh data/events.yaml" in stale.message
    unknown = CHECKS["next_fest_timing"](ctx(example_values(**{"release.events": ["no_such_fest"]})))
    assert unknown.status == "fail"


def test_autocloud_override_needs_all_os_root() -> None:
    v = example_values()
    v["apps"]["main"]["cloud"]["auto_cloud"][0]["os"] = "windows"
    assert CHECKS["autocloud_override_root_all_os"](ctx(v)).status == "fail"
    assert CHECKS["autocloud_override_root_all_os"](ctx(example_values())).status == "pass"


def test_ai_disclosure() -> None:
    assert CHECKS["ai_disclosure_complete"](ctx(example_values(**{"content.ai": {"uses_ai": True}}))).status == "fail"
    live = example_values(**{"content.ai": {"uses_ai": True, "live_generated": "NPC lines"}})
    assert "guardrails" in CHECKS["ai_disclosure_complete"](ctx(live)).message


# ------------------------------------------------------------------------------------------------ interview


def test_questions_start_with_gate_zero_then_game_inputs() -> None:
    values = Manifest().model_dump(mode="json")
    s = State()
    res = list(results(values, state=s).values())
    order = [p for p, _, _ in pending_fields(res, values, s)]
    assert order[0].startswith(("prerequisites.", "apps.main.appid"))
    assert order.index("game.name") < order.index("store.platforms")
    assert "store.short_description" not in order and "game.players.max" in order
    qs, remaining = next_questions(res, values, s, limit=3)
    assert len(qs) == 3 and remaining > 10
    assert all(q.gate == qs[0].gate for q in qs)


def test_store_page_inputs_are_asked() -> None:
    values = Manifest().model_dump(mode="json")
    s = State()
    order = [p for p, _, _ in pending_fields(list(results(values, state=s).values()), values, s)]
    wanted = [
        "game.genres",
        "game.comparable_games",
        "game.hook",
        "game.fantasy",
        "game.core_loop",
        "game.players.min",
        "game.run_length",
        "game.progression",
        "game.launch_content",
        "game.tone",
    ]
    assert [p for p in order if p in wanted] == wanted
    content = build_question("game.launch_content", 1, values, s, "game")
    assert content.kind == "list" and "at launch" in content.question
    assert "solo" in build_question("game.players.min", 1, values, s, "players").question
    assert "never named" in build_question("game.comparable_games", 1, values, s, "game").help


def test_scanned_values_are_offered_for_confirmation() -> None:
    values = Manifest.model_validate({"game": {"name": "Pillow Fort Panic"}}).model_dump(mode="json")
    s = State()
    s.record_value("game.name", "Pillow Fort Panic", "scan", confidence=0.7)
    res = list(results(values, state=s).values())
    q = build_question("game.name", 1, values, s, "game")
    assert q.confirm and q.suggestion == "Pillow Fort Panic" and q.suggestion_source == "scan"
    assert "game.name" in [p for p, _, _ in pending_fields(res, values, s)]


def test_coerce() -> None:
    assert coerce("store.platforms", "windows, linux") == ["windows", "linux"]
    assert coerce("prerequisites.tax_interview_done", "Yes") is True
    assert coerce("prerequisites.tax_interview_done", "no") is False
    assert coerce("game.players.max", "4") == "4"  # pydantic converts on write


def test_language_answers_become_steam_codes() -> None:
    assert coerce("source_language", "German") == "german"
    assert coerce("target_languages", "de, Simplified Chinese, Brazilian Portuguese") == [
        "german",
        "schinese",
        "brazilian",
    ]
    assert coerce("target_languages", "none") == [] and coerce("target_languages", "no") == []
    assert coerce("target_languages", "German, no") == ["german", "norwegian"]  # in a list, "no" is Norwegian
    table = {"english": {"interface": True, "full_audio": True, "subtitles": True}}
    assert coerce("store.supported_languages", ["english", "french"], table) == {
        "english": table["english"],  # a chosen language keeps its row
        "french": {},
    }


def test_language_questions() -> None:
    empty = Manifest().model_dump(mode="json")
    s = State()
    s.reconcile(empty)
    order = [p for p, _, _ in pending_fields(list(results(empty, state=s).values()), empty, s)]
    # translations default to the supported languages, so those come first
    assert order.index("source_language") < order.index("store.supported_languages")
    assert "target_languages" not in order

    values = Manifest.model_validate(
        {"store": {"supported_languages": {"english": {}, "german": {}, "schinese": {}}}}
    ).model_dump(mode="json")
    s = State()
    s.reconcile(values)
    groups = {p: g for p, _, g in pending_fields(list(results(values, state=s).values()), values, s)}
    assert groups["source_language"] == groups["target_languages"] == "languages"
    assert "store.supported_languages" not in groups
    source = build_question("source_language", 1, values, s, "languages")
    assert source.kind == "choice" and source.confirm and source.suggestion == "english"
    target = build_question("target_languages", 1, values, s, "languages")
    assert target.kind == "multi" and "english" not in target.options and "koreana" in target.options
    assert target.suggestion == ["german", "schinese"] and target.suggestion_source == "store.supported_languages"
    assert target.labels["schinese"] == "Chinese (Simplified)"
    supported = build_question("store.supported_languages", 1, values, s, "languages")
    assert supported.kind == "multi" and supported.suggestion == ["english", "german", "schinese"]


def test_language_form_uses_titled_options() -> None:
    values = Manifest().model_dump(mode="json")
    qs = [build_question(p, 1, values, State(), "languages") for p in ("source_language", "store.supported_languages")]
    model = form_model(qs)
    schema = render_elicitation_schema(model)["properties"]  # raises when a field is not spec-valid
    assert {"const": "koreana", "title": "Korean"} in schema["source_language"]["oneOf"]
    assert "enum" not in schema["source_language"]
    multi = schema[form_key("store.supported_languages")]
    assert (
        multi["type"] == "array" and {"const": "latam", "title": "Spanish (Latin America)"} in multi["items"]["anyOf"]
    )
    answer = model.model_validate({"source_language": "french", form_key("store.supported_languages"): ["french"]})
    assert answer.model_dump()["source_language"] == "french"


# ------------------------------------------------------------------------------------------------ tools


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def game(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURE, tmp_path / "game")
    return tmp_path


async def tool(config: Config, name: str, client_kw: dict[str, Any] | None = None, **args: Any) -> dict[str, Any]:
    async with Client(create_server(config), **(client_kw or {})) as client:
        res = await client.call_tool(name, args)
    assert not res.is_error, getattr(res.content[0], "text", "")
    assert res.structured_content is not None
    return dict(res.structured_content)


@pytest.mark.anyio
async def test_interview_round_trip(game: Path) -> None:
    config = Config(workspace_root=game)
    await tool(config, "init_project", path="game")
    report = await tool(config, "gap_report", path="game")
    assert report["fields_to_fill"] > 0 and report["next"]
    assert {g["gate"] for g in report["gates"]} == {0, 1, 2, 3}
    first = await tool(config, "start_interview", path="game", use_form=False)
    ids = [q["id"] for q in first["questions"]]
    assert 1 <= len(ids) <= 3
    answers = {
        i: (
            "yes"
            if q["type"] == "bool"
            else "1000000"
            if q["type"] == "int"
            else "2026-09-01"
            if q["type"] == "date"
            else "x"
        )
        for i, q in zip(ids, first["questions"], strict=True)
    }
    saved = await tool(config, "set_field", path="game", values=answers)
    assert set(saved["set"]) == set(ids) and all(v == "approved" for v in saved["status"].values())
    second = await tool(config, "start_interview", path="game", use_form=False)
    assert not set(ids) & {q["id"] for q in second["questions"]}


@pytest.mark.anyio
async def test_approve_and_mark_applied(game: Path) -> None:
    config = Config(workspace_root=game)
    await tool(config, "init_project", path="game")
    approved = await tool(config, "approve_fields", path="game", fields=["achievements.*.name", "apps.main.cloud"])
    assert "apps.main.cloud.auto_cloud.0.pattern" in approved["approved"]
    assert "achievements.ACH_FIRST_FORT.name" in approved["skipped"]  # empty
    done = await tool(
        config,
        "mark_applied",
        path="game",
        fields=[
            "checklist.sign_nda_and_steam_distribution_agreement",
            "apps.main.cloud.auto_cloud.0.pattern",
            "game.name",
        ],
    )
    assert "checklist.sign_nda_and_steam_distribution_agreement" in done["applied"]
    assert "apps.main.cloud.auto_cloud.0.pattern" in done["applied"]
    assert "game.name" in done["refused"]  # a scanned draft is not approved yet
    state = load_state(ProjectFiles(game / "game"))
    assert state.get("checklist.sign_nda_and_steam_distribution_agreement").status == "applied"


@pytest.mark.anyio
async def test_generated_store_text_breaking_valve_rules_is_rejected(game: Path) -> None:
    config = Config(workspace_root=game)
    await tool(config, "init_project", path="game", scan=False)
    async with Client(create_server(config)) as client:
        bad = await client.call_tool(
            "set_field",
            {
                "path": "game",
                "field": "store.short_description",
                "value": "Out now at www.example.com!",
                "source": "generated",
            },
        )
    assert bad.is_error and "store rules" in getattr(bad.content[0], "text", "")
    ok = await tool(
        config,
        "set_field",
        path="game",
        field="store.short_description",
        value="Build blanket forts with up to four friends.",
        source="generated",
    )
    assert ok["status"]["store.short_description"] == "draft"
    user = await tool(config, "set_field", path="game", field="store.short_description", value="See www.example.com")
    assert user["status"]["store.short_description"] == "approved" and user["store_rule_findings"]


@pytest.mark.anyio
async def test_set_field_from_draft(game: Path) -> None:
    config = Config(workspace_root=game)
    await tool(config, "init_project", path="game", scan=False)
    files = ProjectFiles(game / "game")
    save_draft(
        files,
        Draft(
            id="fantasy-1",
            field="store.short_description",
            value="You and three friends build a fort.",
            strategy="fantasy",
        ),
    )
    out = await tool(config, "set_field", path="game", field="store.short_description", from_draft="fantasy-1")
    assert out["status"]["store.short_description"] == "approved"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_interview_form_when_the_client_supports_it(game: Path, mode: str) -> None:
    config = Config(workspace_root=game)
    await tool(config, "init_project", path="game", scan=False)
    seen: dict[str, Any] = {}

    async def answer(context: Any, params: Any) -> ElicitResult:
        props = params.requested_schema["properties"]
        seen.update(props)
        content = {
            k: (True if p.get("type") == "boolean" else 1000000 if p.get("type") == "integer" else "2026-09-01")
            for k, p in props.items()
        }
        return ElicitResult(action="accept", content=content)

    out = await tool(config, "start_interview", {"elicitation_callback": answer, "mode": mode}, path="game")
    assert out["saved_from_form"] and len(seen) == len(out["saved_from_form"])
    state = load_state(ProjectFiles(game / "game"))
    assert all(state.get(f).status == "approved" for f in out["saved_from_form"])


@pytest.fixture
def example(tmp_path: Path) -> Path:
    """The example game without translation languages (and so without a language decision yet)."""
    shutil.copytree(EXAMPLE.parent, tmp_path / "game")
    manifest = tmp_path / "game" / "steamworks.yaml"
    text = manifest.read_text("utf-8")
    manifest.write_text(text.replace("target_languages: [german, french, schinese]", "target_languages: []"), "utf-8")
    return tmp_path


@pytest.mark.anyio
async def test_languages_through_a_form(example: Path) -> None:
    config = Config(workspace_root=example)
    seen: dict[str, Any] = {}

    async def answer(context: Any, params: Any) -> ElicitResult:
        props = params.requested_schema["properties"]
        seen.update(props)
        return ElicitResult(action="accept", content={"source_language": "english", "target_languages": ["german"]})

    out = await tool(config, "start_interview", {"elicitation_callback": answer}, path="game", gate=1, max_questions=2)
    assert set(seen) == {"source_language", "target_languages"}
    assert seen["target_languages"]["default"] == ["german"]  # the supported languages besides the source
    assert out["saved_from_form"] == ["source_language", "target_languages"]
    assert ManifestFile.load(example / "game" / "steamworks.yaml").manifest.target_languages == ["german"]
    assert load_state(ProjectFiles(example / "game")).get("target_languages").status == "approved"


@pytest.mark.anyio
async def test_no_translations_is_an_answer(example: Path) -> None:
    config = Config(workspace_root=example)
    first = await tool(config, "start_interview", path="game", gate=1, use_form=False)
    assert [q["id"] for q in first["questions"]][:2] == ["source_language", "target_languages"]
    saved = await tool(config, "set_field", path="game", values={"target_languages": "none"})
    assert saved["status"]["target_languages"] == "missing"
    again = await tool(config, "start_interview", path="game", gate=1, use_form=False)
    assert not {"source_language", "target_languages"} & {q["id"] for q in again["questions"]}


@pytest.mark.anyio
async def test_the_source_language_is_never_a_target(example: Path) -> None:
    config = Config(workspace_root=example)
    out = await tool(
        config, "set_field", path="game", values={"source_language": "German", "target_languages": "German, English"}
    )
    assert not out.get("not_saved")
    m = ManifestFile.load(example / "game" / "steamworks.yaml").manifest
    assert (m.source_language, m.target_languages) == ("german", ["english"])


def test_spec_info_store_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {"type": "game", "genres": [{"description": "Action"}], "supported_languages": "English"}
    data = patterns.build([patterns.measure(i, page) for i in range(5)], dt.date(2026, 10, 3))
    monkeypatch.setattr(spec_info_module, "store_patterns", lambda: data)
    assert spec_info_module.spec_info("store_patterns")["appids"] == [0, 1, 2, 3, 4]
    action = spec_info_module.spec_info("store_patterns:action")
    assert action["genre"] == "Action" and action["games"] == 5
    with pytest.raises(ValueError, match="groups: Action"):
        spec_info_module.spec_info("store_patterns:Racing")
    monkeypatch.setattr(spec_info_module, "store_patterns", lambda: None)
    with pytest.raises(ValueError, match="build_store_patterns"):
        spec_info_module.spec_info("store_patterns")


@pytest.mark.anyio
async def test_get_spec_info(game: Path) -> None:
    config = Config(workspace_root=game)
    assert len((await tool(config, "get_spec_info", kind="gates"))["gates"]) == 4
    assert (await tool(config, "get_spec_info", kind="gate:3"))["gate"] == 3
    assert (await tool(config, "get_spec_info", kind="reference:3527290"))["appid"] == 3527290
    assert "guide" in await tool(config, "get_spec_info", kind="style_guide:coop_party")
    assert (await tool(config, "get_spec_info", kind="store_patterns"))["overall"]["games"] > 0
