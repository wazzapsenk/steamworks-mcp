"""Steamworks partner-site operations used by the BROWSER mode (the endpoints in docs/STEAMWORKS_INTERNALS.md).

All values use Steamworks' own vocabulary; :mod:`steamworks_mcp.execute.sync` maps them to steamworks.yaml.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import unquote_plus, urlsplit

from steamworks_mcp.execute.browser.html import parse
from steamworks_mcp.execute.browser.transport import NotLoggedInError, Response, Transport


class FormatError(RuntimeError):
    """A page no longer looks like it did when this tool was verified; nothing is written then."""


def _checked(res: Response, what: str) -> Response:
    if "goto=" in res.url or "goto=" in res.redirect:
        raise NotLoggedInError()
    if res.status >= 400:
        raise RuntimeError(f"{what}: HTTP {res.status}")
    return res


def _ok(res: Response, what: str) -> dict[str, Any]:
    data = _checked(res, what).json()
    if not isinstance(data, dict):
        raise FormatError(f"{what}: unexpected answer")
    if data.get("success") in (False, 0) or (
        data.get("success") is None and "deleted" not in data and "saved" not in data
    ):
        raise RuntimeError(f"{what}: {data.get('message') or data.get('error') or 'refused'}")
    return data


# ---------------------------------------------------------------------------------------------------- store text


async def store_item_id(t: Transport, appid: int) -> str:
    res = _checked(await t.get(f"/admin/game/editbyappid/{appid}"), "store page")
    m = re.search(r"/admin/game/edit/(\d+)", res.redirect or res.url)
    if not m:
        raise FormatError(f"App {appid} has no store page (playtests have none).")
    return m.group(1)


async def read_store_localization(t: Transport, item_id: str) -> dict[str, Any]:
    res = _checked(await t.get(f"/admin/game/downloadloc/{item_id}?language=all&format=json"), "store localization")
    data = res.json()
    if not isinstance(data, dict) or "languages" not in data:
        raise FormatError("The store localization export changed format.")
    return dict(data)


async def upload_store_localization(t: Transport, appid: int, data: dict[str, Any]) -> None:
    """Empty values are dropped: Steamworks clears a field whose value in the import is empty."""
    languages = {
        lang: kept
        for lang, fields in (data.get("languages") or {}).items()
        if (kept := {k: v for k, v in (fields or {}).items() if str(v or "").strip()})
    }
    if not languages:
        return
    raw = json.dumps({**data, "languages": languages}, ensure_ascii=False).encode("utf-8")
    _checked(await t.upload_store_localization(appid, "store_localization.json", raw), "store localization upload")


def serialized_app_data(page: str) -> dict[str, Any]:
    """The whole store item as the edit page embeds it (``<input name="serialized_app_data" value="{...}">``)."""
    m = re.search(r'name="serialized_app_data"[^>]*value="([^"]*)"', page) or re.search(
        r'value="([^"]*)"[^>]*name="serialized_app_data"', page
    )
    if not m:
        raise FormatError("The store page changed (serialized_app_data not found).")
    return dict(json.loads(html.unescape(m.group(1))))


async def read_store_form(t: Transport, item_id: str) -> tuple[dict[str, Any], str]:
    """The inputs of the store page's form (``#gameform``, the one carrying ``serialized_app_data``) and the page."""
    res = _checked(await t.get(f"/admin/game/edit/{item_id}"), "store page")
    form = next((f for f in parse(res.text).forms.values() if "serialized_app_data" in f), None)
    if form is None:
        raise FormatError("The store page changed (its form was not found).")
    return form, res.text


async def save_store_page(t: Transport, item_id: str, changes: dict[str, str]) -> None:
    """Post the store page's own form back with ``changes``. Steamworks redirects to the edit page, adding
    "Changes saved" only when something changed (an identical post gets no message); the caller reads back."""
    res = _checked(
        await t.submit_form(
            f"/admin/game/edit/{item_id}",
            "#gameform",
            f"/admin/game/save/{item_id}",
            [*changes.items(), ("activetab", "tab_basic")],
        ),
        "store page save",
    )
    where = unquote_plus(res.url + " " + res.redirect)
    if f"/admin/game/edit/{item_id}" not in where:
        raise RuntimeError(f"store page save: Steamworks answered with an unexpected page ({urlsplit(res.url).path}).")
    if m := re.search(r"[?&]errors?\[?\d*\]?=([^&]+)", where):
        raise RuntimeError(f"store page save: {m.group(1)}")


