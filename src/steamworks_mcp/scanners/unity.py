"""Unity project scanner (read-only)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from steamworks_mcp.languages import find_language
from steamworks_mcp.manifest.models import API_NAME
from steamworks_mcp.manifest.state import Evidence
from steamworks_mcp.scanners import unity_yaml
from steamworks_mcp.scanners.base import Finding, ScanResult, ScanWarning, ev, iter_files, line_of, read_text, rel

PLACEHOLDER_NAMES = re.compile(r"^(my project|new unity project|project_.*|.*_unity|untitled.*)$", re.I)
PLACEHOLDER_COMPANY = {"defaultcompany", "default company", ""}
SPACEWAR = 480

# Third-party code (the Steam SDK wrappers themselves define these methods), editor tools and demo scenes.
VENDOR = re.compile(
    r"(?i)(^|/)(plugins|thirdparty|third party|com\.rlabrecque\.steamworks\.net|steamworks\.net|facepunch[^/]*)/"
)
NOT_GAME_CODE = re.compile(r"(?i)/(editor|demos?|samples?|examples?|tests?)/")
VENDOR_HINT = re.compile(
    r"(?i)^(readme.*|license.*|licence.*|third.?party.*|.*\.pdf|documentation|docs?|demos?|samples?|examples?)$"
)
GENERIC_WORDS = {"project", "game", "unity", "new", "my", "the", "prototype", "test"}

S = r'"([A-Za-z0-9_]+)"'
ACHIEVEMENT_CALLS = [
    re.compile(r"\b(?:Set|Clear|Get|Unlock|Award|Grant|Trigger)Achievement\w*\s*\(\s*" + S),
    re.compile(r"\bnew\s+(?:Steamworks\.Data\.)?Achievement\s*\(\s*" + S),
    re.compile(r"\bIndicateAchievementProgress\s*\(\s*" + S),
]
PROGRESS_CALL = re.compile(r"\bIndicateAchievementProgress\s*\(\s*" + S + r"\s*,\s*[^,]+,\s*(\d+)")
STAT_CALLS = re.compile(r"\b(?:Set|Get|Add|Increment)Stat(?:Int|Float)?\s*\(\s*" + S + r"\s*(?:,\s*([^)]+))?\)")
LEADERBOARD_CALLS = re.compile(r"\bFind(?:OrCreate)?Leaderboard\w*\s*\(\s*" + S + r"([^;]*)")
APPID_PATTERNS = [
    re.compile(r"\bSteamClient\.Init\s*\(\s*(\d{3,8})"),
    re.compile(r"\bnew\s+AppId_t\s*\(\s*(\d{3,8})"),
    re.compile(r"\bRestartAppIfNecessary\s*\(\s*(?:new\s+AppId_t\s*\(\s*)?(\d{3,8})"),
    re.compile(r"\b(?:const|static readonly)\s+u?int\s+\w*APP_?ID\w*\s*=\s*(\d{3,8})", re.I),
]
SAVE_FILE = re.compile(r'"([^"\\/]+\.(sav|save|json|dat|bin|bytes|xml|es3))"', re.I)
NETWORKING = {
    "netcode_for_gameobjects": re.compile(r"\busing\s+Unity\.Netcode\b"),
    "fishnet": re.compile(r"\busing\s+FishNet\b"),
    "mirror": re.compile(r"\busing\s+Mirror\b"),
    "photon": re.compile(r"\busing\s+(?:Photon|Fusion)\b"),
    "steam_networking": re.compile(r"\bSteamNetworking(?:Sockets|Messages)?\b"),
    "steam_lobbies": re.compile(r"\bSteamMatchmaking\b|\bLobby\.(?:Join|Create)|CreateLobbyAsync"),
}
ART_HINT = re.compile(r"(?i)(key ?art|keyart|logo|capsule|cover|hero|icon|screenshot|banner|thumbnail)")
UNITY_LOCALE = re.compile(r"m_Identifier:\s*\n\s*m_Code:\s*([A-Za-z\-]+)")
LOCALE_ALIASES = {
    "zh-hans": "schinese",
    "zh-cn": "schinese",
    "zh-hant": "tchinese",
    "zh-tw": "tchinese",
    "ko": "koreana",
    "ko-kr": "koreana",
    "pt-br": "brazilian",
    "es-419": "latam",
    "es-mx": "latam",
}


def steam_language(code: str) -> str | None:
    c = code.strip().lower()
    if c in LOCALE_ALIASES:
        return LOCALE_ALIASES[c]
    lang = find_language(c) or find_language(c.split("-")[0])
    return lang.api if lang else None


def _tokens(*names: str | None) -> set[str]:
    out: set[str] = set()
    for n in names:
        for w in re.split(r"[^A-Za-z0-9]+", n or ""):
            if len(w) >= 4 and w.lower() not in GENERIC_WORDS:
                out.add(w.lower())
    return out


def third_party_folders(root: Path, product: str | None, company: str | None) -> set[str]:
    """Top-level Assets folders that look like Asset Store / vendor packages (their code is not the game's).

    Own folders: names starting with "_", or an asmdef named after the product or company. Vendor folders: a
    readme / license / PDF / documentation / demo folder near the top, an asmdef named after someone else, or a
    well-known shared folder (Plugins, TextMesh Pro, Packages).
    """
    assets = root / "Assets"
    tokens = _tokens(product, company)
    vendor: set[str] = set()
    for d in sorted(p for p in assets.iterdir() if p.is_dir()) if assets.is_dir() else []:
        if d.name.startswith("_"):
            continue
        if d.name.lower() in ("plugins", "textmesh pro", "packages", "third party", "thirdparty"):
            vendor.add(d.name)
            continue
        asmdefs = [
            a.stem.lower() for a in list(d.glob("*.asmdef")) + list(d.glob("*/*.asmdef")) + list(d.glob("*/*/*.asmdef"))
        ]
        if asmdefs and any(t in a for a in asmdefs for t in tokens):
            continue
        near = [c.name for c in d.iterdir()] + [c.name for sub in d.iterdir() if sub.is_dir() for c in sub.iterdir()]
        if asmdefs or any(VENDOR_HINT.match(n) for n in near):
            vendor.add(d.name)
    return vendor


class UnityScanner:
    name = "unity"

    def detect(self, root: Path) -> bool:
        return (root / "ProjectSettings" / "ProjectVersion.txt").is_file() and (root / "Assets").is_dir()

    def scan(self, root: Path) -> ScanResult:
        r = ScanResult(self.name)
        company, product = self._settings(root, r)
        packages = self._packages(root, r)
        vendor_dirs = third_party_folders(root, product, company)
        r.facts["third_party_folders"] = sorted(vendor_dirs)

        def own(p: Path) -> bool:
            path = "/" + rel(root, p)
            top = rel(root, p).split("/")[1] if rel(root, p).count("/") > 1 else ""
            return not VENDOR.search(path) and not NOT_GAME_CODE.search(path) and top not in vendor_dirs

        code = [(p, read_text(p)) for p in iter_files(root, (".cs",), "Assets") if own(p)]
        self._steam(root, r, packages, code)
        self._stats_and_achievements(root, r, code)
        self._saves(root, r, code, company, product)
        self._controller(root, r, packages, code)
        self._localization(root, r, packages)
        self._networking(root, r, packages, code)
        self._art(root, r)
        r.facts["ai_generated_content"] = "never scanned: ask the user"
        return r

    # ------------------------------------------------------------------ project settings

    def _settings(self, root: Path, r: ScanResult) -> tuple[str | None, str | None]:
        version_file = root / "ProjectSettings" / "ProjectVersion.txt"
        m = re.search(r"m_EditorVersion:\s*(\S+)", read_text(version_file))
        r.findings.append(Finding("game.engine.name", "unity", 1.0, [ev(root, version_file, 1)]))
        if m:
            r.findings.append(Finding("game.engine.version", m.group(1), 1.0, [ev(root, version_file, 1)]))
        settings = root / "ProjectSettings" / "ProjectSettings.asset"
        text = read_text(settings)
        player = unity_yaml.first(settings, "PlayerSettings")

        def line(key: str) -> int | None:
            i = text.find(f"\n  {key}:")
            return line_of(text, i + 1) if i >= 0 else None

        product = str(player.get("productName") or "").strip() or None
        company = str(player.get("companyName") or "").strip() or None
        if product:
            placeholder = bool(PLACEHOLDER_NAMES.match(product))
            r.findings.append(
                Finding(
                    "game.name",
                    product,
                    0.3 if placeholder else 0.7,
                    [ev(root, settings, line("productName"), "productName")],
                    note="looks like a working title" if placeholder else "",
                )
            )
        if company and company.lower() not in PLACEHOLDER_COMPANY:
            r.findings.append(
                Finding("store.developers", [company], 0.5, [ev(root, settings, line("companyName"), "companyName")])
            )
        if v := player.get("bundleVersion"):
            r.facts["bundle_version"] = str(v)
        r.facts["company_name"], r.facts["product_name"] = company, product
        return company, product

    def _packages(self, root: Path, r: ScanResult) -> set[str]:
        manifest = root / "Packages" / "manifest.json"
        try:
            deps = json.loads(read_text(manifest) or "{}").get("dependencies", {})
        except json.JSONDecodeError:
            r.warnings.append(ScanWarning("packages_unreadable", "Packages/manifest.json is not valid JSON."))
            deps = {}
        names = set(deps)
        r.facts["packages"] = sorted(n for n in names if not n.startswith("com.unity.modules."))
        return names

    # ------------------------------------------------------------------ Steam integration

    def _steam(self, root: Path, r: ScanResult, packages: set[str], code: list[tuple[Path, str]]) -> None:
        sdk = None
        if any("steamworks.net" in p for p in packages) or (root / "Assets" / "com.rlabrecque.steamworks.net").exists():
            sdk = "steamworks.net"
        elif list(root.glob("Assets/**/Facepunch.Steamworks*.dll")):
            sdk = "facepunch"
        elif any("using Steamworks" in t for _, t in code):
            sdk = "steamworks (namespace used)"
        r.facts["steam_sdk"] = sdk
        if not sdk:
            r.warnings.append(
                ScanWarning(
                    "no_steam_sdk",
                    "No Steamworks SDK wrapper (Steamworks.NET or Facepunch.Steamworks) found. Achievements, stats, "
                    "leaderboards and the Steam Cloud API need one; Auto-Cloud does not.",
                )
            )
        appid_file = root / "steam_appid.txt"
        if appid_file.is_file():
            value = read_text(appid_file).strip()
            if value.isdigit():
                if int(value) == SPACEWAR:
                    r.warnings.append(
                        ScanWarning(
                            "spacewar_appid",
                            "steam_appid.txt holds 480 (Valve's Spacewar test app).",
                            [ev(root, appid_file, 1)],
                        )
                    )
                else:
                    r.findings.append(Finding("apps.main.appid", int(value), 0.8, [ev(root, appid_file, 1)]))
        for p, text in code:
            for pat in APPID_PATTERNS:
                for m in pat.finditer(text):
                    appid = int(m.group(1))
                    if appid != SPACEWAR:
                        r.findings.append(
                            Finding("apps.main.appid", appid, 0.6, [ev(root, p, line_of(text, m.start()))])
                        )

    def _stats_and_achievements(self, root: Path, r: ScanResult, code: list[tuple[Path, str]]) -> None:
        achievements: dict[str, list[Evidence]] = defaultdict(list)
        progress: dict[str, int] = {}
        stats: dict[str, list[Evidence]] = defaultdict(list)
        float_stats: set[str] = set()
        boards: dict[str, tuple[list[Evidence], str]] = {}
        for p, text in code:
            for pat in ACHIEVEMENT_CALLS:
                for m in pat.finditer(text):
                    if API_NAME.match(m.group(1)):
                        achievements[m.group(1)].append(ev(root, p, line_of(text, m.start())))
            for m in PROGRESS_CALL.finditer(text):
                progress[m.group(1)] = int(m.group(2))
            for m in STAT_CALLS.finditer(text) if "Steam" in text or "UserStats" in text else ():
                stats[m.group(1)].append(ev(root, p, line_of(text, m.start())))
                if m.group(2) and re.search(r"\d\.\d|\bf\b|\d+f\b|float", m.group(2)):
                    float_stats.add(m.group(1))
            for m in LEADERBOARD_CALLS.finditer(text):
                prev = boards.get(m.group(1), ([], ""))
                boards[m.group(1)] = ([*prev[0], ev(root, p, line_of(text, m.start()))], prev[1] + m.group(2))
        stats_only = {k: v for k, v in stats.items() if k not in achievements}
        for name, evidence in sorted(achievements.items()):
            r.findings.append(Finding(f"achievements.{name}", {"id": name}, 0.9, evidence, kind="item"))
        for name, evidence in sorted(stats_only.items()):
            r.findings.append(Finding(f"stats.{name}", {"name": name}, 0.8, evidence, kind="item"))
            if name in float_stats:
                r.findings.append(Finding(f"stats.{name}.type", "float", 0.5, evidence[:1]))
        for name, (evidence, args) in sorted(boards.items()):
            r.findings.append(Finding(f"leaderboards.{name}", {"name": name}, 0.9, evidence, kind="item"))
            if re.search(r"Ascending", args):
                r.findings.append(Finding(f"leaderboards.{name}.sort_method", "ascending", 0.8, evidence[:1]))
            elif re.search(r"Descending", args):
                r.findings.append(Finding(f"leaderboards.{name}.sort_method", "descending", 0.8, evidence[:1]))
            if re.search(r"TimeMilliSeconds|MilliSeconds", args):
                r.findings.append(Finding(f"leaderboards.{name}.display_type", "milliseconds", 0.8, evidence[:1]))
            elif re.search(r"TimeSeconds|\bSeconds\b", args):
                r.findings.append(Finding(f"leaderboards.{name}.display_type", "seconds", 0.8, evidence[:1]))
        r.facts["code_achievements"] = sorted(achievements)
        r.facts["achievement_progress_max"] = progress
        r.facts["code_stats"] = sorted(stats_only)
        r.facts["code_leaderboards"] = sorted(boards)

    # ------------------------------------------------------------------ saves & Steam Cloud

    def _saves(
        self, root: Path, r: ScanResult, code: list[tuple[Path, str]], company: str | None, product: str | None
    ) -> None:
        persistent: list[Evidence] = []
        prefs: list[Evidence] = []
        remote: list[Evidence] = []
        extensions: set[str] = set()
        for p, text in code:
            for m in re.finditer(r"Application\.persistentDataPath", text):
                persistent.append(ev(root, p, line_of(text, m.start())))
                window = text[max(0, m.start() - 400) : m.start() + 400]
                extensions |= {e.lower() for _, e in SAVE_FILE.findall(window)}
            for m in re.finditer(r"\bPlayerPrefs\.Set\w+", text):
                prefs.append(ev(root, p, line_of(text, m.start())))
            for m in re.finditer(r"\bSteamRemoteStorage\.\w+", text):
                remote.append(ev(root, p, line_of(text, m.start())))
        r.facts["save_paths"] = [e.model_dump() for e in persistent[:20]]
        r.facts["save_extensions"] = sorted(extensions)
        r.facts["cloud_api"] = bool(remote)
        if prefs:
            r.warnings.append(
                ScanWarning(
                    "playerprefs_not_synced",
                    "PlayerPrefs is used. On Windows it lives in the registry, which Steam Auto-Cloud cannot sync: "
                    "move progress you want in the cloud into files under Application.persistentDataPath.",
                    prefs[:10],
                )
            )
        if not persistent or not company or not product:
            return
        sub = f"{company}/{product}"
        pattern = f"*.{next(iter(extensions))}" if len(extensions) == 1 else "*"
        evidence = persistent[:3]
        r.findings.append(Finding("apps.main.cloud.enabled", True, 0.5, evidence))
        r.findings.append(
            Finding(
                "apps.main.cloud.auto_cloud",
                [
                    {
                        "root": "WinAppDataLocalLow",
                        "subdirectory": sub,
                        "pattern": pattern,
                        "os": "all",
                        "recursive": True,
                    }
                ],
                0.6,
                evidence,
                note="Unity's persistentDataPath on Windows is AppData/LocalLow/<company>/<product>.",
            )
        )
        r.findings.append(
            Finding(
                "apps.main.cloud.overrides",
                [
                    {
                        "root": "WinAppDataLocalLow",
                        "os": "macos",
                        "use_instead": "MacAppSupport",
                        "add_path": "",
                        "replace_path": False,
                    },
                    {
                        "root": "WinAppDataLocalLow",
                        "os": "linux",
                        "use_instead": "LinuxXdgConfigHome",
                        "add_path": "unity3d",
                        "replace_path": False,
                    },
                ],
                0.4,
                evidence,
                note="macOS: ~/Library/Application Support/<company>/<product>; "
                "Linux: ~/.config/unity3d/<company>/<product>. "
                "Check the real paths of a build on each OS before applying.",
            )
        )

    # ------------------------------------------------------------------ controller, localization, networking, art

    def _controller(self, root: Path, r: ScanResult, packages: set[str], code: list[tuple[Path, str]]) -> None:
        gamepad: list[Evidence] = []
        for p in iter_files(root, (".inputactions",), "Assets"):
            text = read_text(p)
            if i := text.find("<Gamepad>") + 1:
                gamepad.append(ev(root, p, line_of(text, i)))
        steam_input = [
            ev(root, p, line_of(t, m.start())) for p, t in code for m in re.finditer(r"\bSteamInput\.\w+", t)
        ]
        r.facts["input_system"] = "com.unity.inputsystem" in packages
        if gamepad:
            r.findings.append(
                Finding(
                    "game.platform_features.controller",
                    "partial",
                    0.4,
                    gamepad[:5],
                    note="Full support needs every menu playable with a gamepad.",
                )
            )
        if steam_input:
            r.findings.append(Finding("game.platform_features.steam_input", True, 0.8, steam_input[:3]))

    def _localization(self, root: Path, r: ScanResult, packages: set[str]) -> None:
        if "com.unity.localization" not in packages:
            return
        found: dict[str, Evidence] = {}
        for p in iter_files(root, (".asset",), "Assets"):
            text = read_text(p)
            if "m_Identifier:" not in text or "LocaleName" not in text:
                continue
            for m in UNITY_LOCALE.finditer(text):
                lang = steam_language(m.group(1))
                if lang:
                    found.setdefault(lang, ev(root, p, line_of(text, m.start()), f"Locale {m.group(1)}"))
        r.facts["unity_locales"] = sorted(found)
        if not found:
            return
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

    def _networking(self, root: Path, r: ScanResult, packages: set[str], code: list[tuple[Path, str]]) -> None:
        used: dict[str, list[Evidence]] = defaultdict(list)
        for name in ("com.unity.netcode.gameobjects", "com.unity.multiplayer.playmode"):
            if name in packages:
                used["netcode_for_gameobjects" if "netcode" in name else "multiplayer_playmode"].append(
                    Evidence(file="Packages/manifest.json", note=name)
                )
        for p, text in code:
            for key, pat in NETWORKING.items():
                if (m := pat.search(text)) and len(used[key]) < 5:
                    used[key].append(ev(root, p, line_of(text, m.start())))
        r.facts["networking"] = sorted(k for k, v in used.items() if v)
        if used:
            evidence = [e for v in used.values() for e in v][:5]
            r.findings.append(
                Finding(
                    "game.players.online_coop",
                    True,
                    0.3,
                    evidence,
                    note="Networking code found; co-op vs PvP has to be confirmed.",
                )
            )

    def _art(self, root: Path, r: ScanResult) -> None:
        candidates = []
        for p in iter_files(root, (".png", ".jpg", ".jpeg", ".psd"), "Assets"):
            if ART_HINT.search(p.stem) and not VENDOR.search(rel(root, p)):
                candidates.append(rel(root, p))
        for folder in ("Marketing", "Art", "Store", "Press"):
            for p in iter_files(root, (".png", ".jpg", ".jpeg", ".psd"), folder):
                candidates.append(rel(root, p))
        r.facts["art_candidates"] = sorted(candidates)[:200]
