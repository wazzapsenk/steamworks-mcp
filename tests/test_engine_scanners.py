"""The Godot and Unreal scanners, on small projects, end to end through init_project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from steamworks_mcp.config import Config
from steamworks_mcp.manifest.io import ManifestFile
from steamworks_mcp.scanners import run_scanners
from steamworks_mcp.server import create_server

GODOT_PROJECT = """config_version=5

[application]

config/name="Pillow Siege"
config/features=PackedStringArray("4.3", "Forward Plus")

[input]

jump={
"deadzone": 0.5,
"events": [Object(InputEventJoypadButton,"button_index":0)]
}

[internationalization]

locale/translations=PackedStringArray("res://loc/game.en.translation", "res://loc/game.de.translation", "res://loc/game.pt_BR.translation")

[steam]

initialization/app_id=1234560
"""
GODOT_PRESETS = """[preset.0]

name="Windows Desktop"
platform="Windows Desktop"

[preset.1]

name="Linux"
platform="Linux"
"""
GODOT_MAIN = """extends Node

func win() -> void:
\tSteam.setAchievement("ACH_WIN")
\tSteam.setStatFloat("BEST_TIME", 12.5)
\tSteam.storeStats()
\tSteam.findOrCreateLeaderboard("FASTEST", Steam.LEADERBOARD_SORT_METHOD_ASCENDING, 2)

func save() -> void:
\tvar f := FileAccess.open("user://slot1.sav", FileAccess.WRITE)
"""

UPROJECT = {
    "FileVersion": 3,
    "EngineAssociation": "5.4",
    "Plugins": [{"Name": "OnlineSubsystemSteam", "Enabled": True}],
    "TargetPlatforms": ["Windows", "Linux"],
}
DEFAULT_GAME = """[/Script/EngineSettings.GeneralProjectSettings]
ProjectName=Blanket Wars
CompanyName=Example Studio
ProjectVersion=0.3
"""
DEFAULT_ENGINE = """[OnlineSubsystem]
DefaultPlatformService=Steam

[OnlineSubsystemSteam]
bEnabled=true
SteamDevAppId=480
Achievement_0_Id="ACH_FIRST_BLOOD"
"""
UNREAL_CPP = """#include "SaveSystem.h"
void USaveSystem::Save() { UGameplayStatics::SaveGameToSlot(Data, TEXT("Slot1"), 0); }
void UStats::Win() { SteamUserStats()->SetAchievement("ACH_WIN"); SteamUserStats()->StoreStats(); }
"""


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def findings(root: Path) -> dict[str, Any]:
    results = run_scanners(root)
    assert results, root
    return {f.field: f.value for r in results for f in r.findings}


def test_godot(tmp_path: Path) -> None:
    root = write(
        tmp_path / "game",
        {
            "project.godot": GODOT_PROJECT,
            "export_presets.cfg": GODOT_PRESETS,
            "scripts/main.gd": GODOT_MAIN,
            "addons/godotsteam/plugin.gd": 'Steam.setAchievement("VENDOR_ONLY")\n',
        },
    )
    f = findings(root)
    assert f["game.engine.name"] == "godot" and f["game.engine.version"] == "4.3" and f["game.name"] == "Pillow Siege"
    assert f["store.platforms"] == ["linux", "windows"] and f["apps.main.appid"] == 1234560
    assert "achievements.ACH_WIN" in f and "achievements.VENDOR_ONLY" not in f
    assert f["stats.BEST_TIME.type"] == "float" and f["leaderboards.FASTEST.sort_method"] == "ascending"
    assert f["target_languages"] == ["brazilian", "german"]
    assert f["game.platform_features.controller"] == "partial"
    cloud = f["apps.main.cloud.auto_cloud"][0]
    assert cloud["root"] == "WinAppDataRoaming" and cloud["subdirectory"] == "Godot/app_userdata/Pillow Siege"
    assert cloud["pattern"] == "*.sav"
    linux = next(o for o in f["apps.main.cloud.overrides"] if o["os"] == "linux")
    assert linux["add_path"] == "godot/app_userdata/Pillow Siege" and linux["replace_path"] is True


def test_unreal(tmp_path: Path) -> None:
    root = write(
        tmp_path / "game",
        {
            "BlanketWars.uproject": json.dumps(UPROJECT),
            "Config/DefaultGame.ini": DEFAULT_GAME,
            "Config/DefaultEngine.ini": DEFAULT_ENGINE,
            "Config/DefaultInput.ini": '+ActionMappings=(ActionName="Jump",Key=Gamepad_FaceButton_Bottom)\n',
            "Content/Localization/Game/de/Game.locres": "x",
            "Content/Localization/Game/ja/Game.locres": "x",
            "Source/BlanketWars/SaveSystem.cpp": UNREAL_CPP,
        },
    )
    results = run_scanners(root)
    f = {x.field: x.value for r in results for x in r.findings}
    assert f["game.engine.name"] == "unreal" and f["game.engine.version"] == "5.4" and f["game.name"] == "Blanket Wars"
    assert f["store.developers"] == ["Example Studio"] and f["store.platforms"] == ["linux", "windows"]
    assert "apps.main.appid" not in f  # 480 is a warning, not a value
    assert any(w.code == "spacewar_appid" for r in results for w in r.warnings)
    assert "achievements.ACH_FIRST_BLOOD" in f and "achievements.ACH_WIN" in f
    assert f["target_languages"] == ["german", "japanese"] and f["game.platform_features.controller"] == "partial"
    assert f["apps.main.cloud.auto_cloud"][0]["subdirectory"] == "BlanketWars/Saved/SaveGames"


def test_findings_fit_steamworks_yaml(tmp_path: Path) -> None:
    write(
        tmp_path / "godot",
        {"project.godot": GODOT_PROJECT, "export_presets.cfg": GODOT_PRESETS, "scripts/main.gd": GODOT_MAIN},
    )
    write(
        tmp_path / "unreal",
        {
            "BlanketWars.uproject": json.dumps(UPROJECT),
            "Config/DefaultGame.ini": DEFAULT_GAME,
            "Config/DefaultEngine.ini": DEFAULT_ENGINE.replace("480", "1234560"),
            "Source/BlanketWars/SaveSystem.cpp": UNREAL_CPP,
        },
    )

    async def init(path: str) -> dict[str, Any]:
        async with Client(create_server(Config(workspace_root=tmp_path))) as client:
            result = await client.call_tool("init_project", {"path": path})
        assert not result.is_error, getattr(result.content[0], "text", "")
        return dict(result.structured_content or {})

    for path, appid in (("godot", 1234560), ("unreal", 1234560)):
        out = anyio.run(init, path)
        assert out["scan"]["counts"]["rejected"] == 0, out["scan"]["rejected"]
        manifest = ManifestFile.load(tmp_path / path / "steamworks.yaml").manifest
        assert manifest.apps.main.appid == appid and manifest.achievements
