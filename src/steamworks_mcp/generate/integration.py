"""``integration_code``: Steamworks SDK code that matches steamworks.yaml, for the game's engine.

The API names (achievements, stats, leaderboards) and the app id come from steamworks.yaml, so the code and Steamworks
can never disagree on a name. Files go to ``.steam-mcp/exports/code/<engine>/``; the server never writes into the game
project. Supported: Unity with Steamworks.NET or Facepunch.Steamworks, Godot with GodotSteam 4, Unreal (the engine's
Steam online subsystem plus direct SDK calls) and plain C++ with the Steamworks SDK.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from steamworks_mcp.manifest.io import atomic_write

Target = Literal["unity-steamworks-net", "unity-facepunch", "godot", "unreal", "cpp"]
FEATURES = ("init", "achievements", "stats", "leaderboards", "cloud")
TARGETS: dict[str, str] = {
    "unity-steamworks-net": "Unity + Steamworks.NET",
    "unity-facepunch": "Unity + Facepunch.Steamworks",
    "godot": "Godot 4 + GodotSteam 4",
    "unreal": "Unreal Engine (Online Subsystem Steam + Steamworks SDK)",
    "cpp": "C++ + Steamworks SDK",
}
SORT = {"ascending": 1, "descending": 2}
DISPLAY = {"numeric": 1, "seconds": 2, "milliseconds": 3}


def fill(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"@@{key}@@", value)
    leftover = re.findall(r"@@[A-Z_]+@@", out)
    assert not leftover, leftover
    return out.lstrip("\n")


def detect_target(values: dict[str, Any], root: Path) -> Target:
    """From game.engine.name, the project files and the wrapper the code already uses."""
    engine = str(((values.get("game") or {}).get("engine") or {}).get("name") or "").lower()
    if not engine:
        if (root / "ProjectSettings").is_dir():
            engine = "unity"
        elif (root / "project.godot").is_file():
            engine = "godot"
        elif any(root.glob("*.uproject")):
            engine = "unreal"
    if engine == "unity":
        assets = root / "Assets"
        if assets.is_dir():
            for path in assets.rglob("*.cs"):
                try:
                    if "SteamClient.Init" in path.read_text(encoding="utf-8", errors="replace"):
                        return "unity-facepunch"
                except OSError:
                    continue
        return "unity-steamworks-net"
    if engine in ("godot", "unreal"):
        return engine  # type: ignore[return-value]
    return "cpp"


def _ids(values: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        [a for a in values.get("achievements") or [] if a.get("id")],
        [s for s in values.get("stats") or [] if s.get("name")],
        [b for b in values.get("leaderboards") or [] if b.get("name")],
    )


def _tail(text: str, mark: str) -> str:
    return f"  {mark} {text}" if text else ""


def _comment(a: dict[str, Any]) -> str:
    text = a.get("name") or a.get("display_name") or ""
    desc = a.get("description") or ""
    return " ".join(f"{text}: {desc}".strip(": ").split())[:100]


# ---------------------------------------------------------------------------------------------------- Unity


UNITY_BOOT = """
// Starts Steam once for the whole game. Put it on a GameObject in the first scene.
// steam_appid.txt with @@APP_ID@@ next to the Unity editor / the game's .exe is only for development: leave it out of
// the builds you upload.
using Steamworks;
using UnityEngine;

public class SteamBootstrap : MonoBehaviour
{
    public const uint AppId = @@APP_ID@@;
    public static bool Initialized { get; private set; }
    static SteamBootstrap instance;

    void Awake()
    {
        if (instance != null) { Destroy(gameObject); return; }
        instance = this;
        DontDestroyOnLoad(gameObject);
        try
        {
            // Started from the .exe instead of Steam: relaunch through Steam and quit this copy.
            if (SteamAPI.RestartAppIfNecessary(new AppId_t(AppId))) { Application.Quit(); return; }
        }
        catch (System.DllNotFoundException e)
        {
            Debug.LogError("[Steam] steam_api library not found: " + e.Message);
            Application.Quit();
            return;
        }
        Initialized = SteamAPI.Init();
        if (!Initialized)
            Debug.LogError("[Steam] SteamAPI.Init failed: is Steam running, and is the app id right?");
    }

