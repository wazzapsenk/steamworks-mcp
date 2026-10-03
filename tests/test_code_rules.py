"""check_code: the Steamworks rules over small Unity, Godot and SteamPipe projects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from steamworks_mcp import present
from steamworks_mcp.validate import code

VALUES: dict[str, Any] = {
    "apps": {"main": {"appid": 1000000}, "demo": {"appid": 1000001}},
    "achievements": [{"id": "ACH_WIN"}, {"id": "ACH_SECRET"}],
    "stats": [{"name": "WINS"}],
}


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def found(out: dict[str, Any]) -> dict[str, list[tuple[str | None, int | None]]]:
    rules: dict[str, list[tuple[str | None, int | None]]] = {}
    for f in out["findings"]:
        rules.setdefault(f["rule"], []).append((f["file"], f["line"]))
    return rules


UNITY_BOOT = """using Steamworks;
using System.Runtime.Serialization.Formatters.Binary;
public class Boot : UnityEngine.MonoBehaviour {
    void Start() {
        // SteamAPI.Init();   a comment is not a call
        SteamAPI.Init();
        SteamUserStats.SetAchievement("ACH_WIN");
        SteamUserStats.SetAchievement("ACH_TYPO");
        SteamUserStats.SetStat("LOSSES", 1);
        UnityEngine.Screen.SetResolution(1920, 1080, true);
        if (UnityEngine.Input.GetMouseButtonDown(0)) { }
        UnityEngine.PlayerPrefs.SetInt("level", 3);
        var path = "C:\\\\Saves\\\\game.sav";
        var url = "https://example.com/not//a//comment";
        var bf = new BinaryFormatter();
        SteamNetworking.SendP2PPacket(id, data, len, EP2PSend.k_EP2PSendReliable);
        SteamUser.GetAuthSessionTicket(buf, 1024, out n, ref identity);
    }
}
"""


def test_rules_file_and_checks_agree() -> None:
    defined = code.rules()
    checked = {rule for ids, _ in code.CHECKS for rule in ids}
    assert set(defined) == checked
    groups = {g.id for g in code.rules_file().groups}
    assert {r.group for r in defined.values()} == groups
    assert all(r.source.startswith("https://") for r in defined.values())


def test_comments_are_stripped_but_not_inside_strings() -> None:
    text = 'a(); // x\nb("http://y"); /* c\nd */ e();\n# not a comment in C\n'
    stripped = code.strip_comments(text)
    assert stripped.count("\n") == text.count("\n")
    assert "x" not in stripped and '"http://y"' in stripped and "d */" not in stripped and "e();" in stripped
    assert code.strip_comments("x = 1 # note\n", hash_comments=True).strip() == "x = 1"


def test_unity_project(tmp_path: Path) -> None:
    root = write(tmp_path, {"steam_appid.txt": "480\n", "Assets/_Game/Scripts/Boot.cs": UNITY_BOOT})
    out = code.check_code(root, VALUES)
    rules = found(out)
    boot = "Assets/_Game/Scripts/Boot.cs"
    assert rules["appid_spacewar"] == [("steam_appid.txt", 1)]
    assert rules["init_result_unchecked"] == [(boot, 6)]
    for rule in ("callbacks_not_run", "no_restart_check", "no_shutdown"):
        assert rules[rule] == [(boot, 6)], rule  # the first real start call, not the commented one
    assert rules["stats_not_stored"] == [(boot, 7)]
    assert rules["achievement_unknown"] == [(boot, 8)]
    assert rules["stat_unknown"] == [(boot, 9)]
    assert rules["achievement_never_unlocked"] == [("steamworks.yaml", None)]
    assert rules["fixed_resolution"] == [(boot, 10)]
    assert rules["no_gamepad_input"] == [(boot, 11)]
    assert rules["registry_saves"] == [(boot, 12)]
    assert rules["windows_paths"] == [(boot, 13)]
    assert rules["binary_formatter"] == [(boot, 15)]
    assert rules["old_p2p_api"] == [(boot, 16)]
    assert rules["auth_ticket_unverified"] == [(boot, 17)]
    assert out["counts"]["error"] == 4
    shown = present.check_code(out)
    assert shown["summary"].startswith("4 problems to fix, ")
    assert (
        "| error | The result of the Steam API start is not checked | Assets/_Game/Scripts/Boot.cs:6 |"
        in shown["display"]
    )


def test_a_wrapper_in_a_plugin_folder_counts_for_companion_calls(tmp_path: Path) -> None:
    good = """using Steamworks;
