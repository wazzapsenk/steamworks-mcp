"""The store page form (Store Admin > Edit Store Page): links, support info, legal line, system requirements,
platforms, the language table, genres, categories and third-party DRM / accounts.

Steamworks saves that whole form at once (``/admin/game/save/<storeItemId>``). The tool posts the page's own form
back with only the changed inputs replaced. Tested live: an unchanged post leaves the store item exactly as it was,
and inputs the static form does not carry (About, the social links) keep their values. Saves go into the
unpublished draft; publishing stays with the user.

Hidden "fancy checkbox" inputs hold ``"true"`` when ticked and ``""`` when not (some start as ``"1"``).
"""

from __future__ import annotations

import re
from typing import Any

from steamworks_mcp.execute import sync
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport

SECTION = "store_page"
MANAGED = re.compile(
    r"^(app\[content\]\[(links|support_info|legal|sysreqs|supported_languages)\]|app\[platforms\]|rgGenres\["
    r"|app\[classification\]\[(category|primary_genre)\]|app\[game\]\[(3pdrm|3pacc)\])"
)
OS = {"windows": "windows", "macos": "mac", "linux": "linux"}
PLATFORMS = {"windows": "win", "macos": "mac", "linux": "linux"}
LEVELS = {"minimum": "min", "recommended": "rec"}
TEXT_REQUIREMENTS = {
    "os": "osversion",
    "processor": "processor",
    "graphics": "graphics",
    "sound_card": "soundcard",
    "vr_support": "vrsupport",
    "additional_notes": "notes",
}
SIZE_REQUIREMENTS = {"memory": "memory", "storage": "diskspace"}
CATEGORIES = {
    1: "Multi-player",
    2: "Single-player",
    9: "Co-op",
    22: "Steam Achievements",
    23: "Steam Cloud",
    24: "Shared/Split Screen",
    25: "Steam Leaderboards",
    27: "Cross-Platform Multiplayer",
    29: "Steam Trading Cards",
    30: "Steam Workshop",
    35: "In-App Purchases",
    36: "Online PvP",
    37: "Shared/Split Screen PvP",
    38: "Online Co-op",
    39: "Shared/Split Screen Co-op",
    41: "Remote Play on Phone",
    42: "Remote Play on Tablet",
    43: "Remote Play on TV",
    44: "Remote Play Together",
    47: "LAN PvP",
    48: "LAN Co-op",
    49: "PvP",
    62: "Family Sharing",
    63: "Steam Timeline",
}
"""Steam category ids and their store names. Controller support (18, 28, 55-60) and accessibility (64-82) are set
by Steamworks' wizards, which also record that the wizard was completed; they stay with the user."""
WIZARD_ONLY = {"partial controller support", "full controller support"}


def ticked(value: Any) -> bool:
    return bool(value) and str(value) not in ("", "false")


def size(text: str) -> tuple[str, str] | None:
    """ "8 GB" -> ("8", "GB"); "1.5 GB" -> ("1536", "MB"). Steamworks takes whole numbers in MB or GB."""
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(MB|GB|TB)?\s*(RAM|available space)?\s*", text, re.I)
    if not m:
        return None
    amount, unit = float(m.group(1).replace(",", ".")), (m.group(2) or "GB").upper()
    if unit == "TB":
        amount, unit = amount * 1024, "GB"
    if not amount.is_integer():
        amount, unit = (amount * 1024, "MB") if unit == "GB" else (round(amount), unit)
    return str(int(amount)), unit