    void Update()
    {
        if (Initialized) SteamAPI.RunCallbacks();
    }

    void OnApplicationQuit()
    {
        if (!Initialized) return;
        SteamAPI.Shutdown();
        Initialized = false;
    }
}
"""

UNITY_FACEPUNCH_BOOT = """
// Starts Steam once for the whole game. Put it on a GameObject in the first scene.
// Facepunch.Steamworks runs Steam's callbacks itself.
using UnityEngine;

public class SteamBootstrap : MonoBehaviour
{
    public const uint AppId = @@APP_ID@@;
    public static bool Initialized { get; private set; }
    static SteamBootstrap instance;

    void Awake()
    {
        if (instance != null) { Destroy(gameObject); return; }
        instance = this;
        DontDestroyOnLoad(gameObject);
#if !UNITY_EDITOR
        // Started from the .exe instead of Steam: relaunch through Steam and quit this copy.
        if (Steamworks.SteamClient.RestartAppIfNecessary(AppId)) { Application.Quit(); return; }
#endif
        try
        {
            Steamworks.SteamClient.Init(AppId);
            Initialized = true;
        }
        catch (System.Exception e)
        {
            Debug.LogError("[Steam] Steam did not start: " + e.Message);
        }
    }

    void OnApplicationQuit()
    {
        if (!Initialized) return;
        Steamworks.SteamClient.Shutdown();
        Initialized = false;
    }
}
"""

UNITY_IDS = """
// Steam API names from steamworks.yaml. Regenerate with integration_code when they change; a wrong name fails silently.
public static class SteamIds
{
    public static class Achievements
    {
@@ACHIEVEMENTS@@
    }

    public static class Stats
    {
@@STATS@@
    }

    public static class Leaderboards
    {
@@LEADERBOARDS@@
    }
}
"""

UNITY_STATS_NET = """
// Achievements and stats (Steamworks.NET). Nothing reaches Steam until StoreStats: these helpers call it for you.
using Steamworks;

public static class SteamProgress
{
    public static void Unlock(string achievement)
    {
        if (!SteamBootstrap.Initialized) return;
        SteamUserStats.GetAchievement(achievement, out bool done);
        if (done) return;
        SteamUserStats.SetAchievement(achievement);
        SteamUserStats.StoreStats();
    }

    /// Shows "current/max" progress on screen; unlock with Unlock when it is reached.
    public static void ShowProgress(string achievement, uint current, uint max)
    {
        if (SteamBootstrap.Initialized) SteamUserStats.IndicateAchievementProgress(achievement, current, max);
    }

    public static void SetStat(string stat, int value)
    {
        if (!SteamBootstrap.Initialized) return;
        SteamUserStats.SetStat(stat, value);
        SteamUserStats.StoreStats();
    }

    public static void SetStat(string stat, float value)
    {
        if (!SteamBootstrap.Initialized) return;
        SteamUserStats.SetStat(stat, value);
        SteamUserStats.StoreStats();
    }

    public static int GetIntStat(string stat)
    {
        return SteamBootstrap.Initialized && SteamUserStats.GetStat(stat, out int value) ? value : 0;
    }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
    /// For testing only: locks an achievement again.
    public static void ResetAchievement(string achievement)
    {
        if (!SteamBootstrap.Initialized) return;
        SteamUserStats.ClearAchievement(achievement);
        SteamUserStats.StoreStats();
    }
#endif
}
"""

UNITY_STATS_FACEPUNCH = """
// Achievements and stats (Facepunch.Steamworks). Trigger() stores the achievement; stats need StoreStats.
using Steamworks;

public static class SteamProgress
{
    public static void Unlock(string achievement)
    {
        if (!SteamBootstrap.Initialized) return;
        var a = new Steamworks.Data.Achievement(achievement);
        if (!a.State) a.Trigger();
    }

