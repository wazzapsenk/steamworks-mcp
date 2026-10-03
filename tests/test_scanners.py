"""Unity scanner and the Steam Build Pipeline plugin, on a small fictional Unity project."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steamworks_mcp.scanners import run_scanners
from steamworks_mcp.scanners.base import Finding, ScanResult
from steamworks_mcp.scanners.unity import UnityScanner, steam_language, third_party_folders
from steamworks_mcp.scanners.unity_yaml import documents

FIXTURE = Path(__file__).parent / "fixtures" / "unity_min_project"


@pytest.fixture(scope="module")
def results() -> dict[str, ScanResult]:
    return {r.scanner: r for r in run_scanners(FIXTURE)}


def finding(r: ScanResult, field: str) -> Finding:
    found = [f for f in r.findings if f.field == field]
    assert found, f"no finding for {field}: {[f.field for f in r.findings]}"
    return max(found, key=lambda f: f.confidence)


def test_both_scanners_ran(results: dict[str, ScanResult]) -> None:
    assert set(results) == {"unity", "steam_build_pipeline"}


def test_project_settings(results: dict[str, ScanResult]) -> None:
    u = results["unity"]
    assert finding(u, "game.engine.version").value == "6000.3.10f1"
    name = finding(u, "game.name")
    assert name.value == "Pillow Fort Panic" and name.confidence == 0.7
    assert name.evidence[0].file == "ProjectSettings/ProjectSettings.asset" and name.evidence[0].line
    assert finding(u, "store.developers").value == ["Example Studio"]
    assert u.facts["bundle_version"] == "0.4.2"


def test_appid_from_steam_appid_txt(results: dict[str, ScanResult]) -> None:
    f = finding(results["unity"], "apps.main.appid")
    assert f.value == 1000000 and f.evidence[0].file == "steam_appid.txt"


def test_achievements_stats_leaderboards_from_game_code_only(results: dict[str, ScanResult]) -> None:
    u = results["unity"]
    assert u.facts["steam_sdk"] == "steamworks.net"
    assert u.facts["code_achievements"] == ["ACH_FIRST_FORT", "ACH_TEN_FORTS"]  # not VENDOR_ACH (third-party folder)
    assert u.facts["achievement_progress_max"] == {"ACH_TEN_FORTS": 10}
    assert u.facts["code_stats"] == ["FORTS_BUILT"]  # not EDITOR_ONLY (editor code), not animator states
    assert finding(u, "achievements.ACH_FIRST_FORT").kind == "item"
    assert finding(u, "leaderboards.FASTEST_FORT.sort_method").value == "ascending"
    assert finding(u, "leaderboards.FASTEST_FORT.display_type").value == "seconds"
    ev = finding(u, "achievements.ACH_FIRST_FORT").evidence[0]
    assert ev.file == "Assets/_Game/Scripts/Achievements.cs" and ev.line == 5


def test_saves_map_to_auto_cloud_and_playerprefs_warns(results: dict[str, ScanResult]) -> None:
    u = results["unity"]
    roots = finding(u, "apps.main.cloud.auto_cloud").value
    assert roots == [
        {
            "root": "WinAppDataLocalLow",
            "subdirectory": "Example Studio/Pillow Fort Panic",
            "pattern": "*.sav",
            "os": "all",
            "recursive": True,
        }
    ]
    overrides = finding(u, "apps.main.cloud.overrides").value
    assert {o["use_instead"] for o in overrides} == {"MacAppSupport", "LinuxXdgConfigHome"}
    warning = next(w for w in u.warnings if w.code == "playerprefs_not_synced")
    assert warning.evidence[0].file == "Assets/_Game/Scripts/SaveSystem.cs"


def test_controller_localization_networking(results: dict[str, ScanResult]) -> None:
    u = results["unity"]
    assert finding(u, "game.platform_features.controller").value == "partial"
    assert finding(u, "target_languages").value == ["german"]
    assert finding(u, "store.supported_languages.german").value["interface"] is True
    assert u.facts["networking"] == ["netcode_for_gameobjects"]
    assert finding(u, "game.players.online_coop").confidence <= 0.3
    assert "VendorPack" in u.facts["third_party_folders"]


def test_steam_build_pipeline_maps_environments(results: dict[str, ScanResult]) -> None:
    s = results["steam_build_pipeline"]
    assert finding(s, "apps.main.appid").value == 1000000
    assert finding(s, "apps.demo.appid").value == 1000001
    depots = finding(s, "apps.main.builds.depots").value
    assert depots == [
        {"name": "windows", "depot_id": 1000002, "os": "windows", "arch": "64", "content_root": "Builds/Prod"}
    ]
    assert finding(s, "apps.main.builds.branches").value == [{"name": "beta"}]


def test_secrets_in_the_build_config_are_never_read(results: dict[str, ScanResult]) -> None:
    dumped = json.dumps(
        [{"findings": [f.__dict__ for f in r.findings], "facts": r.facts} for r in results.values()], default=str
    )
    assert "never_read_this_user" not in dumped
    assert "steamcmd.exe" not in dumped


def test_never_scans_ai_content(results: dict[str, ScanResult]) -> None:
    assert not any(f.field.startswith("content.") for r in results.values() for f in r.findings)


def test_placeholder_product_names_get_low_confidence(tmp_path: Path) -> None:
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.1f1\n")
    (tmp_path / "ProjectSettings" / "ProjectSettings.asset").write_text(
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!129 &1\nPlayerSettings:\n"
        "  companyName: DefaultCompany\n  productName: My project\n"
    )
    r = UnityScanner().scan(tmp_path)
    assert finding(r, "game.name").confidence == 0.3
    assert not [f for f in r.findings if f.field == "store.developers"]
    assert any(w.code == "no_steam_sdk" for w in r.warnings)


def test_spacewar_appid_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.1f1\n")
    (tmp_path / "steam_appid.txt").write_text("480")
    r = UnityScanner().scan(tmp_path)
    assert not [f for f in r.findings if f.field == "apps.main.appid"]
    assert any(w.code == "spacewar_appid" for w in r.warnings)


def test_unity_yaml_fallback() -> None:
    text = "%YAML 1.1\n--- !u!129 &1\nPlayerSettings:\n  productName: A: weird: value\n  bad: [unclosed\n"
    ((name, body),) = documents(text)
    assert name == "PlayerSettings" and body["productName"].startswith("A")


@pytest.mark.parametrize(
    ("code", "api"),
    [
        ("de", "german"),
        ("zh-Hans", "schinese"),
        ("pt-BR", "brazilian"),
        ("es-419", "latam"),
        ("ko", "koreana"),
        ("xx", None),
    ],
)
def test_locale_codes(code: str, api: str | None) -> None:
    assert steam_language(code) == api


def test_third_party_detection() -> None:
    assert third_party_folders(FIXTURE, "Pillow Fort Panic", "Example Studio") == {"VendorPack"}
