"""Plans: what has to change in Steamworks so it matches steamworks.yaml (or a snapshot, for restores).

Values are first translated into Steamworks' own vocabulary (``desired_*``), then one planner per area compares
them with what Steamworks shows. Only approved values are written; rows that exist only in Steamworks are left
alone unless ``remove_extra`` is set.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport
from steamworks_mcp.localization.store import Localization
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.state import State, is_empty

SECTIONS = ("cloud", "installation", "achievements", "store_text")
CLOUD_OS = {"all": "", "windows": "Windows", "macos": "MacOS", "linux": "Linux", "android": "Android"}
LAUNCH_OS = {"all": "", "windows": "windows", "macos": "macos", "linux": "linux", "android": "android"}
ARCH = {"all": "", "32": "32", "64": "64"}
STORE_FIELDS = {"store.short_description": "app[content][short_description]", "store.about": "app[content][about]"}
OK_STATUS = ("approved", "applied")


@dataclass
class Op:
    area: str
    action: str
    target: str
    before: Any
    after: Any
    run: Callable[[Transport], Awaitable[None]]

    def describe(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "action": self.action,
            "target": self.target,
            "before": self.before,
            "after": self.after,
        }


# ---------------------------------------------------------------------------------------------------- approval


def written(section: str, app: str, path: str, icons: bool = False) -> bool:
    """Whether ``apply(section)`` writes this steamworks.yaml field to Steamworks."""
    if section == "cloud":
        return path.startswith(f"apps.{app}.cloud.")
    if section == "installation":
        return path.startswith(f"apps.{app}.installation.")
    if section == "achievements":
        m = re.match(r"achievements\.[^.]+\.([a-z_]+)$", path)
        return m is not None and m.group(1) in (
            ("name", "description", "hidden") + (("icon", "icon_locked") if icons else ())
        )
    if section == "store_text":
        return path in STORE_FIELDS
    if section == "leaderboards":
        return path.startswith("leaderboards.")
    if section == "build":
        return path.startswith(f"apps.{app}.builds.depots.")
    raise ValueError(f"unknown section {section}")


def not_approved(values: dict[str, Any], state: State, section: str, app: str, icons: bool = False) -> list[str]:
    """Fields the section would write that the user has not approved yet."""
    out = []
    for path, value in fp.iter_fields(values):
        if written(section, app, path, icons) and not is_empty(value):
            fs = state.fields.get(path)
            if fs is None or fs.status not in OK_STATUS:
                out.append(path)
    return out


def approved_translations(values: dict[str, Any], state: State, root: Path, prefix: str) -> dict[str, dict[str, str]]:
    """``{language: {key: text}}`` of approved translations whose key starts with ``prefix``."""
    loc = Localization(root)
    out: dict[str, dict[str, str]] = {}
    for lang in values.get("target_languages") or []:
        for key, text in loc.read(lang).items():
            fs = state.fields.get(f"localization.{lang}.{key}")
            if key.startswith(prefix) and text and fs is not None and fs.status in OK_STATUS:
                out.setdefault(lang, {})[key] = text
    return out


# ---------------------------------------------------------------------------------------------------- desired state


def desired_cloud(values: dict[str, Any], app: str, current: dict[str, Any]) -> dict[str, Any]:
    c = fp.get(values, f"apps.{app}.cloud") or {}
    enabled = c.get("enabled")
    quota = c.get("byte_quota")
    out = {
        "byte_quota": 0 if enabled is False else (quota if quota is not None else current["byte_quota"]),
        "file_quota": 0
        if enabled is False
        else (c.get("file_quota") if c.get("file_quota") is not None else current["file_quota"]),
        "shared_appid": c.get("shared_appid") if c.get("shared_appid") is not None else current["shared_appid"],
        "developers_only": c.get("developers_only")
        if c.get("developers_only") is not None
        else current["developers_only"],
        "sync_on_suspend": c.get("sync_on_suspend")
        if c.get("sync_on_suspend") is not None
        else current["sync_on_suspend"],
        "roots": [
            {
                "index": i,
                "root": r["root"],
                "path": r.get("subdirectory", ""),
                "pattern": r["pattern"],
                "os": CLOUD_OS[r.get("os", "all")],
                "recursive": bool(r.get("recursive")),
            }
            for i, r in enumerate(c.get("auto_cloud") or [])
        ],
        "overrides": [
            {
                "index": i,
                "root": o["root"],
                "os": CLOUD_OS[o["os"]],
                "use_instead": o["use_instead"],
                "add_path": o.get("add_path", ""),
                "replace_path": bool(o.get("replace_path")),
            }
            for i, o in enumerate(c.get("overrides") or [])
        ],
    }
    return out


def desired_installation(
    values: dict[str, Any], app: str, current: dict[str, Any], translations: dict[str, dict[str, str]]
) -> dict[str, Any]:
    inst = fp.get(values, f"apps.{app}.installation") or {}
    by_index = {o["index"]: o for o in current["launch_options"]}
    options = []
    for i, o in enumerate(inst.get("launch_options") or []):
        cur = by_index.get(i, {})
        descriptions = dict(cur.get("descriptions") or {})  # languages this project does not manage stay as they are
        if o.get("description"):
            descriptions["english"] = o["description"]
        key = f"apps.{app}.installation.launch_options.{i}.description"
        for lang, texts in translations.items():
            if texts.get(key):
                descriptions[lang] = texts[key]
        options.append(
            {
                "index": i,
                "executable": o["executable"],
                "arguments": o.get("arguments", ""),
                "working_dir": o.get("working_dir", ""),
                "type": o.get("type", "default"),
                "os": LAUNCH_OS[o.get("os", "windows")],
                "arch": ARCH[o.get("arch", "64")],
                "oscpu": cur.get("oscpu", ""),
                "beta_key": o.get("beta_key", ""),
                "owns_dlc": o.get("owns_dlc", ""),
                "realm": cur.get("realm", ""),
                "steamdeck": cur.get("steamdeck", ""),
                "descriptions": descriptions,
            }
        )
    folder = inst.get("install_folder")
    return {"install_folder": folder if folder is not None else current["install_folder"], "launch_options": options}


def desired_achievements(values: dict[str, Any], translations: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    source = str(values.get("source_language") or "english")
    out = []
    for a in values.get("achievements") or []:
        names = {source: a.get("name") or ""}
        descs = {source: a.get("description") or ""}
        for lang, texts in translations.items():
            names[lang] = texts.get(f"achievements.{a['id']}.name", "")
            descs[lang] = texts.get(f"achievements.{a['id']}.description", "")
        out.append(
            {
                "api_name": a["id"],
                "names": {k: v for k, v in names.items() if v},
                "descriptions": {k: v for k, v in descs.items() if v},
                "hidden": bool(a.get("hidden")),
            }
        )
    return out


def desired_store(values: dict[str, Any], translations: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    source = str(values.get("source_language") or "english")
    out = {source: {STORE_FIELDS[f]: fp.get(values, f) for f in STORE_FIELDS if fp.get(values, f)}}
    for lang, texts in translations.items():
        fields = {STORE_FIELDS[f]: texts[f] for f in STORE_FIELDS if texts.get(f)}
        if fields:
            out[lang] = fields
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------------------------------- planners


def plan_cloud(
    appid: int, desired: dict[str, Any], current: dict[str, Any], remove_extra: bool, force: bool = False
) -> list[Op]:
    """Rows first, quotas and flags last: saving an Auto-Cloud row turns "developers only" back on in
    Steamworks (seen live), so the flags are written again after any row changed."""
    ops: list[Op] = []
    keys = ("byte_quota", "file_quota", "shared_appid", "developers_only", "sync_on_suspend")
    for kind, setter, deleter in (
        ("roots", P.set_root, P.delete_root),
        ("overrides", P.set_override, P.delete_override),
    ):
        cur = {r["index"]: r for r in current[kind]}
        for row in desired[kind]:
            if force or cur.get(row["index"]) != row:

                async def put(t: Transport, r: dict[str, Any] = row, f: Any = setter) -> None:
                    await f(t, appid, r)

                ops.append(Op("cloud", "set", f"{kind[:-1]} #{row['index']}", cur.get(row["index"]), row, put))
        extra = sorted((i for i in cur if i >= len(desired[kind])), reverse=True)
        for i in extra if remove_extra else []:

            async def drop(t: Transport, idx: int = i, f: Any = deleter) -> None:
                await f(t, appid, idx)

            ops.append(Op("cloud", "delete", f"{kind[:-1]} #{i}", cur[i], None, drop))
    if force or ops or {k: desired[k] for k in keys} != {k: current[k] for k in keys}:
        new = {k: desired[k] for k in keys}

        async def ufs(t: Transport, s: dict[str, Any] = new) -> None:
            await P.set_ufs(t, appid, s)

        ops.append(Op("cloud", "set", "quotas and flags", {k: current[k] for k in keys}, new, ufs))
    return ops


def plan_installation(
    appid: int, desired: dict[str, Any], current: dict[str, Any], remove_extra: bool, force: bool = False
) -> list[Op]:
    ops: list[Op] = []
    if (force or desired["install_folder"] != current["install_folder"]) and desired["install_folder"]:

        async def folder(t: Transport, v: str = desired["install_folder"]) -> None:
            await P.set_install_folder(t, appid, v)

        ops.append(
            Op("installation", "set", "install folder", current["install_folder"], desired["install_folder"], folder)
        )
    cur = {o["index"]: o for o in current["launch_options"]}
    for o in desired["launch_options"]:
        if force or cur.get(o["index"]) != o:

            async def put(t: Transport, opt: dict[str, Any] = o) -> None:
                await P.set_launch_option(t, appid, opt)

            ops.append(Op("installation", "set", f"launch option #{o['index']}", cur.get(o["index"]), o, put))
    for i in sorted((i for i in cur if i >= len(desired["launch_options"])), reverse=True) if remove_extra else []:

        async def drop(t: Transport, idx: int = i) -> None:
            await P.delete_launch_option(t, appid, idx)

        ops.append(Op("installation", "delete", f"launch option #{i}", cur[i], None, drop))
    return ops


async def _nothing(t: Transport) -> None:
    return None


def _as_map(v: Any) -> dict[str, str]:
    if isinstance(v, str):
        return {"english": v} if v else {}
    return {k: s for k, s in (v or {}).items() if k != "token" and s}


def _hidden(v: Any) -> bool:
    return v in (True, 1, "1", "true")


def plan_achievements(
    appid: int,
    desired: list[dict[str, Any]],
    current: dict[str, Any],
    remove_extra: bool,
    icons: dict[str, tuple[bytes, bytes]] | None = None,
    force: bool = False,
) -> list[Op]:
    ops: list[Op] = []
    by_name = {a["api_name"]: a for a in current["achievements"]}
    counter = {"stat": current["max_statid"], "bit": current["max_bitid"]}
    for d in desired:
        cur = by_name.get(d["api_name"])
        merged_names = {**_as_map(cur["display_name"]), **d["names"]} if cur else d["names"]
        merged_descs = {**_as_map(cur["description"]), **d["descriptions"]} if cur else d["descriptions"]
        changed = (
            cur is None
            or merged_names != _as_map(cur["display_name"])
            or merged_descs != _as_map(cur["description"])
            or d["hidden"] != _hidden(cur.get("hidden"))
        )
        icon = (icons or {}).get(d["api_name"])
        if not changed and icon is None and not force:
            continue
        if cur is not None and cur.get("progress"):
            ops.append(
                Op(
                    "achievements",
                    "skip",
                    d["api_name"],
                    None,
                    "Has a progress bar in Steamworks that saving through this tool would reset; edit it there.",
                    _nothing,
                )
            )
            continue

        async def run(
            t: Transport,
            want: dict[str, Any] = d,
            existing: dict[str, Any] | None = cur,
            names: dict[str, str] = merged_names,
            descs: dict[str, str] = merged_descs,
            art: tuple[bytes, bytes] | None = icon,
        ) -> None:
            if existing is None:
                made = await P.new_achievement(t, appid, counter["stat"], counter["bit"])
                counter["stat"], counter["bit"] = str(made["maxstatid"]), str(made["maxbitid"])
                stat, bit = str(made["achievement"]["stat_id"]), str(made["achievement"]["bit_id"])
            else:
                stat, bit = str(existing["stat_id"]), str(existing["bit_id"])
            permission = int((existing or {}).get("permission") or 0)
            await P.save_achievement(t, appid, stat, bit, want["api_name"], names, descs, want["hidden"], permission)
            if art:
                await P.upload_achievement_icon(t, appid, stat, bit, art[0], False, f"{want['api_name']}.jpg")
                await P.upload_achievement_icon(t, appid, stat, bit, art[1], True, f"{want['api_name']}_locked.jpg")

        before = (
            None
            if cur is None
            else {
                "names": _as_map(cur["display_name"]),
                "descriptions": _as_map(cur["description"]),
                "hidden": _hidden(cur.get("hidden")),
            }
        )
        after = {
            "names": merged_names,
            "descriptions": merged_descs,
            "hidden": d["hidden"],
            **({"icons": "upload"} if icon else {}),
        }
        ops.append(
            Op(
                "achievements",
                "create" if cur is None else "update" if changed or icon else "rewrite",
                d["api_name"],
                before,
                after,
                run,
            )
        )
    wanted = {d["api_name"] for d in desired}
    for name, cur in by_name.items() if remove_extra else []:
        if name in wanted:
            continue

        async def drop(t: Transport, a: dict[str, Any] = cur) -> None:
            await P.delete_achievement(t, appid, str(a["stat_id"]), str(a["bit_id"]))

        ops.append(Op("achievements", "delete", name, {"names": _as_map(cur["display_name"])}, None, drop))
    return ops


def normalize_store_text(s: str) -> str:
    """Steamworks' editor wraps paragraphs in [p]…[/p]; compare without that noise."""
    s = s.replace("\r\n", "\n")
    s = re.sub(r"\[/?p\]", "\n", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def plan_store(
    appid: int, item_id: str, desired: dict[str, dict[str, str]], current: dict[str, Any], force: bool = False
) -> list[Op]:
    changes: dict[str, dict[str, str]] = {}
    before: dict[str, dict[str, str]] = {}
    for lang, fields in desired.items():
        cur = current["languages"].get(lang)
        cur_fields = cur if isinstance(cur, dict) else {}
        for field, text in fields.items():
            if (force and text) or normalize_store_text(cur_fields.get(field, "")) != normalize_store_text(text):
                changes.setdefault(lang, {})[field] = text
                before.setdefault(lang, {})[field] = cur_fields.get(field, "")
    if not changes:
        return []
    payload = {"itemid": item_id, "languages": changes}

    async def upload(t: Transport) -> None:
        await P.upload_store_localization(t, appid, payload)

    return [
        Op(
            "store_text",
            "upload",
            f"{sum(len(v) for v in changes.values())} text(s) in {len(changes)} language(s)",
            before,
            changes,
            upload,
        )
    ]
