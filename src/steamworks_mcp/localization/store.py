"""Translations of player-facing texts: ``localization/<lang>.yaml`` (flat ``field path: text``).

The host model translates: ``localization_pending`` gives it the texts with context and glossary terms,
``localization_set`` checks and stores them. A lock file (``localization/.lock.json``) remembers the hash of the
source text each translation was made from, so a changed source marks the translation stale. Translation state is
also tracked as ``localization.<lang>.<field>`` in state.json (drafts until the user approves them).

``localization/glossary.yaml`` (optional)::

    do_not_translate: [Pillow Fort Panic, Steam]
    terms:
      fort: {german: Festung, french: fort}
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML

from steamworks_mcp.languages import is_api_code
from steamworks_mcp.manifest.io import atomic_write
from steamworks_mcp.manifest.state import State
from steamworks_mcp.validate.store_text import check_store_text

SHORT_DESCRIPTION_MAX = 300
ACHIEVEMENT_NAME_MAX = 64
ACHIEVEMENT_DESCRIPTION_MAX = 160
LAUNCH_DESCRIPTION_MAX = 64
LOCK_FILE = ".lock.json"
TAG = re.compile(r"\[(/?)([a-z0-9*]+)(?:[= ][^\]]*)?\]", re.I)


@dataclass(frozen=True)
class Entry:
    key: str
    """Field path, e.g. ``store.about`` or ``achievements.ACH_WIN.name``."""
    text: str
    format: Literal["plain", "bbcode"]
    context: str
    max_length: int | None = None


def entries(values: dict[str, Any]) -> list[Entry]:
    """Every player-facing text in steamworks.yaml that needs translating."""
    name = (values.get("game") or {}).get("name") or "the game"
    store = values.get("store") or {}
    out: list[Entry] = []

    def push(e: Entry) -> None:
        if e.text and e.text.strip():
            out.append(e)

    push(
        Entry(
            "store.short_description",
            store.get("short_description") or "",
            "plain",
            f'Steam store short description of "{name}", next to the header capsule. '
            "Plain text, at most 300 characters.",
            SHORT_DESCRIPTION_MAX,
        )
    )
    push(
        Entry(
            "store.about",
            store.get("about") or "",
            "bbcode",
            f'"About This Game" on the Steam store page of "{name}". Keep every BBCode tag exactly as it is and in the '
            "same order; translate only the readable text, never image paths, and keep [GIF: …] placeholders "
            "as they are.",
        )
    )
    for a in values.get("achievements") or []:
        hidden = " It is a hidden achievement." if a.get("hidden") else ""
        push(
            Entry(
                f"achievements.{a['id']}.name",
                a.get("name") or "",
                "plain",
                f'Name of a Steam achievement in "{name}". A short title.{hidden}',
                ACHIEVEMENT_NAME_MAX,
            )
        )
        push(
            Entry(
                f"achievements.{a['id']}.description",
                a.get("description") or "",
                "plain",
                f'Description of the Steam achievement "{a.get("name") or a["id"]}": how to unlock it.',
                ACHIEVEMENT_DESCRIPTION_MAX,
            )
        )
    for app in ("main", "demo", "playtest"):
        profile = (values.get("apps") or {}).get(app) or {}
        for i, lo in enumerate((profile.get("installation") or {}).get("launch_options") or []):
            push(
                Entry(
                    f"apps.{app}.installation.launch_options.{i}.description",
                    lo.get("description") or "",
                    "plain",
                    "Label of a launch option in the Steam client's Play menu (e.g. 'Play in safe mode'). Short.",
                    LAUNCH_DESCRIPTION_MAX,
                )
            )
    return out


def text_hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def tag_signature(text: str) -> str:
    """Ordered BBCode tag names without attributes, e.g. ``h2,/h2,list,*,/list``."""
    return ",".join(f"{m.group(1)}{m.group(2).lower()}" for m in TAG.finditer(text))


class Localization:
    def __init__(self, root: Path) -> None:
        self.dir = root / "localization"

    def _file(self, lang: str) -> Path:
        if not is_api_code(lang):
            raise ValueError(f'"{lang}" is not a Steam API language code.')
        return self.dir / f"{lang}.yaml"

    def read(self, lang: str) -> dict[str, str]:
        p = self._file(lang)
        if not p.exists():
            return {}
        data = YAML(typ="safe", pure=True).load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"localization/{lang}.yaml must be a flat 'field: text' map.")
        return {str(k): "" if v is None else str(v) for k, v in data.items()}

    def write(self, lang: str, data: dict[str, str]) -> None:
        y = YAML()
        y.width = 4096
        y.allow_unicode = True
        from io import StringIO

        buf = StringIO()
        y.dump(data, buf)
        atomic_write(self._file(lang), buf.getvalue())

    def lock(self) -> dict[str, dict[str, str]]:
        p = self.dir / LOCK_FILE
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def write_lock(self, lock: dict[str, dict[str, str]]) -> None:
        atomic_write(self.dir / LOCK_FILE, json.dumps(lock, indent=2, sort_keys=True) + "\n")

    def glossary(self) -> dict[str, Any]:
        p = self.dir / "glossary.yaml"
        if not p.exists():
            return {"do_not_translate": [], "terms": {}}
        data = YAML(typ="safe", pure=True).load(p.read_text(encoding="utf-8")) or {}
        return {"do_not_translate": list(data.get("do_not_translate") or []), "terms": dict(data.get("terms") or {})}


@dataclass
class LanguageStatus:
    language: str
    total: int
    translated: int
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    """Translated, but the source text changed afterwards."""
    orphaned: list[str] = field(default_factory=list)
    """Keys in the language file that no longer exist in the source."""


def status(values: dict[str, Any], root: Path, languages: list[str] | None = None) -> list[LanguageStatus]:
    loc = Localization(root)
    items = entries(values)
    lock = loc.lock()
    out = []
    for lang in languages if languages is not None else list(values.get("target_languages") or []):
        data, hashes = loc.read(lang), lock.get(lang, {})
        missing = [e.key for e in items if not data.get(e.key, "").strip()]
        stale = [e.key for e in items if e.key not in missing and hashes.get(e.key) != text_hash(e.text)]
        known = {e.key for e in items}
        out.append(
            LanguageStatus(
                lang,
                len(items),
                len(items) - len(missing) - len(stale),
                missing,
                stale,
                [k for k in data if k not in known],
            )
        )
    return out


def _terms_in(text: str, glossary: dict[str, Any], lang: str) -> dict[str, Any]:
    lower = text.lower()
    keep = [t for t in glossary["do_not_translate"] if t.lower() in lower]
    terms = {
        t: tr[lang]
        for t, tr in glossary["terms"].items()
        if t.lower() in lower and isinstance(tr, dict) and tr.get(lang)
    }
    return {k: v for k, v in (("do_not_translate", keep), ("use_terms", terms)) if v}


def pending(values: dict[str, Any], root: Path, lang: str, limit: int = 30) -> list[dict[str, Any]]:
    st = status(values, root, [lang])[0]
    loc = Localization(root)
    data, glossary = loc.read(lang), loc.glossary()
    out = []
    for e in entries(values):
        if e.key not in st.missing and e.key not in st.stale:
            continue
        item: dict[str, Any] = {
            "key": e.key,
            "text": e.text,
            "format": e.format,
            "context": e.context,
            "reason": "missing" if e.key in st.missing else "stale",
        }
        if e.max_length:
            item["max_length"] = e.max_length
        if e.key in st.stale:
            item["previous_translation"] = data.get(e.key)
        if g := _terms_in(e.text, glossary, lang):
            item["glossary"] = g
        out.append(item)
        if len(out) >= limit:
            break
    return out


def check(entry: Entry, text: str, lang: str, glossary: dict[str, Any]) -> tuple[str | None, list[str]]:
    """(error, warnings) for one translation."""
    if not text.strip():
        return "The translation is empty.", []
    warnings: list[str] = []
    if entry.format == "bbcode" and tag_signature(entry.text) != tag_signature(text):
        return f"BBCode tags differ from the source. Expected, in order: {tag_signature(entry.text) or '(none)'}.", []
    length = len(text)
    if entry.key == "store.short_description" and length > SHORT_DESCRIPTION_MAX:
        return f"{length} characters; Steam's limit is {SHORT_DESCRIPTION_MAX} in every language.", []
    if entry.key in ("store.short_description", "store.about"):
        findings = check_store_text({lang: {entry.key: text}})
        if errors := [f for f in findings if f.severity == "error"]:
            return "; ".join(f"[{f.rule_id}] {f.message}" for f in errors), []
        warnings += [f"[{f.rule_id}] {f.message}" for f in findings]
    if entry.max_length and length > entry.max_length and entry.key != "store.short_description":
        warnings.append(f"{length} characters; Steam shows about {entry.max_length}.")
    for term in glossary["do_not_translate"]:
        if term.lower() in entry.text.lower() and term.lower() not in text.lower():
            warnings.append(f'"{term}" should stay untranslated.')
    for term, tr in glossary["terms"].items():
        want = tr.get(lang) if isinstance(tr, dict) else None
        if want and term.lower() in entry.text.lower() and want.lower() not in text.lower():
            warnings.append(f'Glossary: "{term}" is "{want}" in {lang}.')
    return None, warnings


def set_translations(
    values: dict[str, Any], root: Path, state: State, lang: str, translations: dict[str, str], source: str = "generated"
) -> dict[str, Any]:
    if lang == values.get("source_language"):
        raise ValueError(f'"{lang}" is the source language: edit steamworks.yaml with set_field instead.')
    if lang not in (values.get("target_languages") or []):
        raise ValueError(f'"{lang}" is not in target_languages; add it with set_field first.')
    loc = Localization(root)
    known = {e.key: e for e in entries(values)}
    data, lock, glossary = loc.read(lang), loc.lock(), loc.glossary()
    hashes = lock.setdefault(lang, {})
    saved: list[str] = []
    rejected: dict[str, str] = {}
    warnings: dict[str, list[str]] = {}
    for key, text in translations.items():
        entry = known.get(key)
        if entry is None:
            rejected[key] = "Unknown key; see localization_pending for the keys to translate."
            continue
        error, warns = check(entry, text, lang, glossary)
        if error:
            rejected[key] = error
            continue
        if warns:
            warnings[key] = warns
        data[key] = text
        hashes[key] = text_hash(entry.text)
        state.record_value(f"localization.{lang}.{key}", text, "user" if source == "user" else "generated")
        saved.append(key)
    if saved:
        ordered = {k: data[k] for k in known if k in data}
        ordered.update({k: v for k, v in data.items() if k not in ordered})
        loc.write(lang, ordered)
        loc.write_lock(lock)
    return {"saved": saved, "rejected": rejected, "warnings": warnings}
