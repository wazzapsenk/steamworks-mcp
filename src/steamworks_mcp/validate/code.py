"""``check_code``: Steamworks rules over a game project's own code, engine settings and build scripts (read-only).

Each rule (texts in ``data/code_rules.yaml``) is a function here. They read the game's own code (third-party plugins,
editor tools and demos excluded, comments stripped) and report findings with file and line. Rules about keys and
login files read every text file, plugins included. Nothing in the project is changed, and a secret that is found is
never repeated in the result.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from steamworks_mcp.data import load_yaml
from steamworks_mcp.scanners.base import MAX_FILE_BYTES, SKIP_DIRS, line_of, read_text
from steamworks_mcp.scanners.unity import (
    ACHIEVEMENT_CALLS,
    NOT_GAME_CODE,
    SPACEWAR,
    STAT_CALLS,
    VENDOR,
    third_party_folders,
)
from steamworks_mcp.status import engine_of

Severity = Literal["error", "warning", "info"]
CODE_SUFFIXES = (".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".gd")
TEXT_SUFFIXES = (
    *CODE_SUFFIXES,
    ".ini",
    ".cfg",
    ".json",
    ".yaml",
    ".yml",
    ".txt",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".vdf",
    ".toml",
    ".xml",
    ".godot",
    ".asset",
    ".py",
    ".js",
    ".ts",
    ".inputactions",
)
BUILD_DIRS = ("Build", "Builds", "build", "builds", "export", "Export", "Packaged")
MAX_PER_RULE = 20


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Group(Model):
    id: str
    title: str
    globs: list[str]
    guidance: str


class Rule(Model):
    id: str
    group: str
    severity: Severity
    title: str
    why: str
    fix: str
    source: str


class RulesFile(Model):
    last_reviewed: str
    groups: list[Group]
    rules: list[Rule]


@cache
def rules_file() -> RulesFile:
    return RulesFile.model_validate(load_yaml("code_rules.yaml"))


def rules() -> dict[str, Rule]:
    return {r.id: r for r in rules_file().rules}


@dataclass
class Hit:
    rule: str
    file: str | None
    line: int | None
    message: str


# ---------------------------------------------------------------------------------------------------- reading


def strip_comments(text: str, hash_comments: bool = False) -> str:
    """Blank out comments (keeping line breaks, so line numbers stay), but not ``//`` inside strings."""
    out = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote or c == "\n":
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if (hash_comments and c == "#") or text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
            continue
        if not hash_comments and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:end]))
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _walk(root: Path, suffixes: tuple[str, ...], skip: set[str]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            if name.endswith(suffixes) or (not suffixes):
                p = Path(dirpath) / name
                try:
                    if p.stat().st_size <= MAX_FILE_BYTES:
                        yield p
                except OSError:
                    continue


@dataclass
class Project:
    root: Path
    values: dict[str, Any]
    engine: str | None = None
    own: dict[str, str] = field(default_factory=dict)
    """The game's own code, comments stripped: relative path -> text."""
    vendor: dict[str, str] = field(default_factory=dict)
    """Third-party code (plugins, SDK wrappers), comments stripped."""
    texts: dict[str, str] = field(default_factory=dict)
    """Every small text file (for keys and passwords), as it is."""

    @classmethod
    def read(cls, root: Path, values: dict[str, Any]) -> Project:
        p = cls(root, values, engine_of(root))
        vendor_folders: set[str] = set()
        if p.engine == "Unity":
            game = values.get("game") or {}
            vendor_folders = {f"Assets/{d}/" for d in third_party_folders(root, game.get("name"), None)}
        for path in _walk(root, TEXT_SUFFIXES, SKIP_DIRS | {"Intermediate", "Binaries", "Saved", "DerivedDataCache"}):
            rel = path.relative_to(root).as_posix()
            text = read_text(path)
            if "\x00" in text[:4096]:
                continue
            p.texts[rel] = text
            if not rel.endswith(CODE_SUFFIXES):
                continue
            code = strip_comments(text, hash_comments=rel.endswith(".gd"))
            third_party = (
                VENDOR.search("/" + rel)
                or NOT_GAME_CODE.search("/" + rel)
                or any(rel.startswith(v) for v in vendor_folders)
                or rel.startswith(("addons/", "Plugins/", "ThirdParty/", "Packages/"))
            )
            (p.vendor if third_party else p.own)[rel] = code
        return p

    def find(
        self, pattern: re.Pattern[str], where: Literal["own", "all", "texts"] = "own"
    ) -> Iterator[tuple[str, int, re.Match[str]]]:
        sources = {"own": self.own, "all": {**self.vendor, **self.own}, "texts": self.texts}[where]
        for rel, text in sources.items():
            for m in pattern.finditer(text):
                yield rel, line_of(text, m.start()), m

    def has(self, pattern: re.Pattern[str], where: Literal["own", "all", "texts"] = "all") -> bool:
        return next(self.find(pattern, where), None) is not None

    def known_appids(self) -> set[int]:
        apps = self.values.get("apps") or {}
        return {int(a["appid"]) for a in apps.values() if isinstance(a, dict) and a.get("appid")}


# ---------------------------------------------------------------------------------------------------- patterns

INIT = re.compile(
    r"\b(?:SteamAPI_Init(?:Ex|Safe|Flat)?|SteamAPI\.Init(?:Ex)?|Steam\.steamInit(?:Ex)?|SteamClient\.Init)\s*\("
)
INIT_STATEMENT = re.compile(
    r"^[ \t]*(?:SteamAPI_Init(?:Ex|Safe|Flat)?|SteamAPI\.Init(?:Ex)?|Steam\.steamInit(?:Ex)?)[ \t]*\([^;\n]*\)"
    r"[ \t]*;?[ \t]*$",
    re.M,
)
FACEPUNCH = re.compile(r"\bSteamClient\.Init\s*\(")
CALLBACKS = re.compile(
    r"\b(?:SteamAPI_RunCallbacks|SteamAPI\.RunCallbacks|Steam\.run_callbacks|SteamGameServer_RunCallbacks|"
    r"SteamAPI_ManualDispatch_RunFrame|GameServer\.RunCallbacks)\s*\("
)
GODOT_EMBEDDED_CALLBACKS = re.compile(r"\bsteamInitEx\s*\([^)]*,[^)]*,\s*true\s*\)|embed_callbacks\s*=\s*true", re.I)
RESTART = re.compile(
    r"\b(?:SteamAPI_RestartAppIfNecessary|SteamAPI\.RestartAppIfNecessary|SteamClient\.RestartAppIfNecessary|"
    r"Steam\.restartAppIfNecessary)\s*\("
)
SHUTDOWN = re.compile(r"\b(?:SteamAPI_Shutdown|SteamAPI\.Shutdown|SteamClient\.Shutdown|Steam\.steamShutdown)\s*\(")
SET_STATS = re.compile(
    r"\b(?:SetAchievement|SetStat(?:Int|Float)?|UpdateAvgRateStat|setAchievement|setStat(?:Int|Float)?)\s*\(", re.I
)
STORE_STATS = re.compile(r"\bstoreStats\s*\(", re.I)
GODOT_ACHIEVEMENT = re.compile(r"\b(?:set|clear|get)Achievement\w*\s*\(\s*\"([A-Za-z0-9_]+)\"")
GODOT_STAT = re.compile(r"\b(?:set|get)Stat(?:Int|Float)?\s*\(\s*\"([A-Za-z0-9_]+)\"")
APPID_IN_CODE = [
    re.compile(r"\bSteamClient\.Init\s*\(\s*(\d{2,8})"),
    re.compile(r"\bnew\s+AppId_t\s*\(\s*(\d{2,8})"),
    re.compile(r"\bRestartAppIfNecessary\s*\(\s*(?:new\s+AppId_t\s*\(\s*|k_uAppIdInvalid\s*\+\s*)?(\d{2,8})", re.I),
    re.compile(r"\bsteamInit(?:Ex)?\s*\(\s*(?:true|false)?\s*,?\s*(\d{2,8})"),
    re.compile(
        r"\b(?:const|static readonly|constexpr|static const)\s+(?:u?int\w*|AppId_t)\s+\w*APP_?ID\w*\s*=\s*(\d{2,8})",
        re.I,
    ),
]
APPID_IN_SETTINGS = re.compile(
    r"^\s*(?:SteamDevAppId|SteamAppId|(?:steam/)?initialization/app_id)\s*=\s*(\d+)", re.M | re.I
)
HEX32 = r"[0-9A-Fa-f]{32}"
WEB_KEY = [
    re.compile(r"(?:steampowered|steam-api)\.com[^\n\"']*[?&]key=(" + HEX32 + r")\b"),
    re.compile(
        r"(?i)\b(?:publisher_?key|webapi_?key|web_api_key|steam_?api_?key|STEAMWORKS_PUBLISHER_KEY)\b[\"']?\s*[:=]\s*"
        r"[\"']?(" + HEX32 + r")\b"
    ),
]
STEAMCMD_LOGIN = re.compile(r"\+login\s+(\S+)\s+(\S+)")
SETLIVE = re.compile(r"\"setlive\"\s+\"default\"", re.I)
FIXED_RES = [
    re.compile(r"\bScreen\.SetResolution\s*\(\s*\d+\s*,\s*\d+"),
    re.compile(r"\bwindow_set_size\s*\(\s*Vector2i?\s*\(\s*\d+\s*,\s*\d+"),
    re.compile(r"\br\.SetRes\s+\d+x\d+", re.I),
]
MOUSE_KEYS = re.compile(r"\bInput\.(?:GetMouseButton\w*|GetKey\w*|mousePosition)\b|\b(?:Mouse|Keyboard)\.current\b")
GAMEPAD = re.compile(
    r"\bGamepad\.current\b|\bInput\.GetJoystick\w*|GetAxis\s*\(\s*\"Joy|\bSteamInput\b|<Gamepad>|\bXInput\b|"
    r"InputEventJoypad|Gamepad_|\bjoy_|is_joy_button_pressed",
)
ANTICHEAT = re.compile(r"\b(?:EasyAntiCheat|EOS_AntiCheat\w*|BattlEye|BEClient)\b")
PLAYERPREFS = re.compile(r"\bPlayerPrefs\.Set(?:Int|Float|String)\s*\(")
WINDOWS_PATH = re.compile(r"\"[A-Za-z]:(?:\\\\|/)[^\"\n]*\"|%(?:APPDATA|LOCALAPPDATA|USERPROFILE)%", re.I)
BINARY_FORMATTER = re.compile(r"\bBinaryFormatter\b")
OLD_P2P = re.compile(r"\b(?:SendP2PPacket|ReadP2PPacket|IsP2PPacketAvailable|AcceptP2PSessionWithUser)\s*\(", re.I)
TICKET = re.compile(r"\b(?:GetAuthSessionTicket|GetAuthTicketForWebApi)\s*\(", re.I)
VERIFY = re.compile(r"\b(?:BeginAuthSession|AuthenticateUserTicket|ValidateAuthTicketResponse)\b", re.I)


# ---------------------------------------------------------------------------------------------------- VDF


def parse_vdf(text: str) -> list[tuple[list[str], str, int]]:
    """Key/value text (SteamPipe scripts) as (key path, value, line) leaves; keys lowercased."""
    out: list[tuple[list[str], str, int]] = []
    stack: list[str] = []
    token = re.compile(r'"((?:[^"\\]|\\.)*)"|(\{)|(\})|(//[^\n]*)|([^\s{}"]+)')
    pending: tuple[str, int] | None = None
    for m in token.finditer(text):
        quoted, open_, close, comment, bare = m.groups()
        if comment is not None:
            continue
        line = line_of(text, m.start())
        if open_:
            stack.append(pending[0].lower() if pending else "")
            pending = None
        elif close:
            if stack:
                stack.pop()
            pending = None
        else:
            word = quoted if quoted is not None else bare
            if pending is None:
                pending = (word, line)
            else:
                out.append(([*stack, pending[0].lower()], word, pending[1]))
                pending = None
    return out


# ---------------------------------------------------------------------------------------------------- rules


def _lifecycle(p: Project) -> tuple[list[tuple[str, int]], bool]:
    """Own-code init call sites, and whether they use Facepunch.Steamworks (which runs callbacks itself)."""
    sites = [(rel, line) for rel, line, _ in p.find(INIT)]
    return sites, p.has(FACEPUNCH, "own")


def appid_rules(p: Project) -> list[Hit]:
    hits: list[Hit] = []
    sources: list[tuple[str, int, int]] = []
    for rel, text in p.texts.items():
        in_build = rel.split("/")[0] in BUILD_DIRS
        if rel.split("/")[-1] == "steam_appid.txt" and text.strip().isdigit() and not in_build:
            sources.append((rel, 1, int(text.strip())))
        if rel.endswith((".ini", ".godot")):
            sources += [(rel, line_of(text, m.start()), int(m.group(1))) for m in APPID_IN_SETTINGS.finditer(text)]
        if rel.endswith(".vdf"):
            leaves = parse_vdf(text)
            if any(path[0] == "appbuild" for path, _, _ in leaves):
                sources += [
                    (rel, line, int(v)) for path, v, line in leaves if path == ["appbuild", "appid"] and v.isdigit()
                ]
    for pattern in APPID_IN_CODE:
        sources += [(rel, line, int(m.group(1))) for rel, line, m in p.find(pattern)]
    known = p.known_appids()
    for rel, line, appid in sources:
        if appid == SPACEWAR:
            hits.append(Hit("appid_spacewar", rel, line, "App id 480 (Spacewar)."))
        elif known and appid not in known:
            hits.append(
                Hit(
                    "appid_mismatch", rel, line, f"App id {appid}; the game's are {', '.join(map(str, sorted(known)))}."
                )
            )
    return hits


def steam_appid_txt_in_build(p: Project) -> list[Hit]:
    hits = []
    seen: set[Path] = set()
    for d in BUILD_DIRS:
        base = p.root / d
        if base.is_dir() and base.resolve() not in seen:  # case-insensitive file systems: Build is build
            seen.add(base.resolve())
            for path in _walk(base, ("steam_appid.txt",), set()):
                hits.append(
                    Hit(
                        "steam_appid_txt_in_build",
                        path.relative_to(p.root).as_posix(),
                        None,
                        "steam_appid.txt in a build folder.",
                    )
                )
    return hits


def lifecycle_rules(p: Project) -> list[Hit]:
    sites, facepunch = _lifecycle(p)
    if not sites:
        return []
    rel, line = sites[0]
    hits = [
        Hit(
            "init_result_unchecked",
            r,
            line_of(t, m.start()),
            "The Steam API start is called without checking its result.",
        )
        for r, t in p.own.items()
        for m in INIT_STATEMENT.finditer(t)
    ]
    godot = any(r.endswith(".gd") for r, _ in sites)
    embedded = p.has(GODOT_EMBEDDED_CALLBACKS, "all") or any(
        GODOT_EMBEDDED_CALLBACKS.search(t) for r, t in p.texts.items() if r.endswith(".godot")
    )
    if not facepunch and not p.has(CALLBACKS) and not (godot and embedded):
        hits.append(Hit("callbacks_not_run", rel, line, "The Steam API starts here, but no code runs its callbacks."))
    if not p.has(RESTART):
        hits.append(
            Hit(
                "no_restart_check", rel, line, "The Steam API starts here without SteamAPI_RestartAppIfNecessary first."
            )
        )
    if not godot and not p.has(SHUTDOWN):
        hits.append(Hit("no_shutdown", rel, line, "The Steam API starts here but is never shut down."))
    return hits


def _names(p: Project, patterns: list[re.Pattern[str]]) -> dict[str, tuple[str, int]]:
    found: dict[str, tuple[str, int]] = {}
    for pattern in patterns:
        for rel, line, m in p.find(pattern):
            found.setdefault(m.group(1), (rel, line))
    return found


def stats_rules(p: Project) -> list[Hit]:
    hits: list[Hit] = []
    sets = list(p.find(SET_STATS))
    if sets and not p.has(STORE_STATS):
        rel, line, _ = sets[0]
        hits.append(
            Hit("stats_not_stored", rel, line, f"{len(sets)} call(s) set achievements or stats; none stores them.")
        )
    defined = {str(a.get("id")): a for a in p.values.get("achievements") or [] if a.get("id")}
    used = _names(p, [*ACHIEVEMENT_CALLS, GODOT_ACHIEVEMENT])
    if defined:
        for name, (rel, line) in used.items():
            if name not in defined:
                hits.append(
                    Hit("achievement_unknown", rel, line, f'"{name}" is not an achievement in steamworks.yaml.')
                )
        if used:
            mentioned = "\n".join(p.own.values())
            for name, a in defined.items():
                if name not in used and f'"{name}"' not in mentioned and not a.get("progress"):
                    hits.append(
                        Hit(
                            "achievement_never_unlocked",
                            "steamworks.yaml",
                            None,
                            f'"{name}" is never unlocked in code.',
                        )
                    )
    stats = {str(s.get("name")) for s in p.values.get("stats") or [] if s.get("name")}
    if stats:
        for name, (rel, line) in _names(p, [STAT_CALLS, GODOT_STAT]).items():
            if name not in stats:
                hits.append(Hit("stat_unknown", rel, line, f'"{name}" is not a stat in steamworks.yaml.'))
    return hits


def secret_rules(p: Project) -> list[Hit]:
    hits: list[Hit] = []
    for rel, text in p.texts.items():
        name = rel.split("/")[-1]
        if name.startswith(".env"):
            continue  # the right place for keys, as long as it stays untracked
        for pattern in WEB_KEY:
            for m in pattern.finditer(text):
                hits.append(
                    Hit(
                        "web_api_key_in_game",
                        rel,
                        line_of(text, m.start()),
                        "A 32-character Web API key (hidden here).",
                    )
                )
        if (
            name.endswith((".bat", ".cmd", ".ps1", ".sh", ".yml", ".yaml", ".txt", ".json", ".toml"))
            or "steamcmd" in text
        ):
            for m in STEAMCMD_LOGIN.finditer(text):
                account, password = m.group(1), m.group(2)
                placeholder = account.startswith(("<", "$", "%", "{")) or password.startswith(
                    ("+", "$", "%", "{", "<", '"$', "'$")
                )
                if not placeholder and "secrets." not in password:
                    hits.append(
                        Hit(
                            "steamcmd_password",
                            rel,
                            line_of(text, m.start()),
                            "steamcmd +login with a password (hidden here).",
                        )
                    )
    for dirpath, dirnames, filenames in os.walk(p.root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "Library", "node_modules")]
        for f in filenames:
            path = Path(dirpath) / f
            if (
                re.fullmatch(r"ssfn\d+", f)
                or f == "loginusers.vdf"
                or (f == "config.vdf" and "ConnectCache" in read_text(path)[:200_000])
            ):
                hits.append(
                    Hit(
                        "steam_credential_files", path.relative_to(p.root).as_posix(), None, f"{f} holds a Steam login."
                    )
                )
    return hits


