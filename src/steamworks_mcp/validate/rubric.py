"""Store text rubric: layer 2 (``style_guides/_base_rubric.yaml``) with layer-3 genre overrides from the matching
style guide's front matter. Deterministic rules are checked here; llm_judged rules become questions for the host
model, whose answers are merged into the report on a second call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from steamworks_mcp.data import load_yaml
from steamworks_mcp.manifest.drafts import RubricResult
from steamworks_mcp.references.analyze import PLAYER_COUNT
from steamworks_mcp.references.anticopy import words
from steamworks_mcp.style_guides import StyleGuide

Section = Literal["short", "long"]
Outcome = Literal["pass", "fail", "warn", "not_applicable"]
FIELD = {"short": "store.short_description", "long": "store.about"}

BB_TAG = re.compile(r"\[/?[a-z0-9*]+(?:=[^\]]*)?\]", re.I)
GIF = re.compile(r"\[GIF:[^\]]*\]", re.I)
MEDIA = re.compile(r"\[img[^\]]*\]|\[GIF:[^\]]*\]|\[video[^\]]*\]", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s")
HEADER = re.compile(r"\[h[1-3]\][^\[]+\[/h[1-3]\]|^\s*\[b\][^\[\n]+\[/b\]\s*$", re.I | re.M)
ACRONYM = re.compile(r"\b(?=[A-Za-z0-9]*[A-Z][A-Za-z0-9]*[A-Z])[A-Z0-9][A-Za-z0-9]{1,5}\b")
"""Short tokens with at least two capitals: DPS, TTK, PvPvE."""


class RubricRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    applies_to: Literal["short", "long", "both"]
    check: Literal["deterministic", "llm_judged"]
    kind: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["warning", "info"] = "warning"
    stage: Literal["any", "final"] = "any"
    question: str | None = None
    rationale: str
    fix: str


@cache
def base_rules() -> tuple[RubricRule, ...]:
    return tuple(RubricRule.model_validate(r) for r in load_yaml("style_guides/_base_rubric.yaml")["rules"])


GUIDE_OVERRIDES: dict[tuple[str, str], tuple[str, str | None]] = {
    ("long_description", "words"): ("long_length_range", None),
    ("long_description", "max_paragraph_words"): ("long_paragraph_length", "max"),
    ("long_description", "media"): ("long_media_count", None),
    ("long_description", "first_media_within_words"): ("long_first_media_early", "max"),
    ("long_description", "feature_list_items"): ("long_feature_list", None),
}


def rules_for(guide: StyleGuide | None) -> list[RubricRule]:
    """Base rules with the genre guide's thresholds applied (layer 3 wins)."""
    rules = {r.id: r.model_copy(deep=True) for r in base_rules()}
    if guide is not None:
        meta = guide.meta.model_dump()
        if (meta.get("long_description") or {}).get("headings") == "optional":
            rules = {k: r for k, r in rules.items() if r.kind != "headers_min"}
        for (section, key), (rule_id, param) in GUIDE_OVERRIDES.items():
            value = (meta.get(section) or {}).get(key)
            if value is None or rule_id not in rules:
                continue
            if param is None and isinstance(value, dict):
                rules[rule_id].params.update({k: v for k, v in value.items() if k in ("min", "max")})
            elif param is not None:
                rules[rule_id].params[param] = value
    return list(rules.values())


# ---------------------------------------------------------------------------------------------------- helpers


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", BB_TAG.sub(" ", GIF.sub(" ", text))).strip()


def first_sentence(text: str) -> str:
    return SENTENCE_END.split(plain(text), maxsplit=1)[0]


def paragraphs(text: str) -> list[str]:
    no_lists = re.sub(r"\[list\].*?\[/list\]|\[olist\].*?\[/olist\]", "\n\n", text, flags=re.S | re.I)
    no_lists = re.sub(r"\[/?h[1-3]\][^\[]*\[/h[1-3]\]", "\n\n", no_lists, flags=re.I)
    parts = re.split(r"\[/?p\]|\n\s*\n|\[GIF:[^\]]*\]|\[img[^\]]*\]", no_lists, flags=re.I)
    return [plain(p) for p in parts if plain(p)]


def list_items(text: str) -> list[int]:
    return [
        len(re.findall(r"\[\*\]", m))
        for m in re.findall(r"\[(?:o)?list\](.*?)\[/(?:o)?list\]", text, flags=re.S | re.I)
    ]