async def upload_store_image(t: Transport, item_id: str, part: str, file_name: str, data: bytes, mime: str) -> None:
    """One image for one Graphical Assets slot, as the page's own upload posts it (into the unpublished draft)."""
    res = _checked(
        await t.post_multipart(
            f"/admin/game/save/{item_id}?activetab=tab_graphicalassets&json=1", {}, {part: (file_name, data, mime)}
        ),
        "image upload",
    )
    try:
        answer = json.loads(res.text) if res.text.strip() else {}
    except ValueError:
        answer = {}
    if isinstance(answer, dict) and (answer.get("success") in (False, 0) or answer.get("error")):
        raise RuntimeError(f"image upload: {answer.get('error') or answer.get('message') or 'refused'}")


async def set_library_logo_position(
    t: Transport, item_id: str, pinned: str, width_pct: float, height_pct: float
) -> None:
    """What Steamworks' Library Logo position tool posts when the user clicks OK (into the unpublished draft)."""
    base = "app[assets][library_logo][logo_position]"
    fields = {
        "json": "1",
        f"{base}[pinned_position]": pinned,
        f"{base}[width_pct]": str(width_pct),
        f"{base}[height_pct]": str(height_pct),
    }
    _checked(
        await t.post_multipart(f"/admin/game/save/{item_id}?activetab=tab_graphicalassets&json=1", fields, {}),
        "library logo position",
    )


# ---------------------------------------------------------------------------------------------------- achievements


async def read_achievements(t: Transport, appid: int) -> dict[str, Any]:
    page = _checked(await t.get(f"/apps/achievements/{appid}"), "achievements page")
    ids = {k: re.search(rf'id="{k}"[^>]*>\s*(-?\d+)', page.text) for k in ("max_statid_used", "max_bitid_used")}
    data = _checked(await t.get(f"/apps/fetchachievements/{appid}"), "achievements").json()
    if not isinstance(data, dict) or "achievements" not in data:
        raise FormatError("The achievements list changed format.")
    return {
        "achievements": data["achievements"],
        "languages": data.get("languages", {}),
        "max_statid": ids["max_statid_used"].group(1)
        if ids["max_statid_used"]
        else str(max([int(a["stat_id"]) for a in data["achievements"]] or [0])),
        "max_bitid": ids["max_bitid_used"].group(1) if ids["max_bitid_used"] else "-1",
    }


async def new_achievement(t: Transport, appid: int, max_stat: str, max_bit: str) -> dict[str, Any]:
    return _ok(
        await t.post(f"/apps/newachievement/{appid}", {"maxstatid": max_stat, "maxbitid": max_bit}), "new achievement"
    )


def localized(value: dict[str, str]) -> str:
    """As the page sends it: languages without text dropped; English only collapses to a plain string."""
    clean = {k: v for k, v in value.items() if v}
    return json.dumps(
        clean["english"] if list(clean) == ["english"] else clean, ensure_ascii=False, separators=(",", ":")
    )


async def save_achievement(
    t: Transport,
    appid: int,
    stat: str,
    bit: str,
    api_name: str,
    names: dict[str, str],
    descriptions: dict[str, str],
    hidden: bool,
    permission: int = 0,
) -> dict[str, Any]:
    """``permission`` (0 client, 1 game server, 2 official game server) is sent back as Steamworks has it, so saving
    never changes who may unlock the achievement."""
    data = _ok(
        await t.post(
            f"/apps/saveachievement/{appid}",
            {
                "statid": stat,
                "bitid": bit,
                "apiname": api_name,
                "displayname": localized(names),
                "description": localized(descriptions),
                "permission": str(permission),
                "hidden": "true" if hidden else "false",
                "progressStat": "-1",
                "progressMin": "",
                "progressMax": "",
            },
        ),
        f"save achievement {api_name}",
    )
    if not data.get("saved"):
        raise RuntimeError(f"Steamworks did not save {api_name}.")
    return data