def build_script_rules(p: Project) -> list[Hit]:
    hits: list[Hit] = []
    for rel, text in p.texts.items():
        if not rel.endswith(".vdf"):
            continue
        for m in SETLIVE.finditer(text):
            hits.append(Hit("setlive_default", rel, line_of(text, m.start()), '"setlive" is "default".'))
        leaves = parse_vdf(text)
        if not leaves or leaves[0][0][0] not in ("appbuild", "depotbuild"):
            continue
        here = (p.root / rel).parent
        values = {tuple(path): (v, line) for path, v, line in leaves}
        kind = leaves[0][0][0]
        root_value = values.get((kind, "contentroot"))
        content = (here / root_value[0].replace("\\", "/")).resolve() if root_value and root_value[0] else here
        if root_value and root_value[0] and not content.is_dir():
            hits.append(
                Hit("build_script_paths", rel, root_value[1], f'Content root "{root_value[0]}" does not exist.')
            )
        if kind == "appbuild":
            for path, v, line in leaves:
                if len(path) == 3 and path[1] == "depots" and v.lower().endswith(".vdf") and not (here / v).is_file():
                    hits.append(Hit("build_script_paths", rel, line, f'Depot script "{v}" does not exist.'))
        for path, v, line in leaves:
            exact = v and not re.search(r"[*?]", v)
            if path[-1] == "localpath" and exact and content.is_dir() and not (content / v.replace("\\", "/")).exists():
                hits.append(
                    Hit("build_script_paths", rel, line, f'Local path "{v}" does not exist in the content root.')
                )
    return hits


