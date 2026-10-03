"""Deterministic Valve store rules (``data/store_rules.yaml``, ``check: deterministic``) applied to store text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from steamworks_mcp.data import load_yaml
from steamworks_mcp.validate.rules_data import StoreRule, StoreRulesFile

TARGET_FIELDS = {"short_description": "store.short_description", "about_this_game": "store.about"}

BBCODE_TAG = re.compile(r"\[/?([a-z0-9*]+)(?:=[^\]]*)?\]", re.I)
HTML_TAG = re.compile(r"</?[a-z][a-z0-9]*(?:\s[^>]*)?>", re.I)
URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|io|gg|co|tv|me|app|dev|xyz|games?)\b(?:/\S*)?",
    re.I,
)


@dataclass(frozen=True)
class TextFinding:
    rule_id: str
    severity: str
    field: str
    language: str
    message: str
    excerpt: str = ""


@cache
def store_rules() -> tuple[StoreRule, ...]:
    return tuple(StoreRulesFile.model_validate(load_yaml("store_rules.yaml")).rules)


def deterministic_text_rules(ids: list[str] | None = None) -> list[StoreRule]:
    """Deterministic rules that apply to the short description or About This Game (optionally only ``ids``)."""
    return [
        r
        for r in store_rules()
        if r.check == "deterministic"
        and any(t in TARGET_FIELDS for t in r.applies_to)
        and r.kind
        not in ("media_max_bytes", "media_total_max_bytes", "media_formats", "media_max_duration", "media_max_width")
        and (not ids or r.id in ids)
    ]


def _excerpt(text: str, start: int, end: int) -> str:
    return text[max(0, start - 20) : end + 20].replace("\n", " ")


def check_text(rule: StoreRule, field: str, language: str, text: str) -> list[TextFinding]:
    out: list[TextFinding] = []

    def add(message: str, excerpt: str = "") -> None:
        out.append(TextFinding(rule.id, rule.severity, field, language, message, excerpt))

    p: dict[str, Any] = rule.params
    if rule.kind == "max_length" and len(text) > int(p["max"]):
        add(f"{len(text)} characters; the limit is {p['max']}.")
    elif rule.kind == "plain_text":
        m = BBCODE_TAG.search(text) or HTML_TAG.search(text)
        if m:
            add("Formatting tags are not allowed here (plain text only).", _excerpt(text, m.start(), m.end()))
        elif "\n" in text.strip():
            add("Line breaks are not allowed here (plain text only).")
    elif rule.kind == "regex_forbidden":
        for pattern in p.get("patterns", []):
            if m := re.search(pattern, text, re.I):
                add(f'"{m.group(0)}" is not allowed: {rule.rule}', _excerpt(text, m.start(), m.end()))
                break
    elif rule.kind == "no_urls":
        m = URL.search(text) or re.search(r"\[url[=\]]", text, re.I)
        if m:
            add(
                "Links and web addresses are not allowed in store text; Steam hides them.",
                _excerpt(text, m.start(), m.end()),
            )
    elif rule.kind == "forbidden_bbcode":
        tags = {t.lower() for t in p.get("tags", [])}
        if found := sorted({m.group(1).lower() for m in BBCODE_TAG.finditer(text)} & tags):
            add(f"BBCode tag(s) not allowed: {', '.join(f'[{t}]' for t in found)}.")
    elif rule.kind == "allowed_bbcode":
        allowed = {t.lower() for t in p.get("tags", [])}
        if unknown := sorted({m.group(1).lower() for m in BBCODE_TAG.finditer(text)} - allowed):
            add(f"Unknown BBCode tag(s): {', '.join(f'[{t}]' for t in unknown)}.")
    return out


def check_store_text(
    texts: dict[str, dict[str, str]], ids: list[str] | None = None, *, english_fallback: bool = True
) -> list[TextFinding]:
    """``texts``: ``{language: {field path: text}}`` (e.g. ``{"english": {"store.about": "..."}}``).

    ``english_fallback`` checks that English has every text; only meaningful when ``texts`` holds every language.
    """
    out: list[TextFinding] = []
    rules = deterministic_text_rules(ids)
    for language, fields in texts.items():
        for rule in rules:
            if rule.kind == "english_present":
                continue
            for target in rule.applies_to:
                field = TARGET_FIELDS.get(target)
                if field and fields.get(field):
                    out += check_text(rule, field, language, fields[field])
    if english_fallback and any(r.kind == "english_present" for r in rules):
        english = texts.get("english", {})
        for field in TARGET_FIELDS.values():
            has_any = any(lang_fields.get(field) for lang_fields in texts.values())
            if has_any and not english.get(field):
                rule = next(r for r in rules if r.kind == "english_present")
                out.append(
                    TextFinding(
                        rule.id, rule.severity, field, "english", "English is Steam's fallback and must have this text."
                    )
                )
    return out