async def upload_achievement_icon(
    t: Transport, appid: int, stat: str, bit: str, jpg: bytes, locked: bool, name: str
) -> None:
    res = await t.post_multipart(
        "/images/uploadachievement",
        {
            "MAX_FILE_SIZE": "3000000",
            "appID": str(appid),
            "statID": stat,
            "bit": bit,
            "requestType": "achievement_gray" if locked else "achievement",
        },
        {"image": (name, jpg, "image/jpeg")},
    )
    _ok(res, f"icon {name}")


async def delete_achievement(t: Transport, appid: int, stat: str, bit: str) -> None:
    data = _checked(await t.post(f"/apps/deleteachievement/{appid}/{stat}/{bit}", {}), "delete achievement").json()
    if not data.get("deleted"):
        raise RuntimeError("Steamworks did not delete the achievement.")


# ---------------------------------------------------------------------------------------------------- Steam Cloud


def _num(v: Any) -> int:
    digits = re.sub(r"\D", "", str(v or "0"))
    return int(digits or 0)


async def read_cloud(t: Transport, appid: int) -> dict[str, Any]:
    res = _checked(await t.get(f"/apps/cloud/{appid}"), "Steam Cloud page")
    page = parse(res.text)
    if "ufsQuota" not in page.by_id or not page.session_id:
        raise FormatError("The Steam Cloud page changed (quota inputs not found).")
    roots, overrides = [], []
    for name, f in page.forms.items():
        m = re.match(r"AutoCloud(Path|Override)Form(\d+)$", name)
        if not m:
            continue
        if m.group(1) == "Path":
            roots.append(
                {
                    "index": int(m.group(2)),
                    "root": f.get("root", ""),
                    "path": f.get("path", ""),
                    "pattern": f.get("pattern", ""),
                    "os": f.get("oslist", ""),
                    "recursive": bool(f.get("recursive")),
                }
            )
        else:
            overrides.append(
                {
                    "index": int(m.group(2)),
                    "root": f.get("root", ""),
                    "os": f.get("os", ""),
                    "use_instead": f.get("useinstead", ""),
                    "add_path": f.get("addpath", ""),
                    "replace_path": bool(f.get("replacepath")),
                }
            )
    return {
        "byte_quota": _num(page.by_id["ufsQuota"]["value"]),
        "file_quota": _num(page.by_id.get("ufsFiles", {}).get("value")),
        "shared_appid": _num(page.by_id.get("relatedAppID", {}).get("value")),
        "developers_only": bool(page.by_id.get("ufsHideInClient", {}).get("checked")),
        "sync_on_suspend": bool(page.by_id.get("ufsAllowSyncOnSuspend", {}).get("checked")),
        "roots": sorted(roots, key=lambda r: r["index"]),
        "overrides": sorted(overrides, key=lambda r: r["index"]),
    }


async def set_ufs(t: Transport, appid: int, s: dict[str, Any]) -> None:
    _ok(
        await t.post(
            f"/apps/setufsparameters/{appid}",
            {
                "cb": str(s["byte_quota"]),
                "cfiles": str(s["file_quota"]),
                "appidRedirect": str(s["shared_appid"]),
                "hideInClient": "1" if s["developers_only"] else "0",
                "syncOnSuspend": "1" if s["sync_on_suspend"] else "0",
            },
        ),
        "Steam Cloud quotas",
    )


async def set_root(t: Transport, appid: int, r: dict[str, Any]) -> None:
    _ok(
        await t.post(
            f"/apps/setautocloudpath/{appid}",
            {
                "index": str(r["index"]),
                "root": r["root"],
                "path": r["path"],
                "pattern": r["pattern"],
                "oslist": r["os"],
                "recursive": "true" if r["recursive"] else "false",
            },
        ),
        f"Auto-Cloud path {r['index']}",
    )