def deck_rules(p: Project) -> list[Hit]:
    hits: list[Hit] = []
    for pattern in FIXED_RES:
        for rel, line, _ in p.find(pattern):
            hits.append(Hit("fixed_resolution", rel, line, "A fixed resolution is set here."))
    for rel, text in p.texts.items():
        if rel.endswith(".ini"):
            hits += [
                Hit("fixed_resolution", rel, line_of(text, m.start()), "A fixed resolution is set here.")
                for m in FIXED_RES[2].finditer(text)
            ]
    gamepad = p.has(GAMEPAD, "texts")
    mouse = next(p.find(MOUSE_KEYS), None)
    godot_actions = any("[input]" in t for r, t in p.texts.items() if r.endswith("project.godot"))
    if not gamepad and (mouse or godot_actions):
        where: tuple[str, int | None] = (mouse[0], mouse[1]) if mouse else ("project.godot", None)
        hits.append(Hit("no_gamepad_input", *where, "Mouse or keyboard input, and no gamepad input anywhere."))
    if anti := next(p.find(ANTICHEAT, "texts"), None):
        hits.append(Hit("anticheat_proton", anti[0], anti[1], f"{anti[2].group(0)} is used."))
    return hits


def save_rules(p: Project) -> list[Hit]:
    hits: list[Hit] = []
    seen: set[str] = set()
    for rel, line, _ in p.find(PLAYERPREFS):
        if rel not in seen:
            seen.add(rel)
            hits.append(
                Hit("registry_saves", rel, line, "PlayerPrefs is written here; fine for settings, not for progress.")
            )
    hits += [Hit("windows_paths", rel, line, f"{m.group(0)[:60]}") for rel, line, m in p.find(WINDOWS_PATH)]
    hits += [
        Hit("binary_formatter", rel, line, "BinaryFormatter is used here.") for rel, line, _ in p.find(BINARY_FORMATTER)
    ]
    return hits