    /// Shows "current/max" progress on screen; unlock with Unlock when it is reached.
    public static void ShowProgress(string achievement, int current, int max)
    {
        if (SteamBootstrap.Initialized) SteamUserStats.IndicateAchievementProgress(achievement, current, max);
    }

    public static void SetStat(string stat, int value)
    {
        if (!SteamBootstrap.Initialized) return;
        SteamUserStats.SetStat(stat, value);
        SteamUserStats.StoreStats();
    }

    public static void SetStat(string stat, float value)
    {
        if (!SteamBootstrap.Initialized) return;
        SteamUserStats.SetStat(stat, value);
        SteamUserStats.StoreStats();
    }

    public static int GetIntStat(string stat)
    {
        return SteamBootstrap.Initialized ? SteamUserStats.GetStatInt(stat) : 0;
    }

#if UNITY_EDITOR || DEVELOPMENT_BUILD
    /// For testing only: locks an achievement again.
    public static void ResetAchievement(string achievement)
    {
        if (SteamBootstrap.Initialized) new Steamworks.Data.Achievement(achievement).Clear();
    }
#endif
}
"""

UNITY_BOARDS_NET = """
// Leaderboard uploads (Steamworks.NET). The leaderboards must exist in Steamworks first (apply section
// "leaderboards", or by hand); KeepBest only replaces a worse score.
using System.Collections.Generic;
using Steamworks;

public static class SteamLeaderboards
{
    static readonly Dictionary<string, SteamLeaderboard_t> Boards = new Dictionary<string, SteamLeaderboard_t>();
    static readonly Dictionary<string, CallResult<LeaderboardFindResult_t>> Finding =
        new Dictionary<string, CallResult<LeaderboardFindResult_t>>();
    static readonly List<CallResult<LeaderboardScoreUploaded_t>> Uploading =
        new List<CallResult<LeaderboardScoreUploaded_t>>();

    public static void Upload(string leaderboard, int score)
    {
        if (!SteamBootstrap.Initialized) return;
        if (Boards.TryGetValue(leaderboard, out var board)) { UploadTo(board, score); return; }
        var call = CallResult<LeaderboardFindResult_t>.Create((result, ioFailure) =>
        {
            Finding.Remove(leaderboard);
            if (ioFailure || result.m_bLeaderboardFound == 0)
            {
                UnityEngine.Debug.LogWarning("[Steam] Leaderboard not found: " + leaderboard);
                return;
            }
            Boards[leaderboard] = result.m_hSteamLeaderboard;
            UploadTo(result.m_hSteamLeaderboard, score);
        });
        call.Set(SteamUserStats.FindLeaderboard(leaderboard));
        Finding[leaderboard] = call;
    }

    static void UploadTo(SteamLeaderboard_t board, int score)
    {
        var call = CallResult<LeaderboardScoreUploaded_t>.Create(
            (result, ioFailure) => Uploading.RemoveAll(c => !c.IsActive()));
        call.Set(SteamUserStats.UploadLeaderboardScore(
            board, ELeaderboardUploadScoreMethod.k_ELeaderboardUploadScoreMethodKeepBest, score, null, 0));
        Uploading.Add(call);
    }
}
"""

UNITY_BOARDS_FACEPUNCH = """
// Leaderboard uploads (Facepunch.Steamworks). The leaderboards must exist in Steamworks first (apply section
// "leaderboards", or by hand). SubmitScoreAsync only replaces a worse score.
using System.Threading.Tasks;
using Steamworks;