async def delete_root(t: Transport, appid: int, index: int) -> None:
    _ok(
        await t.post(
            f"/apps/setautocloudpath/{appid}",
            {"index": str(index), "root": "", "path": "", "pattern": "", "oslist": ""},
        ),
        f"delete Auto-Cloud path {index}",
    )


async def set_override(t: Transport, appid: int, o: dict[str, Any]) -> None:
    _ok(
        await t.post(
            f"/apps/setautocloudoverride/{appid}",
            {
                "index": str(o["index"]),
                "root": o["root"],
                "os": o["os"],
                "useinstead": o["use_instead"],
                "addpath": o["add_path"],
                "replacepath": "true" if o["replace_path"] else "false",
            },
        ),
        f"Auto-Cloud override {o['index']}",
    )


async def delete_override(t: Transport, appid: int, index: int) -> None:
    _ok(
        await t.post(
            f"/apps/setautocloudoverride/{appid}",
            {"index": str(index), "root": "", "os": "", "useinstead": "", "addpath": "", "replacepath": ""},
        ),
        f"delete override {index}",
    )


# ---------------------------------------------------------------------------------------------------- installation

LAUNCH_FIELDS = (
    "executable",
    "arguments",
    "working_dir",
    "type",
    "os",
    "arch",
    "oscpu",
    "beta_key",
    "owns_dlc",
    "realm",
    "steamdeck",
)
FORM_NAMES = {
    "executable": "executable",
    "arguments": "argumentsx",
    "working_dir": "workingdir",
    "type": "type",
    "os": "osversion",
    "arch": "osarch",
    "oscpu": "oscpu",
    "beta_key": "betakey",
    "owns_dlc": "ownsdlc",
    "realm": "realm",
    "steamdeck": "steamdeck",
}


async def read_installation(t: Transport, appid: int) -> dict[str, Any]:
    res = _checked(await t.get(f"/apps/config/{appid}"), "Installation page")
    page = parse(res.text)
    if "InstallFolderForm" not in page.forms:
        raise FormatError("The Installation page changed (install folder form not found).")
    options = []
    for name, f in page.forms.items():
        m = re.match(r"LaunchForm(\d+)$", name)
        if not m:
            continue
        i = m.group(1)
        descriptions = {}
        for key, value in page.named.items():
            lm = re.match(rf"Launch_{i}_description_loc\[([a-z]+)\]$", key)
            if lm and value:
                descriptions[lm.group(1)] = value
        option = {k: str(f.get(v, "")).strip() for k, v in FORM_NAMES.items()}
        options.append({"index": int(i), **option, "descriptions": descriptions})
    return {
        "install_folder": str(page.forms["InstallFolderForm"].get("installfolder", "")),
        "launch_options": sorted(options, key=lambda o: o["index"]),
    }


async def set_install_folder(t: Transport, appid: int, folder: str) -> None:
    _ok(await t.post(f"/apps/setappinstallfolder/{appid}", {"installfolder": folder}), "install folder")


async def set_launch_option(t: Transport, appid: int, o: dict[str, Any]) -> None:
    form = {
        "index": str(o["index"]),
        "executable": o["executable"],
        "arguments": o["arguments"],
        "workingdir": o["working_dir"],
        "type": o["type"],
        "description": o["descriptions"].get("english", ""),
        "osversion": o["os"],
        "osarch": o["arch"],
        "oscpu": o.get("oscpu", ""),
        "betakey": o["beta_key"],
        "ownsdlc": o["owns_dlc"],
        "realm": o.get("realm", ""),
        "steamdeck": o.get("steamdeck", ""),
    }
    for lang, text in o["descriptions"].items():
        if text:
            form[f"description_{lang}"] = text
    _ok(await t.post(f"/apps/setlaunchoption/{appid}", form), f"launch option {o['index']}")


async def delete_launch_option(t: Transport, appid: int, index: int) -> None:
    """What the page's own Delete button sends: every field empty except the index."""
    empty = dict.fromkeys(
        (
            "executable",
            "arguments",
            "workingdir",
            "type",
            "description",
            "osversion",
            "osarch",
            "oscpu",
            "betakey",
            "ownsdlc",
            "realm",
            "steamdeck",
        ),
        "",
    )
    res = await t.post(f"/apps/setlaunchoption/{appid}", {"index": str(index), **empty})
    _checked(res, f"delete launch option {index}")