def directx(text: str) -> str | None:
    m = re.search(r"(\d+(?:\.\d+)?[a-c]?)", text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------------------------------- page


def read(form: dict[str, Any], page: str) -> dict[str, Any]:
    """The managed inputs of the store form, plus the page's genre names (``OnGenreSelect(this, '1', 'Action')``)."""
    genres = {name: gid for gid, name in re.findall(r"OnGenreSelect\(\s*this,\s*'(\d+)',\s*'([^']+)'\)", page)}
    return {
        "form": {
            k: ("true" if v is True else "" if v is False else str(v)) for k, v in form.items() if MANAGED.match(k)
        },
        "genres": genres,
    }


async def read_section(t: Transport, appid: int) -> dict[str, Any]:
    item = await P.store_item_id(t, appid)
    form, page = await P.read_store_form(t, item)
    return {"item_id": item, **read(form, page)}


# ---------------------------------------------------------------------------------------------------- desired


def desired(values: dict[str, Any], current: dict[str, Any], remove_extra: bool) -> dict[str, Any]:
    """``{"inputs": {form input: value}, "problems": [...]}``: every filled field of steamworks.yaml this section
    writes, and the values it cannot write (unknown names, sizes it cannot read). Empty fields are left out: they
    never clear Steam's."""
    store = values.get("store") or {}
    lang = str(values.get("source_language") or "english")
    form = current["form"]
    out: dict[str, str] = {}
    problems: list[str] = []

    def put(name: str, value: Any) -> None:
        if name in form:
            out[name] = value
        else:
            problems.append(f"{name}: not on this store page")

    for key in ("website", "privacy_policy", "forums", "online_manual"):
        if (store.get("links") or {}).get(key):
            put(f"app[content][links][{key}]", store["links"][key])
    for key in ("url", "email", "phone"):
        if (store.get("support") or {}).get(key):
            put(f"app[content][support_info][{key}]", store["support"][key])
    if (store.get("legal") or {}).get("legal_line"):
        put(f"app[content][legal][{lang}]", store["legal"]["legal_line"])
    for os_name, levels in (store.get("system_requirements") or {}).items():
        for level, req in (levels or {}).items():
            if not req:
                continue
            base = f"app[content][sysreqs][{OS[os_name]}][{LEVELS[level]}]"
            for key, field in TEXT_REQUIREMENTS.items():
                if req.get(key):
                    put(f"{base}[{field}][{lang}]", req[key])
            for key, field in SIZE_REQUIREMENTS.items():
                if req.get(key):
                    parsed = size(req[key])
                    if parsed is None:
                        problems.append(f"store.system_requirements.{os_name}.{level}.{key}: write it as '8 GB'")
                    else:
                        put(f"{base}[{field}][amount]", parsed[0])
                        put(f"{base}[{field}][units]", parsed[1])
            if req.get("directx") and os_name == "windows":
                version = directx(req["directx"])
                if version:
                    put(f"{base}[directx]", version)
            if req.get("network"):
                put(f"{base}[broadband]", "true")
    if store.get("platforms"):
        for os_name, key in PLATFORMS.items():
            if os_name in store["platforms"]:
                put(f"app[platforms][{key}]", "true")
            elif remove_extra and ticked(form.get(f"app[platforms][{key}]")):
                put(f"app[platforms][{key}]", "")
    for code, row in (store.get("supported_languages") or {}).items():
        for key, field in (("interface", "supported"), ("full_audio", "full_audio"), ("subtitles", "subtitles")):
            put(f"app[content][supported_languages][{code}][{field}]", "true" if row.get(key) else "")
    genre_ids = {name.lower(): gid for name, gid in current["genres"].items()}
    wanted_genres = {g.lower(): g for g in [*(store.get("genres") or []), store.get("primary_genre") or ""] if g}
    for name, given in sorted(wanted_genres.items()):
        if name in genre_ids:
            put(f"rgGenres[{genre_ids[name]}]", "true")
        else:
            problems.append(f"genre {given!r}: not a Steam genre ({', '.join(sorted(current['genres']))})")
    if wanted_genres and remove_extra:
        for name, gid in genre_ids.items():
            if name not in wanted_genres and ticked(form.get(f"rgGenres[{gid}]")):
                put(f"rgGenres[{gid}]", "")
    if store.get("primary_genre") and store["primary_genre"].lower() in genre_ids:
        put("app[classification][primary_genre]", genre_ids[store["primary_genre"].lower()])
    category_ids = {name.lower(): cid for cid, name in CATEGORIES.items()}
    wanted_categories = {c.lower(): c for c in store.get("categories") or []}
    for name, given in sorted(wanted_categories.items()):
        if name in category_ids:
            put(f"app[classification][category][category_{category_ids[name]}]", "true")
        elif name in WIZARD_ONLY:
            problems.append(f"category {given!r}: set it with the Controller Support wizard in Steamworks")
        else:
            problems.append(f"category {given!r}: not a category this tool writes ({', '.join(CATEGORIES.values())})")
    if wanted_categories and remove_extra:
        for name, cid in category_ids.items():
            key = f"app[classification][category][category_{cid}]"
            if name not in wanted_categories and ticked(form.get(key)):
                put(key, "")
    third = store.get("third_party") or {}
    if third.get("drm"):
        put("app[game][3pdrm][provider]", third["drm"])
    if third.get("account"):
        put("app[game][3pacc][provider]", third["account"])
    if third.get("account_links_to_steam") is not None:
        put("app[game][3pacc][canlinktosteam]", "true" if third["account_links_to_steam"] else "")
    return {"inputs": out, "problems": problems}


def differs(current: str | None, want: str) -> bool:
    if want in ("true", ""):
        return ticked(current) != ticked(want)
    return (current or "").strip() != want.strip()


def changes(want: dict[str, str], current: dict[str, Any], force: bool = False) -> dict[str, str]:
    form = current["form"]
    return {k: v for k, v in want.items() if force or differs(form.get(k), v)}


def plan(item_id: str, want: dict[str, Any], current: dict[str, Any], force: bool = False) -> list[sync.Op]:
    """One save of the inputs that differ, then one "skip" per value that cannot be written."""
    diff = changes(want["inputs"], current, force)
    ops: list[sync.Op] = []
    if diff:

        async def save(t: Transport, c: dict[str, str] = diff) -> None:
            await P.save_store_page(t, item_id, c)

        before = {k: current["form"].get(k, "") for k in diff}
        ops.append(sync.Op(SECTION, "save", f"{len(diff)} input(s) of the store page form", before, diff, save))
    ops += [sync.Op(SECTION, "skip", p, None, "not written", sync._nothing) for p in want["problems"]]
    return ops