public static class SteamLeaderboards
{
    public static async Task Upload(string leaderboard, int score)
    {
        if (!SteamBootstrap.Initialized) return;
        var board = await SteamUserStats.FindLeaderboardAsync(leaderboard);
        if (board.HasValue) await board.Value.SubmitScoreAsync(score);
        else UnityEngine.Debug.LogWarning("[Steam] Leaderboard not found: " + leaderboard);
    }
}
"""

UNITY_SAVES = """
// Where saves go so Steam Auto-Cloud finds them. Application.persistentDataPath is
// %USERPROFILE%/AppData/LocalLow/<company>/<product> on Windows, ~/Library/Application Support/<company>/<product>
// on macOS and ~/.config/unity3d/<company>/<product> on Linux and Steam Deck.
using System.IO;
using UnityEngine;

public static class SaveLocation
{
    public static string Folder => Application.persistentDataPath;

    public static string File(string name) => Path.Combine(Folder, name);

    /// Write to a temporary file first, so a crash never leaves half a save for Steam Cloud to upload.
    public static void Write(string name, string contents)
    {
        var path = File(name);
        var temp = path + ".tmp";
        System.IO.File.WriteAllText(temp, contents);
        if (System.IO.File.Exists(path)) System.IO.File.Replace(temp, path, null);
        else System.IO.File.Move(temp, path);
    }
}
"""


def _cs_const(name: str, comment: str = "") -> str:
    tail = f" // {comment}" if comment else ""
    return f'        public const string {name} = "{name}";{tail}'


def unity(values: dict[str, Any], features: set[str], facepunch: bool) -> dict[str, str]:
    appid = str(((values.get("apps") or {}).get("main") or {}).get("appid") or 480)
    achievements, stats, boards = _ids(values)
    files: dict[str, str] = {}
    if "init" in features:
        files["SteamBootstrap.cs"] = fill(UNITY_FACEPUNCH_BOOT if facepunch else UNITY_BOOT, APP_ID=appid)
    if features & {"achievements", "stats", "leaderboards"}:
        files["SteamIds.cs"] = fill(
            UNITY_IDS,
            ACHIEVEMENTS="\n".join(_cs_const(a["id"], _comment(a)) for a in achievements) or "        // none yet",
            STATS="\n".join(_cs_const(s["name"], s.get("type", "int")) for s in stats) or "        // none yet",
            LEADERBOARDS="\n".join(
                _cs_const(b["name"], f"{b.get('sort_method', 'descending')}, {b.get('display_type', 'numeric')}")
                for b in boards
            )
            or "        // none yet",
        )
    if features & {"achievements", "stats"}:
        files["SteamProgress.cs"] = fill(UNITY_STATS_FACEPUNCH if facepunch else UNITY_STATS_NET)
    if "leaderboards" in features and boards:
        files["SteamLeaderboards.cs"] = fill(UNITY_BOARDS_FACEPUNCH if facepunch else UNITY_BOARDS_NET)
    if "cloud" in features:
        files["SaveLocation.cs"] = fill(UNITY_SAVES)
    return files


# ---------------------------------------------------------------------------------------------------- Godot


GODOT_STEAM = """
# Steam for the whole game (GodotSteam 4). Add it as an autoload named "SteamService"
# (Project > Project Settings > Globals). API names come from steamworks.yaml.
extends Node

const APP_ID := @@APP_ID@@

const ACHIEVEMENTS := {
@@ACHIEVEMENTS@@
}
const STATS := {
@@STATS@@
}
const LEADERBOARDS := {
@@LEADERBOARDS@@
}

var initialized := false
var _boards := {}
var _pending_scores := {}


func _init() -> void:
	# Lets the editor and exported test builds start Steam without steam_appid.txt.
	OS.set_environment("SteamAppId", str(APP_ID))
	OS.set_environment("SteamGameId", str(APP_ID))


func _ready() -> void:
	if not OS.is_debug_build() and Steam.restartAppIfNecessary(APP_ID):
		get_tree().quit()  # started from the executable: Steam starts the game again
		return
	var result: Dictionary = Steam.steamInitEx(true, APP_ID)
	initialized = result.get("status", -1) == 0  # 0 = STEAM_API_INIT_RESULT_OK
	if not initialized:
		push_error("Steam did not start: %s" % result.get("verbal", "unknown error"))
		return
	Steam.leaderboard_find_result.connect(_on_leaderboard_found)


