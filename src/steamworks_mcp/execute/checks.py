"""check_store and check_build: what Steamworks really has, field by field, checked for sense.

Steamworks' own checklist ticks an item as soon as something is there (system requirements with only the OS filled
pass). These checks look at the values instead: whether each is filled the way a store page or a build review needs,
and whether they agree with each other, with steamworks.yaml and with the game's build folder. Every issue says how
bad it is (``missing``: Steam or players will miss it; ``inconsistent``: two places disagree; ``check``: looks odd,
confirm it) and where to fix it. Nothing is written anywhere.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

from steamworks_mcp.execute import store_assets, sync
from steamworks_mcp.execute.store_page import CATEGORIES
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.scanners import redist

STORE_PAGE = "Edit Store Page"
INSTALLATION = "Edit Steamworks Settings > Installation"
SHORT = "app[content][short_description]"
ABOUT = "app[content][about]"
PLATFORMS = {"win": "windows", "mac": "macos", "linux": "linux"}
SYSREQ_OS = {"windows": "windows", "macos": "mac", "linux": "linux"}
SYSREQ_FIELDS = {"osversion": "OS", "processor": "processor", "memory": "memory", "graphics": "graphics"}
SYSREQ_FIELDS["diskspace"] = "storage"
MULTIPLAYER = {9, 24, 27, 36, 37, 38, 39, 47, 48, 49}
"""Categories that only make sense with Multi-player (1) ticked too."""
IMPLIES = {38: 9, 39: 9, 48: 9, 36: 49, 37: 49, 47: 49}
"""Online/local/LAN co-op -> Co-op; online/local/LAN PvP -> PvP."""
REQUIRED_SLOTS = (
    "header_capsule",
    "small_capsule",
    "main_capsule",
    "vertical_capsule",
    "library_capsule",
    "library_header",
    "library_hero",
    "library_logo",
)
EXTENSIONS = {"windows": (".exe", ".bat", ".cmd"), "macos": (".app", ".sh", ""), "linux": (".sh", ".x86_64", "")}
PE_MACHINE = {0x8664: "64", 0xAA64: "64", 0x14C: "32"}


def issue(severity: str, area: str, what: str, fix: str, where: str = STORE_PAGE) -> dict[str, str]:
    return {"severity": severity, "area": area, "what": what, "fix": fix, "where": where}


def _on(v: Any) -> bool:
    return str(v).lower() in ("1", "true")


def _text(v: Any) -> str:
    """A localized value ({"english": "..."}) or a plain one."""
    if isinstance(v, dict):
        return str(v.get("english") or next((x for x in v.values() if x), "") or "")
    return str(v or "")


def _mb(size: Any) -> float | None:
    if not isinstance(size, dict) or not str(size.get("amount") or "").replace(".", "", 1).isdigit():
        return None
    return float(size["amount"]) * {"MB": 1, "GB": 1024, "TB": 1024 * 1024}.get(str(size.get("units")).upper(), 1)


# ---------------------------------------------------------------------------------------------------- store


def store_issues(
    item: dict[str, Any],
    loc: dict[str, Any],
    tags: list[int] | None,
    achievements: int | None,
    cloud: dict[str, Any] | None,
    depots: dict[str, Any] | None,
    values: dict[str, Any],
) -> list[dict[str, str]]:
    """``item`` is the store item (serialized_app_data), ``loc`` the store localization export."""
    out: list[dict[str, str]] = []
    content, game = item.get("content") or {}, item.get("game") or {}
    texts = {k: v for k, v in (loc.get("languages") or {}).items() if isinstance(v, dict)}
    source = str(values.get("source_language") or "english")
    own = texts.get(source) or texts.get("english") or {}
    short, about = str(own.get(SHORT) or "").strip(), str(own.get(ABOUT) or "").strip()

    # texts
    if not short:
        out.append(
            issue("missing", "short description", "There is no short description.", "Write one (≤300 characters).")
        )
    elif len(short) > 300:
        out.append(
            issue("inconsistent", "short description", f"{len(short)} characters; Steam allows 300.", "Shorten it.")
        )
    if not about:
        out.append(issue("missing", "about this game", "'About This Game' is empty.", "Write the long description."))
    elif len(re.sub(r"\[[^\]]*\]", "", about)) < 300:
        out.append(
            issue("check", "about this game", "'About This Game' is very short.", "Most pages use 2-4 paragraphs.")
        )
    want = str(fp.get(values, "store.short_description") or "").strip()
    if want and short and sync.normalize_store_text(want) != sync.normalize_store_text(short):
        out.append(
            issue(
                "inconsistent",
                "short description",
                "steamworks.yaml has a different short description than Steamworks.",
                "apply(section='store_text') to send yours, or import_from_steamworks to take Steam's.",
            )
        )

    # languages
    langs = content.get("supported_languages") or {}
    supported = sorted(k for k, v in langs.items() if isinstance(v, dict) and _on(v.get("supported")))
    if not supported:
        out.append(
            issue("missing", "languages", "No language is marked as supported.", "Tick the interface languages.")
        )
    for lang, row in langs.items():
        if (
            isinstance(row, dict)
            and not _on(row.get("supported"))
            and (_on(row.get("full_audio")) or _on(row.get("subtitles")))
        ):
            out.append(
                issue(
                    "inconsistent",
                    "languages",
                    f"{lang}: audio or subtitles ticked without the interface.",
                    "Tick Interface for it too, or untick the others.",
                )
            )
    untranslated = [lang for lang in supported if lang != source and not (texts.get(lang) or {}).get(SHORT)]
    if untranslated:
        out.append(
            issue(
                "check",
                "languages",
                f"Supported but the store page is not translated: {', '.join(untranslated)} (players see {source}).",
                "Translate the store texts (localization tools) and apply(section='store_text').",
            )
        )

    # platforms and system requirements
    platforms = [PLATFORMS[k] for k, v in (item.get("platforms") or {}).items() if k in PLATFORMS and _on(v)]
    if not platforms:
        out.append(issue("missing", "platforms", "No platform is ticked.", "Tick Windows, macOS and/or Linux."))
    sysreqs = content.get("sysreqs") or {}
    for os_name in platforms:
        reqs = sysreqs.get(SYSREQ_OS[os_name]) or {}
        low, rec = reqs.get("min") or {}, reqs.get("rec") or {}
        empty = [label for key, label in SYSREQ_FIELDS.items() if not (_mb(low.get(key)) or _text(low.get(key)))]
        if empty:
            out.append(
                issue(
                    "missing",
                    "system requirements",
                    f"{os_name} minimum requirements lack: {', '.join(empty)}.",
                    "Fill them under Basic Info > System Requirements (generate(section='requirements') drafts them).",
                )
            )
        for key, label in (("memory", "memory"), ("diskspace", "storage")):
            lo, hi = _mb(low.get(key)), _mb(rec.get(key)) if isinstance(rec, dict) else None
            if lo and hi and hi < lo:
                out.append(
                    issue(
                        "inconsistent",
                        "system requirements",
                        f"{os_name}: recommended {label} is below the minimum.",
                        "Swap or fix them.",
                    )
                )
    depot_os = {str((d.get("config") or {}).get("oslist") or "") for d in (depots or {}).values()}
    if depots is not None and "" not in depot_os:
        for os_name in platforms:
            if not any(os_name in o.split(",") for o in depot_os):
                out.append(
                    issue(
                        "inconsistent",
                        "platforms",
                        f"{os_name} is ticked on the store page but no depot is for {os_name}.",
                        "Add a depot for it or untick the platform.",
                        "Edit Steamworks Settings > SteamPipe > Depots",
                    )
                )

    # genre, categories
    classification = item.get("classification") or {}
    genre, genres = str(classification.get("primary_genre") or ""), [str(g) for g in classification.get("genres") or []]
    if not genre or genre == "0":
        out.append(issue("missing", "genre", "No primary genre.", "Pick one under Basic Info > Genre."))
    elif genres and genre not in genres:
        out.append(
            issue(
                "inconsistent", "genre", "The primary genre is not among the ticked genres.", "Tick it as a genre too."
            )
        )
    cats = {
        int(k.split("_")[1])
        for k, v in (classification.get("category") or {}).items()
        if _on(v) and k.split("_")[-1].isdigit()
    }
    name = {i: CATEGORIES.get(i, f"category {i}") for i in cats | set(IMPLIES.values()) | {1, 2, 22, 23}}
    if not cats & {1, 2}:
        out.append(
            issue("missing", "categories", "Neither Single-player nor Multi-player is ticked.", "Tick at least one.")
        )
    if cats & MULTIPLAYER and 1 not in cats:
        shown = ", ".join(name[c] for c in sorted(cats & MULTIPLAYER))
        out.append(
            issue("inconsistent", "categories", f"{shown} ticked without Multi-player.", "Tick Multi-player too.")
        )
    for child, parent in IMPLIES.items():
        if child in cats and parent not in cats:
            out.append(
                issue(
                    "inconsistent",
                    "categories",
                    f"{name[child]} ticked without {name[parent]}.",
                    f"Tick {name[parent]} too.",
                )
            )
    if achievements is not None and (22 in cats) != (achievements > 0):
        what = (
            "Steam Achievements is ticked but the app has no achievements."
            if 22 in cats
            else (f"The app has {achievements} achievements but the Steam Achievements category is not ticked.")
        )
        out.append(issue("inconsistent", "categories", what, "Make the category match."))
    if cloud is not None:
        uses_cloud = bool(cloud.get("byte_quota"))  # paths are optional: the Cloud API needs none
        if (23 in cats) != uses_cloud:
            what = (
                "Steam Cloud is ticked but its quota is 0."
                if 23 in cats
                else ("Steam Cloud is set up but its category is not ticked.")
            )
            out.append(issue("inconsistent", "categories", what, "Make the category match the Steam Cloud page."))

    # people, support, release
    if not [d for d in game.get("developers") or [] if isinstance(d, dict) and d.get("name")]:
        out.append(issue("missing", "developer", "No developer name.", "Add it under Basic Info."))
    if not [p for p in game.get("publishers") or [] if p]:
        out.append(
            issue("missing", "publisher", "No publisher name.", "Add it under Basic Info (yourself if self-published).")
        )
    support = content.get("support_info")
    if not (isinstance(support, dict) and any(_text(v) for v in support.values())):
        out.append(issue("missing", "support", "No support email or URL.", "Add one under Basic Info > Support Info."))
    release = item.get("release") or {}
    if not release.get("steam_release_date") and not release.get("coming_soon_display"):
        out.append(
            issue("missing", "release date", "No release date or 'coming soon' text.", "Set it under Basic Info.")
        )

    # images and tags
    assets = item.get("assets") or {}
    empty_slots = [s for s in REQUIRED_SLOTS if not store_assets.present(assets, s)]
    if empty_slots:
        out.append(
            issue(
                "missing",
                "images",
                f"Empty image slots: {', '.join(empty_slots)}.",
                "prepare_images, then apply(section='store_assets').",
            )
        )
    if store_assets.present(assets, "library_logo") and not (assets.get("library_logo") or {}).get("logo_position"):
        out.append(
            issue(
                "missing", "images", "The library logo has no position.", "Set it with the Library Logo placement tool."
            )
        )
    shots = [s for s in assets.get("screenshots") or [] if isinstance(s, dict) and any(k != "all_ages" for k in s)]
    if len(shots) < 5:
        out.append(
            issue(
                "missing",
                "screenshots",
                f"{len(shots)} screenshot(s); Steam wants at least 5.",
                "Upload more on Graphical Assets.",
            )
        )
    elif sum(_on(s.get("all_ages")) for s in shots) < 4:
        out.append(
            issue(
                "check",
                "screenshots",
                "Fewer than 4 screenshots are marked 'suitable for all ages'.",
                "If they are, mark at least 4, or the game is hidden from some store areas.",
            )
        )
    if tags is not None and len(tags) < 5:
        out.append(
            issue(
                "missing" if not tags else "check",
                "tags",
                f"{len(tags)} developer tag(s).",
                "Pick up to 20 in the Tag Wizard (apply(section='store_tags') does it).",
                "Store Admin > Tag Wizard",
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------- build


def pe_arch(data: bytes) -> str | None:
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        return PE_MACHINE.get(struct.unpack_from("<H", data, pe + 4)[0]) if data[:2] == b"MZ" else None
    except struct.error:
        return None


def build_folders(values: dict[str, Any], root: Path) -> dict[str, Path]:
    """OS -> the local build folder steamworks.yaml names for it (Builds/<OS> when no depot names one)."""
    out: dict[str, Path] = {}
    for d in fp.get(values, "apps.main.builds.depots") or []:
        if d.get("content_root"):
            for os_name in (d.get("os") or "windows",) if d.get("os") != "all" else ("windows", "macos", "linux"):
                out.setdefault(os_name, root / d["content_root"])
    out.setdefault("windows", root / "Builds" / "Windows")
    return out


def build_issues(
    platforms: list[str],
    depots: dict[str, Any],
    installation: dict[str, Any],
    redists: dict[str, Any],
    cloud: dict[str, Any],
    achievements: list[dict[str, Any]],
    builds: dict[str, Any] | None,
    pending: list[str],
    values: dict[str, Any],
    root: Path,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    depots_page = "Edit Steamworks Settings > SteamPipe > Depots"
    folders = build_folders(values, root)

    # depots and builds
    if not depots:
        out.append(issue("missing", "depots", "The app has no depot.", "Add one on the Depots page.", depots_page))
    depot_os = [str((d.get("config") or {}).get("oslist") or "") for d in depots.values()]
    for os_name in platforms:
        if depots and not any(o == "" or os_name in o.split(",") for o in depot_os):
            out.append(
                issue(
                    "missing",
                    "depots",
                    f"No depot is for {os_name}, which the store page lists.",
                    "Add or set one.",
                    depots_page,
                )
            )
    for depot, d in depots.items():
        for o in str((d.get("config") or {}).get("oslist") or "").split(","):
            if o and platforms and o not in platforms:
                out.append(
                    issue(
                        "inconsistent",
                        "depots",
                        f"Depot {depot} is for {o}, which the store page does not list.",
                        "Tick the platform or fix the depot.",
                        depots_page,
                    )
                )
    if builds is not None:
        if builds.get("error"):
            out.append(
                issue(
                    "check",
                    "builds",
                    f"Builds could not be read: {builds['error']}",
                    "Check the publisher key.",
                    "SteamPipe > Builds",
                )
            )
        elif not builds.get("builds"):
            out.append(
                issue(
                    "missing",
                    "builds",
                    "No build was uploaded yet.",
                    "apply(section='build') uploads one.",
                    "SteamPipe > Builds",
                )
            )

    # launch options
    options = installation.get("launch_options") or []
    if not options:
        out.append(
            issue("missing", "launch options", "No launch option.", "Add one (the game's executable).", INSTALLATION)
        )
    for o in options:
        exe, os_name, n = str(o.get("executable") or ""), str(o.get("os") or ""), o.get("index")
        if not exe:
            out.append(
                issue(
                    "missing",
                    "launch options",
                    f"Launch option #{n} has no executable.",
                    "Fill or delete it.",
                    INSTALLATION,
                )
            )
            continue
        if re.match(r"^(\.{1,2})?[\\/]", exe):
            out.append(
                issue(
                    "inconsistent",
                    "launch options",
                    f"#{n} '{exe}' starts with a slash or dot.",
                    "Use the path inside the depot, e.g. MyGame.exe.",
                    INSTALLATION,
                )
            )
        targets = [os_name] if os_name else platforms or ["windows"]
        for target in targets:
            ext = Path(exe).suffix.lower()
            if target in EXTENSIONS and ext not in EXTENSIONS[target]:
                out.append(
                    issue(
                        "check",
                        "launch options",
                        f"#{n} '{exe}' does not look like a {target} program.",
                        "Set the option's OS or fix the path.",
                        INSTALLATION,
                    )
                )
            folder = folders.get(target)
            if folder is None or not folder.is_dir():
                continue
            local = folder / exe.replace("\\", "/")
            if not local.exists():
                out.append(
                    issue(
                        "inconsistent",
                        "launch options",
                        f"#{n} '{exe}' is not in the {target} build ({folder.name}).",
                        "Fix the path or the build.",
                        INSTALLATION,
                    )
                )
            elif target == "windows" and local.is_file() and o.get("arch") in ("32", "64"):
                arch = pe_arch(local.read_bytes()[:4096])
                if arch and arch != o["arch"]:
                    out.append(
                        issue(
                            "inconsistent",
                            "launch options",
                            f"#{n} is set to {o['arch']}-bit but '{exe}' is {arch}-bit.",
                            "Fix the option's architecture.",
                            INSTALLATION,
                        )
                    )
    if not str(installation.get("install_folder") or "").strip():
        out.append(
            issue("missing", "install folder", "No install folder.", "Set it under General Installation.", INSTALLATION)
        )

    # redistributables
    where = INSTALLATION + " > Redistributables"
    if not redists.get("selected") and not redists.get("none_needed"):
        out.append(
            issue(
                "missing",
                "redistributables",
                "Neither redistributables nor 'none needed' is chosen.",
                "apply(section='redistributables').",
                where,
            )
        )
    win = folders.get("windows")
    if "windows" in (platforms or ["windows"]) and win and win.is_dir():
        needed = redist.needs(redist.binaries([win]), root)
        lacking = sorted(set(needed) - set(redists.get("selected") or []))
        if lacking:
            first = needed[lacking[0]][0]
            out.append(
                issue(
                    "inconsistent",
                    "redistributables",
                    f"The build needs {', '.join(lacking)} ({first.file} {first.note}); Steam does not install it.",
                    "Tick it (scan_project, then apply(section='redistributables')).",
                    where,
                )
            )
        steam_api = [p for p in redist.binaries([win]) if p.name.lower() in ("steam_api64.dll", "steam_api.dll")]
        if (achievements or fp.get(values, "leaderboards")) and not steam_api:
            out.append(
                issue(
                    "check",
                    "build",
                    f"No steam_api DLL in {win.name}, though the game uses Steam features.",
                    "Check the SDK is in the build.",
                    "your build folder",
                )
            )
        if any(p.name.lower() == "steam_appid.txt" for p in win.rglob("steam_appid.txt")):
            out.append(
                issue(
                    "check",
                    "build",
                    "steam_appid.txt is in the build folder.",
                    "Leave it out of shipped builds (check_code explains).",
                    "your build folder",
                )
            )

    # cloud and achievements
    if cloud.get("byte_quota") and not cloud.get("roots"):
        out.append(
            issue(
                "check",
                "steam cloud",
                "Steam Cloud has a quota but no Auto-Cloud path.",
                "Add paths, or confirm the game uses the Steam Cloud API itself.",
                "Steam Cloud page",
            )
        )
    for a in achievements:
        name = _text(a.get("display_name")) or a.get("api_name")
        if not a.get("icon") or not a.get("icon_gray"):
            out.append(
                issue(
                    "missing",
                    "achievements",
                    f"{name}: missing icon(s).",
                    "Upload both icons (apply(section='achievements', upload_icons=true)).",
                    "Stats & Achievements",
                )
            )
        hidden = str(a.get("hidden")) in ("1", "true")
        if not _text(a.get("display_name")) or (not _text(a.get("description")) and not hidden):
            out.append(
                issue(
                    "missing",
                    "achievements",
                    f"{a.get('api_name')}: name or description is empty.",
                    "Fill it.",
                    "Stats & Achievements",
                )
            )
    if pending:
        out.append(
            issue(
                "check",
                "publishing",
                f"Unpublished changes in: {', '.join(pending)}.",
                "Publish them in Steamworks before the build review.",
                "Publish tab",
            )
        )
    return out
