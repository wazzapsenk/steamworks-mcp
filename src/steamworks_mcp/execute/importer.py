"""Import: what Steamworks already has goes into the empty fields of steamworks.yaml, with the status "applied".

The inverse of sync.py's ``desired_*`` functions. Nothing is written to Steamworks. Only empty fields are filled; a
field that already holds a different value is reported as a conflict and left alone, so the import never overwrites
what the user wrote.
"""

from __future__ import annotations

from typing import Any

from steamworks_mcp.execute import sync
from steamworks_mcp.execute.api import board_settings
from steamworks_mcp.fields import set_fields
from steamworks_mcp.localization.store import set_translations
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.state import is_empty, value_hash
from steamworks_mcp.project import Project

SECTIONS = ("store_text", "achievements", "cloud", "installation", "leaderboards", "checklist")
BACK_CLOUD_OS = {v: k for k, v in sync.CLOUD_OS.items()}
BACK_LAUNCH_OS = {v: k for k, v in sync.LAUNCH_OS.items()}
BACK_ARCH = {v: k for k, v in sync.ARCH.items()}
STORE_PATHS = {v: k for k, v in sync.STORE_FIELDS.items()}
PREREQUISITES_NOTE = (
    "The app exists in Steamworks, and Valve lets a partner pay the Steam Direct fee for an app only after the "
    "company, NDA, bank, tax and identity steps. Gate 0 values are the user's to confirm: ask, then set_field."
)

Found = dict[str, Any]
"""``{field path: value}`` read from Steamworks."""
Translations = dict[str, dict[str, str]]
"""``{language: {field path: text}}``."""


# ---------------------------------------------------------------------------------------------------- Steam -> values


def store_text(loc: dict[str, Any], source: str) -> tuple[Found, Translations]:
    found: Found = {}
    translations: Translations = {}
    for lang, fields in (loc.get("languages") or {}).items():
        for key, path in STORE_PATHS.items():
            text = fields.get(key) if isinstance(fields, dict) else None
            if not text or not str(text).strip():
                continue
            if lang == source:
                found[path] = text
            else:
                translations.setdefault(lang, {})[path] = text
    return found, translations


def achievements(current: dict[str, Any], source: str) -> tuple[Found, Translations, list[str]]:
    found: Found = {}
    translations: Translations = {}
    notes = []
    for a in current["achievements"]:
        api_name = a["api_name"]
        names, descs = sync._as_map(a.get("display_name")), sync._as_map(a.get("description"))
        item: dict[str, Any] = {"hidden": sync._hidden(a.get("hidden"))}
        if names.get(source):
            item["name"] = names[source]
        if descs.get(source):
            item["description"] = descs[source]
        found[f"achievements.{api_name}"] = item
        for key, texts in (("name", names), ("description", descs)):
            for lang, text in texts.items():
                if lang != source:
                    translations.setdefault(lang, {})[f"achievements.{api_name}.{key}"] = text
        if a.get("progress"):
            notes.append(f"{api_name} has a progress bar in Steamworks; its stat link is not imported.")
    return found, translations, notes


def cloud(current: dict[str, Any], app: str) -> Found:
    on = bool(current["byte_quota"] or current["file_quota"])
    base = f"apps.{app}.cloud"
    found: Found = {f"{base}.enabled": on}
    if on:
        found |= {
            f"{base}.byte_quota": current["byte_quota"],
            f"{base}.file_quota": current["file_quota"],
            f"{base}.developers_only": current["developers_only"],
            f"{base}.sync_on_suspend": current["sync_on_suspend"],
        }
    if current["shared_appid"]:
        found[f"{base}.shared_appid"] = current["shared_appid"]
    roots = [
        {
            "root": r["root"],
            "subdirectory": r["path"],
            "pattern": r["pattern"],
            "os": BACK_CLOUD_OS.get(r["os"], "all"),
            "recursive": r["recursive"],
        }
        for r in current["roots"]
    ]
    overrides = [
        {
            "root": o["root"],
            "os": BACK_CLOUD_OS.get(o["os"], o["os"]),
            "use_instead": o["use_instead"],
            "add_path": o["add_path"],
            "replace_path": o["replace_path"],
        }
        for o in current["overrides"]
    ]
    if roots:
        found[f"{base}.auto_cloud"] = roots
    if overrides:
        found[f"{base}.overrides"] = overrides
    return found


def installation(current: dict[str, Any], app: str, source: str) -> tuple[Found, Translations, list[str]]:
    base = f"apps.{app}.installation"
    found: Found = {}
    translations: Translations = {}
    notes = []
    if current["install_folder"]:
        found[f"{base}.install_folder"] = current["install_folder"]
    options = current["launch_options"]
    if [o["index"] for o in options] != list(range(len(options))):
        notes.append("Launch options are not numbered 0, 1, 2…: not imported (steamworks.yaml keeps them in order).")
        return found, translations, notes
    rows = []
    for o in options:
        row = {
            "executable": o["executable"],
            "arguments": o["arguments"],
            "working_dir": o["working_dir"],
            "type": o["type"] or "default",
            "os": BACK_LAUNCH_OS.get(o["os"], "all"),
            "arch": BACK_ARCH.get(o["arch"], "all"),
            "beta_key": o["beta_key"],
            "owns_dlc": o["owns_dlc"],
        }
        descriptions = o.get("descriptions") or {}
        if descriptions.get(source):
            row["description"] = descriptions[source]
        for lang, text in descriptions.items():
            if lang != source and text:
                translations.setdefault(lang, {})[f"{base}.launch_options.{o['index']}.description"] = text
        rows.append(row)
    if rows:
        found[f"{base}.launch_options"] = rows
    return found, translations, notes