func _process(_delta: float) -> void:
	if initialized:
		Steam.run_callbacks()


# Achievements and stats: nothing reaches Steam until storeStats, so these helpers call it.
func unlock(achievement: String) -> void:
	if not initialized:
		return
	var state: Dictionary = Steam.getAchievement(achievement)
	if state.get("achieved", false):
		return
	Steam.setAchievement(achievement)
	Steam.storeStats()


func show_progress(achievement: String, current: int, maximum: int) -> void:
	if initialized:
		Steam.indicateAchievementProgress(achievement, current, maximum)


func set_stat(stat: String, value: float) -> void:
	if not initialized:
		return
	if STATS.get(stat, "int") == "int":
		Steam.setStatInt(stat, int(value))
	else:
		Steam.setStatFloat(stat, value)
	Steam.storeStats()


# Leaderboards must exist in Steamworks first; keep_best only replaces a worse score.
func upload_score(leaderboard: String, score: int) -> void:
	if not initialized:
		return
	if _boards.has(leaderboard):
		Steam.uploadLeaderboardScore(score, true, PackedInt32Array(), _boards[leaderboard])
		return
	_pending_scores[leaderboard] = score
	Steam.findLeaderboard(leaderboard)


func _on_leaderboard_found(handle: int, found: int) -> void:
	if found == 0:
		return
	var leaderboard: String = Steam.getLeaderboardName(handle)
	_boards[leaderboard] = handle
	if _pending_scores.has(leaderboard):
		Steam.uploadLeaderboardScore(_pending_scores[leaderboard], true, PackedInt32Array(), handle)
		_pending_scores.erase(leaderboard)
"""

GODOT_SAVES = """
# Where saves go so Steam Auto-Cloud finds them: user:// is %APPDATA%/Godot/app_userdata/<project name> on Windows,
# ~/Library/Application Support/Godot/app_userdata/<project name> on macOS and
# ~/.local/share/godot/app_userdata/<project name> on Linux and Steam Deck (or the custom user folder when
# application/config/use_custom_user_dir is on).
class_name SaveLocation


static func path(file_name: String) -> String:
	return "user://" + file_name


## Writes to a temporary file first, so a crash never leaves half a save for Steam Cloud to upload.
static func write(file_name: String, contents: String) -> void:
	var temp := path(file_name) + ".tmp"
	var f := FileAccess.open(temp, FileAccess.WRITE)
	f.store_string(contents)
	f.close()
	DirAccess.rename_absolute(temp, path(file_name))
"""


def godot(values: dict[str, Any], features: set[str]) -> dict[str, str]:
    appid = str(((values.get("apps") or {}).get("main") or {}).get("appid") or 480)
    achievements, stats, boards = _ids(values)
    files: dict[str, str] = {}
    if features & {"init", "achievements", "stats", "leaderboards"}:
        files["steam_service.gd"] = fill(
            GODOT_STEAM,
            APP_ID=appid,
            ACHIEVEMENTS="\n".join(f'\t"{a["id"]}": "{a["id"]}",' + _tail(_comment(a), "#") for a in achievements),
            STATS="\n".join(f'\t"{s["name"]}": "{s.get("type", "int")}",' for s in stats),
            LEADERBOARDS="\n".join(
                f'\t"{b["name"]}": {{"sort": {SORT[b.get("sort_method", "descending")]}, '
                f'"display": {DISPLAY[b.get("display_type", "numeric")]}}},'
                for b in boards
            ),
        )
    if "cloud" in features:
        files["save_location.gd"] = fill(GODOT_SAVES)
    return files


# ---------------------------------------------------------------------------------------------------- C++ / Unreal


CPP_HEADER = """
// Steam helpers generated from steamworks.yaml. Needs the Steamworks SDK (steam/steam_api.h) and steam_api(64)
// next to the executable. Nothing reaches Steam until StoreStats: the helpers call it for you.
#pragma once
@@INCLUDE@@