def network_rules(p: Project) -> list[Hit]:
    hits = [
        Hit("old_p2p_api", rel, line, f"{m.group(0).rstrip('( ')} is the old API.") for rel, line, m in p.find(OLD_P2P)
    ]
    if (ticket := next(p.find(TICKET), None)) and not p.has(VERIFY, "texts"):
        hits.append(
            Hit(
                "auth_ticket_unverified",
                ticket[0],
                ticket[1],
                "A ticket is created here; nothing in this project verifies one.",
            )
        )
    return hits


CHECKS: tuple[tuple[tuple[str, ...], Callable[[Project], list[Hit]]], ...] = (
    (("appid_spacewar", "appid_mismatch"), appid_rules),
    (("steam_appid_txt_in_build",), steam_appid_txt_in_build),
    (("init_result_unchecked", "callbacks_not_run", "no_restart_check", "no_shutdown"), lifecycle_rules),
    (("stats_not_stored", "achievement_unknown", "stat_unknown", "achievement_never_unlocked"), stats_rules),
    (("web_api_key_in_game", "steamcmd_password", "steam_credential_files"), secret_rules),
    (("setlive_default", "build_script_paths"), build_script_rules),
    (("fixed_resolution", "no_gamepad_input", "anticheat_proton"), deck_rules),
    (("registry_saves", "windows_paths", "binary_formatter"), save_rules),
    (("old_p2p_api", "auth_ticket_unverified"), network_rules),
)
"""Rule ids and the function that checks them (a test keeps these in step with data/code_rules.yaml)."""