public class Boot {
    void Start() {
        if (SteamAPI.RestartAppIfNecessary(new AppId_t(1000000))) { Quit(); return; }
        if (!SteamAPI.Init()) { Quit(); }
        UnityEngine.Gamepad.current?.MakeCurrent();
    }
    void OnDestroy() { SteamAPI.Shutdown(); }
}
"""
    vendor = "public class SteamManager { void Update() { Steamworks.SteamAPI.RunCallbacks(); } }\n"
    root = write(
        tmp_path,
        {"Assets/_Game/Boot.cs": good, "Assets/Plugins/Steamworks.NET/SteamManager.cs": vendor},
    )
    out = code.check_code(root, VALUES)
    assert out["findings"] == [] and out["files_checked"]["third_party_code"] == 1


def test_godot_project(tmp_path: Path) -> None:
    project = (
        '[application]\nconfig/name="Pillow"\n\n'
        '[input]\njump={"events": [Object(InputEventKey,"keycode":32)]}\n\n'
        "[steam]\ninitialization/app_id=480\n"
    )
    main = 'extends Node\n\nfunc _ready():\n\tSteam.steamInit()\n\tSteam.setAchievement("ACH_WIN")  # no storeStats\n'
    root = write(tmp_path, {"project.godot": project, "main.gd": main})
    rules = found(code.check_code(root, VALUES))
    assert rules["appid_spacewar"] == [("project.godot", 8)]
    assert rules["init_result_unchecked"] == [("main.gd", 4)]
    assert rules["callbacks_not_run"] == [("main.gd", 4)] and rules["no_restart_check"] == [("main.gd", 4)]
    assert rules["stats_not_stored"] == [("main.gd", 5)] and rules["no_gamepad_input"] == [("project.godot", None)]
    assert "no_shutdown" not in rules


def test_godot_embedded_callbacks(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "project.godot": "[steam]\ninitialization/app_id=1000000\ninitialization/embed_callbacks=true\n",
            "main.gd": "func _ready():\n\tvar r = Steam.steamInitEx()\n",
        },
    )
    assert "callbacks_not_run" not in found(code.check_code(root, VALUES))


def test_build_scripts(tmp_path: Path) -> None:
    app = """"AppBuild"
{
\t"AppID" "123456" // another app
\t"Desc" "test"
\t"ContentRoot" "../content/"
\t"SetLive" "default"
\t"Depots"
\t{
\t\t"1000002" "depot_build_1000002.vdf"
\t}
}
"""
    depot = """"DepotBuild"
{
\t"DepotID" "1000003"
\t"ContentRoot" "."
\t"FileMapping" { "LocalPath" "Game.exe" "DepotPath" "." }
\t"FileMapping" { "LocalPath" "*" "DepotPath" "." "Recursive" "1" }
}
"""
    root = write(
        tmp_path,
        {"steam/app_build_123456.vdf": app, "steam/depot_build_1000003.vdf": depot, "Builds/steam_appid.txt": "1"},
    )
    rules = found(code.check_code(root, VALUES))
    assert rules["appid_mismatch"] == [("steam/app_build_123456.vdf", 3)]
    assert rules["setlive_default"] == [("steam/app_build_123456.vdf", 6)]
    assert sorted(rules["build_script_paths"]) == [
        ("steam/app_build_123456.vdf", 5),
        ("steam/app_build_123456.vdf", 9),
        ("steam/depot_build_1000003.vdf", 5),
    ]
    assert rules["steam_appid_txt_in_build"] == [("Builds/steam_appid.txt", None)]


def test_secrets_are_found_but_never_shown(tmp_path: Path) -> None:
    key = "0123456789ABCDEF0123456789ABCDEF"
    root = write(
        tmp_path,
        {
            "tools/upload.bat": "steamcmd +login builder hunter2secret +run_app_build app.vdf +quit\n",
            ".github/workflows/ci.yml": "run: steamcmd +login ${{ secrets.USER }} ${{ secrets.PASS }} +quit\n",
            "docs/notes.txt": "steamcmd +login <builder account> +run_app_build x\n",
            "Assets/_Game/Net.cs": f'var u = "https://partner.steam-api.com/ISteamUser/X/v1/?key={key}";\n',
            ".env": f"STEAMWORKS_PUBLISHER_KEY={key}\n",
            "ssfn123456789": "x",
        },
    )
    out = code.check_code(root, VALUES)
    rules = found(out)
    assert rules["steamcmd_password"] == [("tools/upload.bat", 1)]
    assert rules["web_api_key_in_game"] == [("Assets/_Game/Net.cs", 1)]
    assert rules["steam_credential_files"] == [("ssfn123456789", None)]
    text = json.dumps(present.check_code(out))
    assert key not in text and "hunter2secret" not in text


def test_only_some_rules(tmp_path: Path) -> None:
    root = write(tmp_path, {"steam_appid.txt": "480\n", "Assets/_Game/Scripts/Boot.cs": UNITY_BOOT})
    out = code.check_code(root, VALUES, ["steamworks-app-id", "binary_formatter"])
    assert set(found(out)) == {"appid_spacewar", "binary_formatter"} and out["rules_run"] == 3
