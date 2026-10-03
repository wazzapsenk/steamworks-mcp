"""Unreal Engine projects: the .uproject, Config/*.ini and the game's C++ (read-only).

Finds the name, company and engine version, the target platforms, the Steam app id and achievements of the Steam
online subsystem (DefaultEngine.ini), achievements, stats and leaderboards used in C++, where SaveGame slots go for
Steam Auto-Cloud, gamepad input mappings and the localization cultures.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from steamworks_mcp.manifest.state import Evidence
from steamworks_mcp.scanners.base import Finding, ScanResult, ScanWarning, ev, iter_files, line_of, read_text, rel
from steamworks_mcp.scanners.unity import ACHIEVEMENT_CALLS, LEADERBOARD_CALLS, SPACEWAR, STAT_CALLS, steam_language

PLATFORMS = {"windows": "windows", "win64": "windows", "linux": "linux", "mac": "macos"}
OSS_ACHIEVEMENT = re.compile(r"^\s*Achievement_\d+_Id\s*=\s*\"?([A-Za-z0-9_]+)\"?", re.M)
SAVE_SLOT = re.compile(r"\bUGameplayStatics::(?:SaveGameToSlot|AsyncSaveGameToSlot)\b")
GAMEPAD = re.compile(r"Gamepad_\w+")
NETWORKING = re.compile(r"\b(?:CreateSession|FindSessions|JoinSession|IOnlineSession\w*|SteamSockets)\b")
SKIP = ("Plugins/", "Intermediate/", "Binaries/", "Saved/", "DerivedDataCache/")


def ini_value(text: str, section: str, key: str) -> tuple[str, int] | None:
    current = ""
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
        elif current == section and stripped.split("=", 1)[0].strip() == key and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"'), n
    return None


class UnrealScanner:
    name = "unreal"

    def detect(self, root: Path) -> bool:
        return any(root.glob("*.uproject"))

    def scan(self, root: Path) -> ScanResult:
        r = ScanResult(self.name)
        uproject = next(root.glob("*.uproject"))
        try:
            project = json.loads(read_text(uproject) or "{}")
        except json.JSONDecodeError:
            project = {}
            r.warnings.append(ScanWarning("uproject_unreadable", f"{uproject.name} is not valid JSON."))
        r.findings.append(Finding("game.engine.name", "unreal", 1.0, [ev(root, uproject, 1)]))
        version = project.get("EngineAssociation")
        if version and re.match(r"^\d+\.\d+", str(version)):
            r.findings.append(Finding("game.engine.version", str(version), 0.9, [ev(root, uproject, 1)]))
        game_ini = read_text(root / "Config" / "DefaultGame.ini")
        engine_ini_path = root / "Config" / "DefaultEngine.ini"
        engine_ini = read_text(engine_ini_path)
        name = self._names(root, r, uproject, game_ini)
        code = [
            (p, read_text(p)) for p in iter_files(root, (".cpp", ".h"), "Source") if not rel(root, p).startswith(SKIP)
        ]
        self._platforms(root, r, uproject, project)
        self._steam(root, r, project, engine_ini_path, engine_ini, code)
        self._stats_and_achievements(root, r, engine_ini_path, engine_ini, code)
        self._saves(root, r, name, code)
        self._controller(root, r)
        self._localization(root, r)
        evidence = [ev(root, p, line_of(t, m.start())) for p, t in code if (m := NETWORKING.search(t))]
        r.facts["networking"] = bool(evidence)
        if evidence:
            r.findings.append(
                Finding(
                    "game.players.online_coop",
                    True,
                    0.3,
                    evidence[:5],
                    note="Online sessions found; co-op vs PvP has to be confirmed.",
                )
            )
        r.facts["ai_generated_content"] = "never scanned: ask the user"
        return r

    def _names(self, root: Path, r: ScanResult, uproject: Path, game_ini: str) -> str:
        section = "/Script/EngineSettings.GeneralProjectSettings"
        game_path = root / "Config" / "DefaultGame.ini"
        project_name = ini_value(game_ini, section, "ProjectName")
        if project_name:
            r.findings.append(Finding("game.name", project_name[0], 0.7, [ev(root, game_path, project_name[1])]))
        else:
            r.findings.append(Finding("game.name", uproject.stem, 0.4, [ev(root, uproject, 1)], note="file name"))
        if company := ini_value(game_ini, section, "CompanyName"):
            r.findings.append(Finding("store.developers", [company[0]], 0.5, [ev(root, game_path, company[1])]))
        if version := ini_value(game_ini, section, "ProjectVersion"):
            r.facts["project_version"] = version[0]
        r.facts["project_name"] = uproject.stem
        return uproject.stem

    def _platforms(self, root: Path, r: ScanResult, uproject: Path, project: dict[str, object]) -> None:
        targets = project.get("TargetPlatforms")
        found = set()
        if isinstance(targets, list):
            found = {PLATFORMS[str(t).lower()] for t in targets if str(t).lower() in PLATFORMS}
        if found:
            r.findings.append(
                Finding("store.platforms", sorted(found), 0.6, [ev(root, uproject, None, "TargetPlatforms")])
            )

    def _steam(
        self,
        root: Path,
        r: ScanResult,
        project: dict[str, object],
        engine_ini_path: Path,
        engine_ini: str,
        code: list[tuple[Path, str]],
    ) -> None:
        plugins = project.get("Plugins")
        oss = isinstance(plugins, list) and any(
            isinstance(p, dict) and p.get("Name") == "OnlineSubsystemSteam" and p.get("Enabled") for p in plugins
        )
        enabled = ini_value(engine_ini, "OnlineSubsystemSteam", "bEnabled")
        direct = any("steam/steam_api.h" in t or "SteamUserStats()" in t for _, t in code)
        r.facts["steam_sdk"] = "online_subsystem_steam" if oss or enabled else ("steamworks_sdk" if direct else None)
        if not r.facts["steam_sdk"]:
            r.warnings.append(
                ScanWarning(
                    "no_steam_sdk",
                    "The Online Subsystem Steam plugin is not enabled and no Steamworks SDK calls were found. "
                    "Achievements, stats and leaderboards need one of them; Auto-Cloud does not.",
                )
            )
        for key in ("SteamDevAppId", "SteamAppId"):
            if (found := ini_value(engine_ini, "OnlineSubsystemSteam", key)) and found[0].isdigit():
                evidence = ev(root, engine_ini_path, found[1], key)
                if int(found[0]) == SPACEWAR:
                    r.warnings.append(
                        ScanWarning("spacewar_appid", f"{key} is 480 (Valve's Spacewar test app).", [evidence])
                    )
                else:
                    r.findings.append(Finding("apps.main.appid", int(found[0]), 0.7, [evidence]))

    def _stats_and_achievements(
        self, root: Path, r: ScanResult, engine_ini_path: Path, engine_ini: str, code: list[tuple[Path, str]]
    ) -> None:
        achievements: dict[str, list[Evidence]] = defaultdict(list)
        for m in OSS_ACHIEVEMENT.finditer(engine_ini):
            achievements[m.group(1)].append(ev(root, engine_ini_path, line_of(engine_ini, m.start())))
        stats: dict[str, list[Evidence]] = defaultdict(list)
        boards: dict[str, list[Evidence]] = defaultdict(list)
        for p, t in code:
            for pattern in ACHIEVEMENT_CALLS:
                for m in pattern.finditer(t):
                    achievements[m.group(1)].append(ev(root, p, line_of(t, m.start())))
            for m in STAT_CALLS.finditer(t):
                stats[m.group(1)].append(ev(root, p, line_of(t, m.start())))
            for m in LEADERBOARD_CALLS.finditer(t):
                boards[m.group(1)].append(ev(root, p, line_of(t, m.start())))
        for name, evidence in sorted(achievements.items()):
            r.findings.append(Finding(f"achievements.{name}", {"id": name}, 0.9, evidence, kind="item"))
        for name, evidence in sorted(stats.items()):
            if name not in achievements:
                r.findings.append(Finding(f"stats.{name}", {"name": name}, 0.8, evidence, kind="item"))
        for name, evidence in sorted(boards.items()):
            r.findings.append(Finding(f"leaderboards.{name}", {"name": name}, 0.9, evidence, kind="item"))
        r.facts["code_achievements"] = sorted(achievements)
        r.facts["code_stats"] = sorted(s for s in stats if s not in achievements)
        r.facts["code_leaderboards"] = sorted(boards)

    def _saves(self, root: Path, r: ScanResult, name: str, code: list[tuple[Path, str]]) -> None:
        uses = [ev(root, p, line_of(t, m.start())) for p, t in code for m in SAVE_SLOT.finditer(t)]
        r.facts["save_paths"] = [e.model_dump() for e in uses[:20]]
        if not uses:
            return
        r.findings.append(Finding("apps.main.cloud.enabled", True, 0.5, uses[:3]))
        r.findings.append(
            Finding(
                "apps.main.cloud.auto_cloud",
                [
                    {
                        "root": "WinAppDataLocal",
                        "subdirectory": f"{name}/Saved/SaveGames",
                        "pattern": "*.sav",
                        "os": "windows",
                        "recursive": False,
                    }
                ],
                0.5,
                uses[:3],
                note="Packaged Unreal games save SaveGame slots to %LOCALAPPDATA%/<project>/Saved/SaveGames on "
                "Windows. Check the paths of a packaged build on Linux and macOS before adding them.",
            )
        )

    def _controller(self, root: Path, r: ScanResult) -> None:
        path = root / "Config" / "DefaultInput.ini"
        text = read_text(path)
        if m := GAMEPAD.search(text):
            r.findings.append(
                Finding(
                    "game.platform_features.controller",
                    "partial",
                    0.4,
                    [ev(root, path, line_of(text, m.start()), m.group(0))],
                    note="Full support needs every menu playable with a gamepad.",
                )
            )

    def _localization(self, root: Path, r: ScanResult) -> None:
        base = root / "Content" / "Localization" / "Game"
        if not base.is_dir():
            return
        found: dict[str, Evidence] = {}
        for culture in sorted(p for p in base.iterdir() if p.is_dir()):
            lang = steam_language(culture.name.replace("_", "-"))
            if lang:
                found.setdefault(lang, Evidence(file=rel(root, culture), note=culture.name))
        r.facts["unreal_cultures"] = sorted(found)
        targets = sorted(k for k in found if k != "english")
        if targets:
            r.findings.append(Finding("target_languages", targets, 0.6, list(found.values())[:10]))
        for lang, evidence in found.items():
            r.findings.append(
                Finding(
                    f"store.supported_languages.{lang}",
                    {"interface": True, "full_audio": False, "subtitles": False},
                    0.5,
                    [evidence],
                )
            )
