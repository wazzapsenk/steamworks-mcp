"""Godot 4 (and 3) projects: project.godot, export_presets.cfg and the game's GDScript (read-only).

Finds the name and engine version, the export platforms, the Steam app id (GodotSteam's project settings), achievements,
stats and leaderboards used with GodotSteam, where saves go (user://) for Steam Auto-Cloud, gamepad input and the
translation locales.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from steamworks_mcp.manifest.state import Evidence
from steamworks_mcp.scanners.base import Finding, ScanResult, ScanWarning, ev, iter_files, line_of, read_text, rel
from steamworks_mcp.scanners.unity import SPACEWAR, steam_language

S = r'"([A-Za-z0-9_]+)"'
ACHIEVEMENT = re.compile(r"\b(?:set|clear|get)Achievement\w*\s*\(\s*" + S)
PROGRESS = re.compile(r"\bindicateAchievementProgress\s*\(\s*" + S + r"\s*,\s*[^,]+,\s*(\d+)")
STAT = re.compile(r"\b(?:set|get)Stat(Int|Float)?\s*\(\s*" + S)
LEADERBOARD = re.compile(r"\bfind(?:OrCreate)?Leaderboard\s*\(\s*" + S + r"([^)\n]*)")
NETWORKING = re.compile(
    r"\b(?:ENetMultiplayerPeer|WebSocketMultiplayerPeer|SteamMultiplayerPeer|MultiplayerAPI|createLobby|joinLobby)\b"
)
PLATFORMS = {
    "windows desktop": "windows",
    "windows": "windows",
    "linux": "linux",
    "linux/x11": "linux",
    "linux/bsd": "linux",
    "macos": "macos",
    "mac osx": "macos",
}
SKIP = ("addons/", ".godot/", ".import/")
USER_PATH = re.compile(r"user://[^\"'\n]*?(?:\.(\w+))?[\"']")
SAVE_EXTENSIONS = {"sav", "save", "json", "dat", "bin", "res", "tres", "cfg", "ini"}


def ini(text: str) -> dict[str, dict[str, tuple[str, int]]]:
    """project.godot / export_presets.cfg: section -> key -> (raw value, line). Multi-line values keep line 1."""
    out: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
    section = ""
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        elif "=" in stripped and not stripped.startswith(";"):
            key, value = stripped.split("=", 1)
            out[section][key.strip()] = (value.strip(), n)
    return out


def unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


class GodotScanner:
    name = "godot"

    def detect(self, root: Path) -> bool:
        return (root / "project.godot").is_file()

    def scan(self, root: Path) -> ScanResult:
        r = ScanResult(self.name)
        project_file = root / "project.godot"
        text = read_text(project_file)
        settings = ini(text)
        app = settings.get("application", {})
        r.findings.append(Finding("game.engine.name", "godot", 1.0, [ev(root, project_file, 1)]))
        features = app.get("config/features")
        if features and (m := re.search(r'"(\d+\.\d+)"', features[0])):
            r.findings.append(Finding("game.engine.version", m.group(1), 0.9, [ev(root, project_file, features[1])]))
        name = unquote(app.get("config/name", ("", 0))[0]) or None
        if name:
            r.findings.append(Finding("game.name", name, 0.7, [ev(root, project_file, app["config/name"][1])]))
        code = [(p, read_text(p)) for p in iter_files(root, (".gd",)) if not rel(root, p).startswith(SKIP)]
        self._platforms(root, r)
        self._steam(root, r, settings, project_file, code)
        self._stats_and_achievements(root, r, code)
        self._saves(root, r, settings, name, code)
        self._controller(root, r, text, project_file)
        self._localization(root, r, settings, project_file)
        self._networking(root, r, code)
        r.facts["ai_generated_content"] = "never scanned: ask the user"
        return r

    def _platforms(self, root: Path, r: ScanResult) -> None:
        presets = root / "export_presets.cfg"
        if not presets.is_file():
            return
        found: dict[str, Evidence] = {}
        for section, keys in ini(read_text(presets)).items():
            if section.startswith("preset.") and "platform" in keys:
                value, line = keys["platform"]
                os_name = PLATFORMS.get(unquote(value).lower())
                if os_name:
                    found.setdefault(os_name, ev(root, presets, line, unquote(value)))
        if found:
            r.findings.append(Finding("store.platforms", sorted(found), 0.6, list(found.values())))

    def _steam(
        self,
        root: Path,
        r: ScanResult,
        settings: dict[str, dict[str, tuple[str, int]]],
        project_file: Path,
        code: list[tuple[Path, str]],
    ) -> None:
        godotsteam = (root / "addons" / "godotsteam").is_dir() or any("Steam." in t for _, t in code)
        r.facts["steam_sdk"] = "godotsteam" if godotsteam else None
        if not godotsteam:
            r.warnings.append(
                ScanWarning(
                    "no_steam_sdk",
                    "No GodotSteam found. Achievements, stats, leaderboards and the Steam Cloud API need it; "
                    "Auto-Cloud does not.",
                )
            )
        steam = settings.get("steam", {})
        appid = steam.get("initialization/app_id")
        candidates = []
        if appid and appid[0].isdigit():
            candidates.append((int(appid[0]), ev(root, project_file, appid[1])))
        for p, t in code:
            for m in re.finditer(r"\bsteamInit(?:Ex)?\s*\(\s*(?:true|false)?\s*,?\s*(\d{2,8})", t):
                candidates.append((int(m.group(1)), ev(root, p, line_of(t, m.start()))))
        for value, evidence in candidates:
            if value == SPACEWAR:
                r.warnings.append(
                    ScanWarning("spacewar_appid", "The app id is 480 (Valve's Spacewar test app).", [evidence])
                )
            else:
                r.findings.append(Finding("apps.main.appid", value, 0.7, [evidence]))

    def _stats_and_achievements(self, root: Path, r: ScanResult, code: list[tuple[Path, str]]) -> None:
        achievements: dict[str, list[Evidence]] = defaultdict(list)
        stats: dict[str, list[Evidence]] = defaultdict(list)
        floats: set[str] = set()
        boards: dict[str, tuple[list[Evidence], str]] = {}
        progress: dict[str, int] = {}
        for p, t in code:
            for m in ACHIEVEMENT.finditer(t):
                achievements[m.group(1)].append(ev(root, p, line_of(t, m.start())))
            for m in PROGRESS.finditer(t):
                progress[m.group(1)] = int(m.group(2))
            for m in STAT.finditer(t):
                stats[m.group(2)].append(ev(root, p, line_of(t, m.start())))
                if m.group(1) == "Float":
                    floats.add(m.group(2))
            for m in LEADERBOARD.finditer(t):
                prev = boards.get(m.group(1), ([], ""))
                boards[m.group(1)] = ([*prev[0], ev(root, p, line_of(t, m.start()))], prev[1] + m.group(2))
        for name, evidence in sorted(achievements.items()):
            r.findings.append(Finding(f"achievements.{name}", {"id": name}, 0.9, evidence, kind="item"))
        for name, evidence in sorted(stats.items()):
            r.findings.append(Finding(f"stats.{name}", {"name": name}, 0.8, evidence, kind="item"))
            if name in floats:
                r.findings.append(Finding(f"stats.{name}.type", "float", 0.6, evidence[:1]))
        for name, (evidence, args) in sorted(boards.items()):
            r.findings.append(Finding(f"leaderboards.{name}", {"name": name}, 0.9, evidence, kind="item"))
            if re.search(r"ASCENDING|,\s*1\b", args):
                r.findings.append(Finding(f"leaderboards.{name}.sort_method", "ascending", 0.7, evidence[:1]))
            elif re.search(r"DESCENDING|,\s*2\b", args):
                r.findings.append(Finding(f"leaderboards.{name}.sort_method", "descending", 0.7, evidence[:1]))
        r.facts["code_achievements"] = sorted(achievements)
        r.facts["achievement_progress_max"] = progress
        r.facts["code_stats"] = sorted(stats)
        r.facts["code_leaderboards"] = sorted(boards)

    def _saves(
        self,
        root: Path,
        r: ScanResult,
        settings: dict[str, dict[str, tuple[str, int]]],
        name: str | None,
        code: list[tuple[Path, str]],
    ) -> None:
        uses: list[Evidence] = []
        extensions: set[str] = set()
        for p, t in code:
            for m in USER_PATH.finditer(t):
                uses.append(ev(root, p, line_of(t, m.start())))
                if m.group(1) and m.group(1).lower() in SAVE_EXTENSIONS:
                    extensions.add(m.group(1).lower())
        r.facts["save_paths"] = [e.model_dump() for e in uses[:20]]
        r.facts["save_extensions"] = sorted(extensions)
        if not uses or not name:
            return
        app = settings.get("application", {})
        custom = app.get("config/use_custom_user_dir", ("false", 0))[0] == "true"
        folder = unquote(app.get("config/custom_user_dir_name", ("", 0))[0]) if custom else ""
        sub = folder or f"Godot/app_userdata/{name}"
        linux = folder or f"godot/app_userdata/{name}"
        pattern = f"*.{next(iter(extensions))}" if len(extensions) == 1 else "*"
        evidence = uses[:3]
        r.findings.append(Finding("apps.main.cloud.enabled", True, 0.5, evidence))
        r.findings.append(
            Finding(
                "apps.main.cloud.auto_cloud",
                [
                    {
                        "root": "WinAppDataRoaming",
                        "subdirectory": sub,
                        "pattern": pattern,
                        "os": "all",
                        "recursive": True,
                    }
                ],
                0.6,
                evidence,
                note="Godot's user:// on Windows is %APPDATA%/" + sub + ".",
            )
        )
        r.findings.append(
            Finding(
                "apps.main.cloud.overrides",
                [
                    {
                        "root": "WinAppDataRoaming",
                        "os": "macos",
                        "use_instead": "MacAppSupport",
                        "add_path": "",
                        "replace_path": False,
                    },
                    {
                        "root": "WinAppDataRoaming",
                        "os": "linux",
                        "use_instead": "LinuxXdgDataHome",
                        "add_path": linux,
                        "replace_path": linux != sub,
                    },
                ],
                0.4,
                evidence,
                note=f"macOS: ~/Library/Application Support/{sub}; Linux: ~/.local/share/{linux}. "
                "Check the real paths of an export on each OS before applying.",
            )
        )

    def _controller(self, root: Path, r: ScanResult, text: str, project_file: Path) -> None:
        """Input actions span several lines in project.godot, so look at the [input] section as text."""
        start = text.find("\n[input]")
        if start < 0:
            return
        end = text.find("\n[", start + 1)
        section = text[start : end if end > 0 else len(text)]
        joypad = [line_of(text, start + m.start()) for m in re.finditer(r"InputEventJoypad\w*", section)]
        if joypad:
            r.findings.append(
                Finding(
                    "game.platform_features.controller",
                    "partial",
                    0.4,
                    [ev(root, project_file, line, "joypad input") for line in joypad[:5]],
                    note="Full support needs every menu playable with a gamepad.",
                )
            )

    def _localization(
        self, root: Path, r: ScanResult, settings: dict[str, dict[str, tuple[str, int]]], project_file: Path
    ) -> None:
        entry = settings.get("internationalization", {}).get("locale/translations")
        if not entry:
            return
        found: dict[str, Evidence] = {}
        for path in re.findall(r'"res://([^"]+)"', entry[0]):
            stem = Path(path).stem
            code = stem.rsplit(".", 1)[-1] if "." in stem else stem
            lang = steam_language(code.replace("_", "-"))
            if lang:
                found.setdefault(lang, ev(root, project_file, entry[1], path))
        r.facts["godot_locales"] = sorted(found)
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

    def _networking(self, root: Path, r: ScanResult, code: list[tuple[Path, str]]) -> None:
        evidence = [ev(root, p, line_of(t, m.start())) for p, t in code if (m := NETWORKING.search(t))]
        r.facts["networking"] = bool(evidence)
        if evidence:
            r.findings.append(
                Finding(
                    "game.players.online_coop",
                    True,
                    0.3,
                    evidence[:5],
                    note="Networking code found; co-op vs PvP has to be confirmed.",
                )
            )