ORDER = {"error": 0, "warning": 1, "info": 2}


def check_code(root: Path, values: dict[str, Any], only: list[str] | None = None) -> dict[str, Any]:
    """Run the rules over the game project in ``root``; ``values`` are steamworks.yaml's (for app ids, achievements,
    stats). ``only``: rule ids or group ids."""
    defined = rules()
    if only:
        unknown = [o for o in only if o not in defined and o not in {g.id for g in rules_file().groups}]
        if unknown:
            raise ValueError(f"Unknown rule or group: {', '.join(unknown)}. Rules: {', '.join(defined)}.")
    wanted = {r.id for r in defined.values() if not only or r.id in only or r.group in only}
    project = Project.read(root, values)
    hits: list[Hit] = []
    for ids, fn in CHECKS:
        if wanted & set(ids):
            hits += [h for h in fn(project) if h.rule in wanted]
    per_rule: dict[str, int] = {}
    findings = []
    for h in sorted(hits, key=lambda h: (ORDER[defined[h.rule].severity], h.rule, h.file or "", h.line or 0)):
        per_rule[h.rule] = per_rule.get(h.rule, 0) + 1
        if per_rule[h.rule] > MAX_PER_RULE:
            continue
        r = defined[h.rule]
        findings.append(
            {
                "rule": r.id,
                "severity": r.severity,
                "title": r.title,
                "file": h.file,
                "line": h.line,
                "found": h.message,
                "why": r.why,
                "fix": r.fix,
                "source": r.source,
            }
        )
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
    return {
        "engine": project.engine,
        "files_checked": {
            "own_code": len(project.own),
            "third_party_code": len(project.vendor),
            "all_text": len(project.texts),
        },
        "rules_run": len(wanted),
        "counts": counts,
        "findings": findings,
        "more_than_shown": {k: v - MAX_PER_RULE for k, v in per_rule.items() if v > MAX_PER_RULE},
    }