def leaderboards(boards: list[dict[str, Any]]) -> tuple[Found, list[str]]:
    found: Found = {}
    notes = []
    for b in boards:
        settings = board_settings(b)
        if settings["display_type"] == "unset":
            notes.append(f"Leaderboard {b.get('name')} has no display type in Steam; not imported.")
            continue
        found[f"leaderboards.{b['name']}"] = settings
    return found, notes


def checklist(items: list[dict[str, Any]], rules: dict[str, str]) -> list[str]:
    """``checklist.<rule id>`` for every complete Steamworks checklist item that has a matching manual gate rule."""
    out = []
    for item in items:
        rule = rules.get(f"{item['checklist']} / {item['item']}")
        if rule and item.get("status") == "complete":
            out.append(f"checklist.{rule}")
    return sorted(set(out))


def likely_prerequisites(values: dict[str, Any]) -> Found:
    """Gate 0 answers that an existing app implies; returned as suggestions, never written."""
    keys = ("partner_account", "steam_direct_fee_paid", "tax_interview_done", "bank_info_done", "identity_verified")
    return {f"prerequisites.{k}": True for k in keys if is_empty(fp.get(values, f"prerequisites.{k}"))}


# ---------------------------------------------------------------------------------------------------- merge


def _differs(current: Any, steam: Any) -> bool:
    """Whether the file's value disagrees with Steam's; for an object only the keys Steam reports count."""
    if isinstance(current, dict) and isinstance(steam, dict):
        return any(current.get(k) != v for k, v in steam.items())
    return bool(current != steam)


def merge(
    project: Project,
    found: Found,
    translations: Translations,
    checklist_done: list[str],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Fill the empty fields with what Steamworks has; never overwrite. With ``dry_run`` nothing is saved."""
    values = project.values()
    fill: Found = {}
    conflicts = []
    same = []
    for path, steam in found.items():
        current = fp.get(values, path)
        if is_empty(current):
            fill[path] = steam
        elif _differs(current, steam):
            conflicts.append({"field": path, "steamworks_yaml": current, "steamworks": steam})
        else:
            same.append(path)
    targets = list(values.get("target_languages") or [])
    source = str(values.get("source_language") or "english")
    new_languages = sorted(lang for lang in translations if lang not in targets and lang != source)
    if new_languages and targets:
        skipped_languages, new_languages = new_languages, []
    else:
        skipped_languages = []
    out: dict[str, Any] = {
        "fill": sorted(fill),
        "conflicts": conflicts,
        "already_matching": sorted(same),
        "translations": {lang: len(t) for lang, t in sorted(translations.items()) if lang not in skipped_languages},
        "checklist_done": checklist_done,
    }
    if new_languages:
        out["target_languages_added"] = new_languages
    if skipped_languages:
        out["languages_not_imported"] = {
            "languages": skipped_languages,
            "why": "Steam has texts in them, but target_languages lists other languages; add them to import these.",
        }
    if dry_run:
        return out
    saved: dict[str, Any] = {}
    if new_languages:
        fill["target_languages"] = new_languages
    if fill:
        saved = set_fields(project, fill, "steamworks", notes="Read from Steamworks by import_from_steamworks.")
    values = project.values()
    for path in same:  # already in the file and on Steam: an approved value is now confirmed applied
        fs = project.state.fields.get(path)
        if fs is not None and fs.status == "approved" and fs.value_hash == value_hash(fp.get(values, path)):
            project.state.mark_applied(path, fp.get(values, path))
    translated: dict[str, list[str]] = {}
    rejected: dict[str, dict[str, str]] = {}
    disputed = [c["field"] for c in conflicts]  # a translation of a text the file has differently would be wrong
    for lang, texts in translations.items():
        if lang in skipped_languages or lang == source:
            continue
        texts = {k: v for k, v in texts.items() if not any(k == p or k.startswith(p + ".") for p in disputed)}
        if not texts:
            continue
        res = set_translations(values, project.files.root, project.state, lang, texts, source="user")
        for key in res["saved"]:
            path = f"localization.{lang}.{key}"
            project.state.mark_applied(path, texts[key])
            project.state.fields[path].source = "steamworks"
        translated[lang] = res["saved"]
        if res["rejected"]:
            rejected[lang] = res["rejected"]
    for path in checklist_done:
        if project.state.get(path).status != "applied":
            project.state.confirm_checklist(path, notes="Complete on the Steamworks landing page (import).")
            project.state.fields[path].source = "steamworks"
    project.save()
    out["saved"] = saved.get("set", [])
    if saved.get("not_saved"):
        out["not_saved"] = saved["not_saved"]
    out["translations"] = {lang: len(keys) for lang, keys in translated.items()}
    if rejected:
        out["translations_rejected"] = rejected
    return out