@dataclass
class Context:
    values: dict[str, Any]
    root: Path | None = None
    """Project folder, for the checks that read translations; None skips them."""

    def get(self, *path: str) -> Any:
        cur: Any = self.values
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    @property
    def multiplayer(self) -> bool:
        players = self.get("game", "players") or {}
        return bool(
            (players.get("max") or 1) > 1
            or any(players.get(k) for k in ("online_coop", "local_coop", "online_pvp", "local_pvp"))
        )


def _genre_words(ctx: Context) -> set[str]:
    out: set[str] = set()
    for g in (
        (ctx.get("game", "genres") or []) + (ctx.get("store", "genres") or []) + (ctx.get("store", "tags") or [])[:5]
    ):
        out.add(g.lower())
        out |= {w for w in words(g) if len(w) >= 4}
    return out


CLAIMS: list[tuple[re.Pattern[str], Callable[[Context], bool], str]] = [
    (
        re.compile(r"\bco-?op\b|\bcooperative\b", re.I),
        lambda c: bool(c.get("game", "players", "online_coop") or c.get("game", "players", "local_coop")),
        "co-op",
    ),
    (
        re.compile(r"\bpvp\b|\bversus\b|\bcompetitive\b", re.I),
        lambda c: bool(c.get("game", "players", "online_pvp") or c.get("game", "players", "local_pvp")),
        "PvP",
    ),
    (
        re.compile(r"\bremote play\b", re.I),
        lambda c: c.get("game", "platform_features", "remote_play_together") is True,
        "Remote Play Together",
    ),
    (
        re.compile(r"\bsteam deck\b", re.I),
        lambda c: c.get("game", "platform_features", "steam_deck") in ("verified", "playable"),
        "Steam Deck",
    ),
    (
        re.compile(r"\bcontroller\b|\bgamepad\b", re.I),
        lambda c: c.get("game", "platform_features", "controller") in ("full", "partial"),
        "controller support",
    ),
    (re.compile(r"\bleaderboards?\b", re.I), lambda c: bool(c.get("leaderboards")), "leaderboards"),
    (re.compile(r"\bachievements?\b", re.I), lambda c: bool(c.get("achievements")), "achievements"),
]


def _longest_shared_run(a: list[str], b: list[str]) -> int:
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def _number_words(n: int) -> set[str]:
    names = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        8: "eight",
        10: "ten",
        12: "twelve",
        16: "sixteen",
    }
    return {str(n), names.get(n, str(n))}


# ---------------------------------------------------------------------------------------------------- deterministic