# ---------------------------------------------------------------------------------------------------- pending changes


def _plain(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


SECTION = re.compile(
    r'===\s*(?:Changes to "([^"]+)" section[^=]*|"([^"]+)" section is new\s*)===(.*?)(?=<br>\s*===|$)', re.S
)


def _squeeze(parts: list[str]) -> str:
    """Without whitespace and empty KeyValues blocks (deleting the last launch option leaves ``"launch" { }``)."""
    s = re.sub(r"\s+", "", "".join(parts))
    while True:
        shorter = re.sub(r'"[^"]*"\{\}', "", s)
        if shorter == s:
            return s
        s = shorter


def parse_diff(diff_html: str) -> dict[str, dict[str, Any]]:
    """Per app section: whether it is new, the removed and added text, and whether anything really changed.

    Saving a page with the same values still opens a new revision; its diff is empty or differs only in whitespace,
    which does not count as a change. A new section's content is not shown, so it always counts as changed.
    """
    out: dict[str, dict[str, Any]] = {}
    for m in SECTION.finditer(diff_html):
        body = m.group(3)
        removed = [_plain(x) for x in re.findall(r"<del[^>]*>(.*?)</del>", body, re.S)]
        added = [_plain(x) for x in re.findall(r"<ins[^>]*>(.*?)</ins>", body, re.S)]
        new = m.group(2) is not None
        out[m.group(1) or m.group(2)] = {
            "new": new,
            "removed": removed,
            "added": added,
            "changed": new or _squeeze(removed) != _squeeze(added),
        }
    return out


async def pending_changes(t: Transport, appid: int) -> dict[str, Any]:
    """What is still unpublished (the Publish page's read-only "View Diffs"): plain text, and per app section."""
    data = _checked(await t.post(f"/apps/diff/{appid}", {"section": "technical"}), "pending changes").json()
    sections = parse_diff(str(data.get("diff", "")))
    return {
        "text": _plain(f"{data.get('opened', '')}{data.get('diff', '')}"),
        "sections": sections,
        "changed_sections": sorted(k for k, v in sections.items() if v["changed"]),
    }


# ---------------------------------------------------------------------------------------------------- checklists

PANEL = re.compile(r'<div class="panel checklist[^"]*">(.*?)(?=<div class="panel |\Z)', re.S)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def parse_checklists(page: str) -> list[dict[str, Any]]:
    """The release checklists of an app's landing page ("Your Store Presence", "Your Game Build"): every item with
    its section, status (complete, incomplete, suggested) and Valve's explanation."""
    items = []
    for panel in PANEL.finditer(page):
        body = panel.group(1)
        title = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
        name = _text(title.group(1)) if title else ""
        for part in re.split(r'<div class="sectionTitle_small">', body)[1:]:
            section = _text(part.split("</div>")[0]).replace("(?)", "").strip()
            for status, row in re.findall(r'<tr class="checklist_(\w+)">(.*?)</tr>', part, re.S):
                tip = re.search(r'data-tooltip-text="([^"]*)"', row)
                link = re.search(r'href="(https://partner\.steamgames\.com[^"]+)"', row)
                items.append(
                    {
                        "checklist": name,
                        "section": section,
                        "item": _text(re.sub(r'<a class="ttip.*?</a>', "", row, flags=re.S))
                        .replace("\u2714", "")
                        .strip(),
                        "status": status,
                        "explanation": html.unescape(tip.group(1)) if tip else "",
                        "link": html.unescape(link.group(1)) if link else "",
                    }
                )
    return items


async def read_checklists(t: Transport, appid: int) -> list[dict[str, Any]]:
    page = _checked(await t.get(f"/apps/landing/{appid}"), "app landing page").text
    items = parse_checklists(page)
    if not items:
        raise FormatError("The app landing page changed (release checklists not found).")
    return items


def item_from_url(url: str) -> str | None:
    m = re.search(r"/admin/game/edit/(\d+)", urlsplit(url).path)
    return m.group(1) if m else None