namespace SteamGame
{
    constexpr AppId_t AppId = @@APP_ID@@;

    namespace Achievements
    {
@@ACHIEVEMENTS@@
    }

    namespace Stats
    {
@@STATS@@
    }

    namespace Leaderboards
    {
@@LEADERBOARDS@@
    }
@@LIFECYCLE@@
    inline void Unlock(const char* achievement)
    {
        if (!SteamUserStats()) return;
        bool done = false;
        if (SteamUserStats()->GetAchievement(achievement, &done) && done) return;
        SteamUserStats()->SetAchievement(achievement);
        SteamUserStats()->StoreStats();
    }

    inline void ShowProgress(const char* achievement, uint32 current, uint32 max)
    {
        if (SteamUserStats()) SteamUserStats()->IndicateAchievementProgress(achievement, current, max);
    }

    inline void SetStat(const char* stat, int32 value)
    {
        if (!SteamUserStats()) return;
        SteamUserStats()->SetStat(stat, value);
        SteamUserStats()->StoreStats();
    }

    inline void SetStat(const char* stat, float value)
    {
        if (!SteamUserStats()) return;
        SteamUserStats()->SetStat(stat, value);
        SteamUserStats()->StoreStats();
    }

    // Uploads a score once the leaderboard is found; it must exist in Steamworks first. KeepBest only replaces a
    // worse score. Keep one instance alive (e.g. a member of your game class): call results need it.
    class LeaderboardUpload
    {
    public:
        void Upload(const char* leaderboard, int32 score)
        {
            if (!SteamUserStats()) return;
            m_score = score;
            m_find.Set(SteamUserStats()->FindLeaderboard(leaderboard), this, &LeaderboardUpload::OnFound);
        }

    private:
        void OnFound(LeaderboardFindResult_t* result, bool ioFailure)
        {
            if (ioFailure || !result->m_bLeaderboardFound) return;
            SteamUserStats()->UploadLeaderboardScore(
                result->m_hSteamLeaderboard, k_ELeaderboardUploadScoreMethodKeepBest, m_score, nullptr, 0);
        }

        CCallResult<LeaderboardUpload, LeaderboardFindResult_t> m_find;
        int32 m_score = 0;
    };
}
"""

CPP_LIFECYCLE = """
    // Call Start() first thing in main(); quit when it returns false. Call Tick() once per frame, Stop() on exit.
    inline bool Start()
    {
        if (SteamAPI_RestartAppIfNecessary(AppId)) return false;  // started outside Steam: Steam relaunches it
        SteamErrMsg error;
        if (SteamAPI_InitEx(&error) != k_ESteamAPIInitResult_OK)
        {
            // Show `error` to the player: Steam is not running, or the app id is wrong.
            return false;
        }
        return true;
    }

    inline void Tick() { SteamAPI_RunCallbacks(); }

    inline void Stop() { SteamAPI_Shutdown(); }
"""

UNREAL_INI = """
; Add to Config/DefaultEngine.ini. The engine's Steam online subsystem starts Steam, runs its callbacks and shuts
; it down, so the game code only calls the helpers in SteamGame.h. Check the keys against your engine version's
; Online Subsystem Steam documentation.
[/Script/Engine.GameEngine]
+NetDriverDefinitions=(DefName="GameNetDriver",DriverClassName="OnlineSubsystemSteam.SteamNetDriver",DriverClassNameFallback="OnlineSubsystemUtils.IpNetDriver")

[OnlineSubsystem]
DefaultPlatformService=Steam

[OnlineSubsystemSteam]
bEnabled=true
SteamDevAppId=@@APP_ID@@
"""

UNREAL_BUILD = """
// In <Game>.Build.cs, inside the constructor: the Steam online subsystem, and the Steamworks SDK for SteamGame.h.
PublicDependencyModuleNames.AddRange(new string[] { "OnlineSubsystem", "OnlineSubsystemSteam" });
AddEngineThirdPartyPrivateStaticDependencies(Target, "Steamworks");