def check_rule(rule: RubricRule, section: Section, text: str, ctx: Context) -> RubricResult:
    def res(outcome: Outcome, message: str = "") -> RubricResult:
        return RubricResult(
            rule_id=rule.id,
            check="deterministic",
            outcome=outcome,
            message=message,
            fix=rule.fix if outcome in ("fail", "warn") else "",
        )

    p = rule.params
    k = rule.kind
    body = plain(text)
    if k == "first_sentence_mentions_genre":
        genres = _genre_words(ctx)
        if not genres:
            return res("not_applicable", "No genres in steamworks.yaml yet.")
        first = first_sentence(text).lower()
        return (
            res("pass")
            if any(g in first for g in genres)
            else res("warn", "The first sentence does not name the genre.")
        )
    if k == "first_sentence_mentions_players":
        if not ctx.multiplayer:
            return res("not_applicable")
        return (
            res("pass")
            if PLAYER_COUNT.search(first_sentence(text))
            else res("warn", "The first sentence does not mention the player count or mode.")
        )
    if k == "regex_start_forbidden":
        start = body.lower()
        for pattern in p.get("patterns", []):
            if re.match(pattern, start):
                return res("warn", f'Opens with a stock phrase ("{body[:30]}…").')
        return res("pass")
    if k == "word_list_max":
        found = [w for w in words(text) if w in set(p.get("words", []))]
        return (
            res("pass")
            if len(found) <= int(p.get("max", 1))
            else res("warn", f"Hollow adjectives: {', '.join(found)}.")
        )
    if k == "claims_match_store":
        unsupported = [label for pattern, has, label in CLAIMS if pattern.search(body) and not has(ctx)]
        return (
            res("pass")
            if not unsupported
            else res("warn", f"Mentions {', '.join(unsupported)} but steamworks.yaml does not say the game has it.")
        )
    if k == "paragraph_max_words":
        long = [n for n in (len(words(x)) for x in paragraphs(text)) if n > int(p["max"])]
        return (
            res("pass")
            if not long
            else res("warn", f"{len(long)} paragraph(s) over {p['max']} words (longest {max(long)}).")
        )
    if k == "list_items_range":
        lists = list_items(text)
        if not lists:
            return res("warn", "No bullet list of features.")
        best = max(lists)
        ok = int(p.get("min", 0)) <= best <= int(p.get("max", 99))
        return (
            res("pass")
            if ok
            else res("warn", f"The feature list has {best} items; aim for {p.get('min')}-{p.get('max')}.")
        )
    if k == "mentions_features":
        missing = []
        players = ctx.get("game", "players") or {}
        lower = body.lower()
        if (
            players.get("max")
            and players["max"] > 1
            and not any(w in lower for w in _number_words(int(players["max"])))
        ):
            missing.append(f"the player count ({players['max']})")
        session = ctx.get("game", "session_length") or {}
        if (session.get("min_minutes") or session.get("max_minutes")) and not re.search(
            r"\b\d+\s*(?:-|to|–)?\s*\d*\s*min", lower
        ):
            missing.append("the session length")
        if ctx.get("game", "platform_features", "remote_play_together") is True and "remote play" not in lower:
            missing.append("Remote Play Together")
        if ctx.get("game", "platform_features", "steam_deck") in ("verified", "playable") and "steam deck" not in lower:
            missing.append("Steam Deck")
        if ctx.get("game", "platform_features", "controller") in ("full", "partial") and not re.search(
            r"controller|gamepad", lower
        ):
            missing.append("controller support")
        return res("pass") if not missing else res("warn", f"Does not mention {', '.join(missing)}.")
    if k == "words_range":
        n = len(words(text))
        lo, hi = int(p.get("min", 0)), int(p.get("max", 10**6))
        return res("pass") if lo <= n <= hi else res("warn", f"{n} words; comparable pages have {lo}-{hi}.")
    if k == "media_range":
        n = len(MEDIA.findall(text))
        lo, hi = int(p.get("min", 0)), int(p.get("max", 99))
        return res("pass") if lo <= n <= hi else res("warn", f"{n} images/clips; aim for {lo}-{hi}.")
    if k == "first_media_within_words":
        m = MEDIA.search(text)
        if m is None:
            return res("warn", "No image or clip.")
        n = len(words(plain(text[: m.start()])))
        return (
            res("pass")
            if n <= int(p["max"])
            else res("warn", f"The first clip comes after {n} words; aim for {p['max']} or fewer.")
        )
    if k == "phrase_list_max":
        lower = body.lower()
        found = [x for x in p.get("phrases", []) if re.search(rf"\b{re.escape(x.lower())}\b", lower)]
        return res("pass") if len(found) <= int(p.get("max", 0)) else res("warn", f"Stock phrases: {', '.join(found)}.")
    if k == "regex_forbidden":
        for pattern in p.get("patterns", []):
            m = re.search(pattern, body, re.I)
            if m:
                return res("warn", f'"{m.group(0)}"')
        return res("pass")
    if k == "genre_specificity":
        umbrella = {u.lower() for u in p.get("umbrella", [])}
        specific = {g for g in _genre_words(ctx) if g not in umbrella}
        if not specific:
            return res("not_applicable", "No tags more specific than the umbrella genres.")
        lower = body.lower()
        return (
            res("pass")
            if any(re.search(rf"\b{re.escape(g)}", lower) for g in specific)
            else res("warn", f"Only umbrella genres; none of: {', '.join(sorted(specific)[:6])}.")
        )
    if k == "overlap_with_short":
        short = ctx.get("store", "short_description") or ""
        if not short:
            return res("not_applicable", "No short description yet.")
        head = words(" ".join(words(body)[: int(p.get("window_words", 60))]))
        short_words = words(short)
        run = _longest_shared_run(head, short_words)
        grams = {tuple(short_words[i : i + 4]) for i in range(len(short_words) - 3)}
        head_grams = {tuple(head[i : i + 4]) for i in range(len(head) - 3)}
        ratio = len(grams & head_grams) / len(grams) if grams else 0.0
        if run > int(p.get("max_shared_run", 7)) or ratio > float(p.get("max_4gram_ratio", 0.5)):
            return res("warn", f"The opening repeats the short description ({run} words in a row).")
        return res("pass")
    if k == "headers_min":
        if len(words(text)) < int(p.get("min_words", 150)):
            return res("not_applicable")
        n = len(HEADER.findall(text))
        need = int(p.get("min", 2))
        return res("pass") if n >= need else res("warn", f"{n} section header(s); use at least {need}.")
    if k == "genre_keyword_coverage":
        umbrella = {u.lower() for u in p.get("umbrella", [])}
        keys = {w for t in (ctx.get("store", "tags") or [])[:5] + (ctx.get("game", "genres") or []) for w in words(t)}
        keys = {w for w in keys if len(w) >= 4 and w not in umbrella}
        if not keys:
            return res("not_applicable", "No genre words in steamworks.yaml yet.")
        used = sorted(keys & set(words(text)))
        need = min(int(p.get("min", 2)), len(keys))
        return (
            res("pass")
            if len(used) >= need
            else res("warn", f"Uses {len(used)} of the genre words players look for ({', '.join(sorted(keys)[:6])}).")
        )
    if k == "unexplained_acronyms":
        allow = {a.lower() for a in p.get("allow", [])}
        title = {w.lower() for w in re.findall(r"\w+", str(ctx.get("game", "name") or ""))}
        flagged = []
        for m in ACRONYM.finditer(body):
            tok = m.group(0)
            if tok.lower() in allow or tok.lower() in title or tok in flagged:
                continue
            around = body[max(0, m.start() - 2) : m.end() + 2]
            if "(" in around:  # expanded on first use: "time to kill (TTK)" or "TTK (time to kill)"
                continue
            flagged.append(tok)
        lower = body.lower()
        for j in p.get("jargon", []):
            term = re.escape(j)
            explained = re.search(rf"\(\s*{term}\s*\)|\b{term}\s*\(", lower)
            if re.search(rf"\b{term}\b", lower) and not explained and j.upper() not in flagged:
                flagged.append(j)
        return (
            res("pass")
            if len(flagged) <= int(p.get("max", 0))
            else res("warn", f"Unexplained terms: {', '.join(flagged)}.")
        )
    if k == "localized_text_coverage":
        if ctx.root is None:
            return res("not_applicable")
        from steamworks_mcp.localization.store import Localization

        source = str(ctx.get("source_language") or "english")
        own = ctx.get("store", "short_description" if section == "short" else "about") or ""
        key = FIELD[section]
        langs = ctx.get("store", "supported_languages") or {}
        missing, copied = [], []
        for lang, support in langs.items():
            if lang == source or not isinstance(support, dict):
                continue
            if not (support.get("interface") or support.get("subtitles")):
                continue
            text_l = Localization(ctx.root).read(lang).get(key, "")
            if not text_l.strip():
                missing.append(lang)
            elif plain(text_l).strip() == plain(own).strip():
                copied.append(lang)
        if not missing and not copied:
            return res("pass")
        parts = [f"missing in {', '.join(missing)}"] if missing else []
        parts += [f"untranslated in {', '.join(copied)}"] if copied else []
        return res("warn", "; ".join(parts) + ".")
    if k == "studio_talk":
        for para in paragraphs(text):
            start = plain(para).strip().lower()
            for pattern in p.get("start_patterns", []):
                if re.match(pattern, start):
                    return res("warn", f'A paragraph is about the studio ("{plain(para).strip()[:40]}…").')
        lower = body.lower()
        for pattern in p.get("anywhere", []):
            m = re.search(pattern, lower)
            if m:
                return res("warn", f'"{m.group(0)}"')
        return res("pass")
    if k == "no_placeholders":
        n = len(GIF.findall(text))
        return res("pass") if n == 0 else res("warn", f"{n} [GIF: …] placeholder(s) left.")
    return res("not_applicable", f"unknown rule kind {k}")


def evaluate(
    section: Section,
    text: str,
    values: dict[str, Any],
    guide: StyleGuide | None,
    *,
    final: bool = False,
    root: Path | None = None,
) -> tuple[list[RubricResult], list[dict[str, str]], float | None]:
    """(deterministic results, llm_judged questions, score 0-1 over the deterministic rules that apply)."""
    ctx = Context(values, root)
    results: list[RubricResult] = []
    questions: list[dict[str, str]] = []
    for rule in rules_for(guide):
        if rule.applies_to not in (section, "both") or (rule.stage == "final" and not final):
            continue
        if rule.check == "llm_judged":
            questions.append(
                {"rule_id": rule.id, "field": FIELD[section], "question": rule.question or "", "fix": rule.fix}
            )
            continue
        results.append(check_rule(rule, section, text, ctx))
    scored = [r for r in results if r.outcome != "not_applicable"]
    score = round(sum(1 for r in scored if r.outcome == "pass") / len(scored), 2) if scored else None
    return results, questions, score
