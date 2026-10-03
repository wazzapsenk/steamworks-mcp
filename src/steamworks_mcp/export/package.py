"""``export_package(gate)``: everything needed to finish a gate by hand, in ``.steam-mcp/exports/gate_<n>/``.

Each package has correctly named files to upload or import and a ``CHECKLIST.md`` that says, for every item that
is not done yet, which Steamworks page and field it goes into and what to paste. Items already done are ticked.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import shutil
from pathlib import Path
from typing import Any

from steamworks_mcp.capabilities import capabilities
from steamworks_mcp.export.vdf import build_scripts, steamcmd_command
from steamworks_mcp.gates.engine import RuleResult, evaluate_gates, gate_files
from steamworks_mcp.languages import find_language
from steamworks_mcp.localization.store import Localization
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import ProjectFiles, atomic_write, load_drafts
from steamworks_mcp.manifest.state import State
from steamworks_mcp.media.images import prepare_achievement_icons, prepare_store_images

STORE_FIELDS = {"store.short_description": "app[content][short_description]", "store.about": "app[content][about]"}
PARTNER = "https://partner.steamgames.com"
DONE = ("pass", "done")


def _lang_name(code: str) -> str:
    lang = find_language(code)
    return lang.name if lang else code


def _yes(v: Any) -> str:
    return "yes" if v is True else "no" if v is False else "?"


def store_texts(values: dict[str, Any], root: Path) -> dict[str, dict[str, str]]:
    """``{language: {field: text}}`` for the source language and every translated target language."""
    source = str(values.get("source_language") or "english")
    out = {source: {f: fp.get(values, f) for f in STORE_FIELDS if fp.get(values, f)}}
    loc = Localization(root)
    for lang in values.get("target_languages") or []:
        data = loc.read(lang)
        fields = {f: data[f] for f in STORE_FIELDS if data.get(f)}
        if fields:
            out[lang] = fields
    return {k: v for k, v in out.items() if v}


def store_localization_json(values: dict[str, Any], root: Path, item_id: str = "") -> dict[str, Any]:
    """The JSON the store page's Localization tab imports.

    Only languages and fields that have text are included, so importing never blanks anything in Steamworks.
    """
    return {
        "itemid": item_id,
        "languages": {
            lang: {STORE_FIELDS[f]: t for f, t in fields.items()} for lang, fields in store_texts(values, root).items()
        },
    }


def achievements_csv(values: dict[str, Any], root: Path) -> str:
    source = str(values.get("source_language") or "english")
    langs = [source, *(values.get("target_languages") or [])]
    loc = Localization(root)
    translations = {lang: loc.read(lang) for lang in langs[1:]}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["api_name", "hidden", "field", *langs])
    for a in values.get("achievements") or []:
        for field in ("name", "description"):
            row = [a.get(field) or ""] + [
                translations[lang].get(f"achievements.{a['id']}.{field}", "") for lang in langs[1:]
            ]
            w.writerow([a["id"], "yes" if a.get("hidden") else "no", field, *row])
    return "﻿" + buf.getvalue()


# ---------------------------------------------------------------------------------------------------- details per rule


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c if c not in (None, "") else "—") for c in r) + " |" for r in rows]
    return out


def _details(rule_id: str, values: dict[str, Any], files: dict[str, str]) -> list[str]:
    """Extra lines (what to paste or upload) for some rules."""

    def g(path: str) -> Any:
        return fp.get(values, path)

    if rule_id in ("short_description", "about_this_game_description"):
        key = "store.short_description" if rule_id == "short_description" else "store.about"
        return [
            f"Paste from `store/<language>/{key.split('.')[1]}.txt`, or import `store/store_localization.json` "
            "on the Localization tab (all languages at once)."
        ]
    if rule_id == "steam_tags":
        return [", ".join(f"`{t}`" for t in g("store.tags") or []) or "No tags yet."]
    if rule_id == "supported_languages_declared":
        rows = [
            [_lang_name(c), _yes(s.get("interface")), _yes(s.get("full_audio")), _yes(s.get("subtitles"))]
            for c, s in (g("store.supported_languages") or {}).items()
        ]
        return _table(["Language", "Interface", "Full audio", "Subtitles"], rows)
    if rule_id == "supported_os_store_page":
        return [", ".join(g("store.platforms") or []) or "No platforms yet."]
    if rule_id == "system_requirements":
        lines: list[str] = []
        for os_name in g("store.platforms") or []:
            for tier in ("minimum", "recommended"):
                req = g(f"store.system_requirements.{os_name}.{tier}")
                if req and any(req.values()):
                    lines += [
                        f"**{os_name} — {tier}**",
                        *[f"- {k.replace('_', ' ')}: {v}" for k, v in req.items() if v],
                        "",
                    ]
        return lines or ["No requirements yet: generate(section='requirements')."]
    if rule_id == "release_date_set":
        return [
            f"Exact date (hidden): `{g('release.planned_date') or '—'}`; "
            f"shown as: `{g('release.display_date') or '—'}`."
        ]
    if rule_id == "ai_generated_content_disclosure":
        ai = g("content.ai") or {}
        return [
            f"Uses AI: {_yes(ai.get('uses_ai'))}",
            *[
                f"- {k.replace('_', ' ')}: {ai[k]}"
                for k in ("pre_generated", "live_generated", "guardrails")
                if ai.get(k)
            ],
        ]
    if rule_id == "early_access_questionnaire":
        answers = g("release.early_access_answers") or {}
        return [f"- {k.replace('_', ' ')}: {v}" for k, v in answers.items() if v]
    if rule_id == "launch_option_defined":
        options: list[list[Any]] = [
            [i, o["executable"], o.get("arguments"), o.get("os"), o.get("arch"), o.get("type"), o.get("description")]
            for i, o in enumerate(g("apps.main.installation.launch_options") or [])
        ]
        return _table(["#", "Executable", "Arguments", "OS", "Arch", "Type", "Description"], options)
    if rule_id == "install_folder_set":
        return [f"Install folder: `{g('apps.main.installation.install_folder') or '—'}`"]
    if rule_id == "depots_configured":
        rows = [
            [d.get("name"), d.get("depot_id"), d.get("os"), d.get("arch"), d.get("content_root")]
            for d in g("apps.main.builds.depots") or []
        ]
        return _table(["Name", "Depot id", "OS", "Arch", "Build folder"], rows)
    if rule_id == "build_uploaded" and "steam/README.md" in files:
        return ["Scripts and the upload command: `steam/README.md`."]
    if rule_id in ("minimum_price", "proposed_pricing_submitted", "pricing_approved"):
        price = g("pricing.base_price_usd")
        return [
            f"Base price: {'free to play' if g('pricing.free_to_play') else (f'${price} USD' if price else '—')}; "
            f"launch discount: {g('pricing.launch_discount_percent') or 'none'}%."
        ]
    return []


def _asset_file(rule: RuleResult, files: dict[str, str]) -> list[str]:
    if rule.rule.check.kind != "asset":
        return []
    asset = getattr(rule.rule.check, "asset", "")
    found = [f for f in files if f.startswith("images/") and Path(f).stem == asset]
    return [
        f"Upload `{found[0]}`."
        if found
        else "No image yet: set assets.key_art / assets.logo (or an override) and export again."
    ]


# ---------------------------------------------------------------------------------------------------- checklist


def render_checklist(
    gate: int, values: dict[str, Any], results: list[RuleResult], files: dict[str, str], extra: list[str]
) -> str:
    g = next(x for x in gate_files() if x.gate == gate)
    appid = fp.get(values, "apps.main.appid")
    name = fp.get(values, "game.name") or "Your game"
    L = [
        f"# Gate {gate}: {g.title} — {name}{f' (app {appid})' if appid else ''}",
        "",
        f"{g.summary}",
        "",
        f"Generated by steamworks-mcp on {dt.date.today().isoformat()} from steamworks.yaml. Ticked items are done.",
        "When you finish a step, tell your assistant so it can record it (mark_applied).",
        "Nothing here is published for you: publishing in Steamworks is always your own action.",
        "",
    ]
    order = {"required": 0, "recommended": 1, "optional": 2}
    for severity in ("required", "recommended", "optional"):
        rs = [r for r in results if r.rule.severity == severity and r.status not in ("not_applicable", "info")]
        if not rs:
            continue
        L += [f"## {severity.capitalize()}", ""]
        for r in sorted(rs, key=lambda r: (r.status in DONE, order[r.rule.severity])):
            tick = "x" if r.status in DONE else " "
            where = f" — *{r.rule.where}*" if r.rule.where else ""
            L.append(f"- [{tick}] **{r.rule.title}**{where} `({r.mode})`")
            if r.status not in DONE:
                L.append(f"  {r.rule.description}")
                if r.message and not (r.rule.check.kind == "asset" and r.status == "todo"):
                    L.append(f"  Now: {r.message}")
                if r.missing:
                    L.append(f"  Missing in steamworks.yaml: {', '.join(f'`{m}`' for m in r.missing)}")
                if r.needs_approval:
                    L.append(f"  Waiting for your approval: {', '.join(f'`{m}`' for m in r.needs_approval)}")
                L += [f"  {line}" for line in _details(r.rule.id, values, files) + _asset_file(r, files)]
                if r.rule.check.kind == "checklist":
                    L.append(f"  Confirm when done: `mark_applied(['checklist.{r.rule.id}'])`")
            if r.rule.source_doc:
                L.append(f"  Source: <{r.rule.source_doc}>{' (unverified)' if r.rule.unverified else ''}")
        L.append("")
    if extra:
        L += extra
    info = [r for r in results if r.status == "info"]
    if info:
        L += (
            ["## Good to know", ""]
            + [f"- {r.rule.description}" + (f" <{r.rule.source_doc}>" if r.rule.source_doc else "") for r in info]
            + [""]
        )
    return "\n".join(L)


def _cloud_section(values: dict[str, Any]) -> list[str]:
    c = fp.get(values, "apps.main.cloud") or {}
    if not (c.get("enabled") or c.get("auto_cloud")):
        return []
    appid = fp.get(values, "apps.main.appid")
    L = [f"## Steam Cloud ({f'[settings]({PARTNER}/apps/cloud/{appid})' if appid else 'App Admin > Steam Cloud'})", ""]
    if c.get("byte_quota") is not None:
        L.append(f"- Byte quota per user: **{c['byte_quota']}** ({c['byte_quota'] / 1_000_000:.1f} MB)")
    if c.get("file_quota") is not None:
        L.append(f"- Number of files per user: **{c['file_quota']}**")
    if c.get("auto_cloud"):
        L += ["", "Auto-Cloud root paths (save the quotas first; the Auto-Cloud section appears after that):", ""]
        L += _table(
            ["Root", "Subdirectory", "Pattern", "OS", "Recursive"],
            [
                [r["root"], r.get("subdirectory") or ".", r["pattern"], r.get("os"), _yes(r.get("recursive"))]
                for r in c["auto_cloud"]
            ],
        )
    if c.get("overrides"):
        L += ["", "Root overrides (the original root path must be set to all OSes):", ""]
        L += _table(
            ["Original root", "OS", "Use instead", "Add/replace path", "Replace"],
            [
                [o["root"], o["os"], o["use_instead"], o.get("add_path"), _yes(o.get("replace_path"))]
                for o in c["overrides"]
            ],
        )
    return [*L, ""]


def _achievements_section(values: dict[str, Any], files: dict[str, str]) -> list[str]:
    items = values.get("achievements") or []
    if not items:
        return []
    appid = fp.get(values, "apps.main.appid")
    link = (
        f"[Stats & Achievements]({PARTNER}/apps/achievements/{appid})" if appid else "App Admin > Stats & Achievements"
    )
    rows = [
        [
            f"`{a['id']}`",
            a.get("name"),
            a.get("description"),
            _yes(a.get("hidden")),
            "yes" if f"achievements/{a['id']}.jpg" in files else "—",
        ]
        for a in items
    ]
    return [
        f"## Achievements ({link})",
        "",
        "Icons: `achievements/<API name>.jpg` (achieved) and `<API name>_locked.jpg`.",
        "Every language: `achievements/achievements.csv`.",
        "",
        *_table(["API name", "Name", "Description", "Hidden", "Icons"], rows),
        "",
    ]


# ---------------------------------------------------------------------------------------------------- package


def export_package(
    values: dict[str, Any], state: State, files_: ProjectFiles, gate: int, *, browser: bool = False
) -> dict[str, Any]:
    if gate not in range(4):
        raise ValueError("gate must be 0, 1, 2 or 3")
    root = files_.root
    out = files_.export_dir(gate)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written: dict[str, str] = {}

    def write(rel: str, text: str) -> None:
        atomic_write(out / rel, text)
        written[rel] = "written"

    def copy(src: Path, rel: str) -> None:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out / rel)
        written[rel] = "copied"

    extra: list[str] = []
    notes: list[str] = []
    if gate == 0:
        p = capabilities().permissions
        extra = [
            "## Steamworks users for automation",
            "",
            p.notes,
            "",
            f"- Browser-mode user: {', '.join(p.browser_mode)}",
            f"- steamcmd builder account: {', '.join(p.builder_account)}",
            f"- Never needed by this tool: {', '.join(p.never_needed)}",
            "",
        ]
    if gate == 1:
        texts = store_texts(values, root)
        for lang, fields in texts.items():
            for f, text in fields.items():
                write(f"store/{lang}/{f.split('.')[1]}.txt", text + "\n")
        if texts:
            write(
                "store/store_localization.json",
                json.dumps(store_localization_json(values, root), indent=2, ensure_ascii=False) + "\n",
            )
            notes.append(
                "store_localization.json has an empty itemid: Steam's own export fills it in; "
                "importing works without it (unverified)."
            )
        _images(values, root, out, written, groups=("store",))
        for d in load_drafts(files_, "store.about"):
            shotlist = files_.draft_dir("store.about") / f"{d.id}.gif_shotlist.md"
            if d.status == "chosen" and shotlist.exists():
                copy(shotlist, "store/gif_shotlist.md")
    if gate == 2:
        scripts = build_scripts(values, root, out / "steam")
        if isinstance(scripts, dict):
            for name, text in scripts.items():
                write(f"steam/{name}", text)
            appid = fp.get(values, "apps.main.appid")
            write(
                "steam/README.md",
                "# Uploading the build\n\n"
                "Run from this folder with the builder account (never stored by this tool):\n\n"
                f"```\n{steamcmd_command(int(appid))}\n```\n\n"
                "Then set the build live on the default branch by hand in Steamworks > SteamPipe > Builds "
                "(scripts cannot do that), and publish your Steamworks changes yourself.\n",
            )
        else:
            notes.append(f"No SteamPipe scripts yet: {scripts}.")
        _images(values, root, out, written, groups=("library", "icon"))
        icons = prepare_achievement_icons(values, root, out / "achievements")
        for r in icons:
            if r.file:
                written[f"achievements/{r.id}.jpg"] = "generated"
                written[f"achievements/{r.id}_locked.jpg"] = "generated"
        if values.get("achievements"):
            write("achievements/achievements.csv", achievements_csv(values, root))
        extra = _cloud_section(values) + _achievements_section(values, written)
    results = evaluate_gates(values, state, root, [gate], browser=browser)
    write("CHECKLIST.md", render_checklist(gate, values, results, written, extra))
    summary = {
        s: sum(1 for r in results if r.status == s)
        for s in ("pass", "done", "fail", "review", "todo", "warn", "unknown")
    }
    return {"folder": out.relative_to(root).as_posix(), "files": sorted(written), "status": summary, "notes": notes}


def _images(values: dict[str, Any], root: Path, out: Path, written: dict[str, str], groups: tuple[str, ...]) -> None:
    from steamworks_mcp.gates.engine import asset_specs

    ids = [a.id for a in asset_specs().values() if a.group in groups]
    for r in prepare_store_images(values, root, out / "images", ids):
        if r.file:
            written[f"images/{Path(r.file).name}"] = "generated"
            if r.id == "shortcut_icon":
                written["images/shortcut_icon.ico"] = "generated"
    if (out / "images" / "preview.html").exists():
        written["images/preview.html"] = "generated"