// And enable the plugin in <Game>.uproject:
// "Plugins": [ { "Name": "OnlineSubsystemSteam", "Enabled": true } ]
"""


def _cpp_ids(values: dict[str, Any]) -> dict[str, str]:
    achievements, stats, boards = _ids(values)
    return {
        "ACHIEVEMENTS": "\n".join(
            f'        constexpr const char* {a["id"]} = "{a["id"]}";' + _tail(_comment(a), "//") for a in achievements
        )
        or "        // none yet",
        "STATS": "\n".join(
            f'        constexpr const char* {s["name"]} = "{s["name"]}";  // {s.get("type", "int")}' for s in stats
        )
        or "        // none yet",
        "LEADERBOARDS": "\n".join(f'        constexpr const char* {b["name"]} = "{b["name"]}";' for b in boards)
        or "        // none yet",
    }


def cpp(values: dict[str, Any], features: set[str], unreal_engine: bool) -> dict[str, str]:
    appid = str(((values.get("apps") or {}).get("main") or {}).get("appid") or 480)
    include = (
        'THIRD_PARTY_INCLUDES_START\n#include "steam/steam_api.h"\nTHIRD_PARTY_INCLUDES_END'
        if unreal_engine
        else '#include "steam/steam_api.h"'
    )
    files: dict[str, str] = {}
    if features & {"init", "achievements", "stats", "leaderboards"}:
        lifecycle = "" if unreal_engine or "init" not in features else CPP_LIFECYCLE
        files["SteamGame.h"] = fill(CPP_HEADER, INCLUDE=include, APP_ID=appid, LIFECYCLE=lifecycle, **_cpp_ids(values))
    if unreal_engine and "init" in features:
        files["DefaultEngine.steam.ini"] = fill(UNREAL_INI, APP_ID=appid)
        files["Build.cs.steam.txt"] = fill(UNREAL_BUILD)
    return files


# ---------------------------------------------------------------------------------------------------- entry


def generate(
    values: dict[str, Any],
    root: Path,
    out_dir: Path,
    features: list[str] | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    wanted = set(features or FEATURES)
    unknown = wanted - set(FEATURES)
    if unknown:
        raise ValueError(f"Unknown feature(s) {', '.join(sorted(unknown))}; features: {', '.join(FEATURES)}.")
    chosen = target or detect_target(values, root)
    if chosen not in TARGETS:
        raise ValueError(f"Unknown target {chosen!r}; targets: {', '.join(TARGETS)}.")
    if chosen.startswith("unity"):
        files = unity(values, wanted, chosen == "unity-facepunch")
    elif chosen == "godot":
        files = godot(values, wanted)
    else:
        files = cpp(values, wanted, chosen == "unreal")
    folder = out_dir / chosen
    for name, text in files.items():
        atomic_write(folder / name, text)
    achievements, stats, boards = _ids(values)
    notes = []
    appid = ((values.get("apps") or {}).get("main") or {}).get("appid")
    if not appid:
        notes.append("apps.main.appid is not set: the code uses 480 (Valve's test app) until it is.")
    if "achievements" in wanted and not achievements:
        notes.append("No achievements in steamworks.yaml yet: generate(section='achievements') drafts them.")
    if "leaderboards" in wanted and not boards:
        notes.append("No leaderboards in steamworks.yaml, so no leaderboard code.")
    if "cloud" in wanted and not ((values.get("apps") or {}).get("main") or {}).get("cloud", {}).get("auto_cloud"):
        notes.append("Steam Auto-Cloud has no paths yet: generate(section='cloud') drafts them from the save folder.")
    return {
        "target": chosen,
        "target_name": TARGETS[chosen],
        "folder": folder.as_posix(),
        "files": dict(files),
        "names_from_steamworks_yaml": {
            "achievements": [a["id"] for a in achievements],
            "stats": [s["name"] for s in stats],
            "leaderboards": [b["name"] for b in boards],
        },
        "notes": notes,
    }
