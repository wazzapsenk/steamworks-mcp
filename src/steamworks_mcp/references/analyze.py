"""Derived measurements of a reference game. The output contains numbers, Steam's own labels (categories, genres)
and structure only: never another game's description text or achievement names, so it can be committed and shown
to the model as guidance without inviting copies.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import statistics
from html.parser import HTMLParser
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from steamworks_mcp.references.anticopy import words

BlockKind = Literal["heading", "paragraph", "list", "image", "animation"]

PLAYER_COUNT = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|eight|ten)\s*(?:-|to|–)?\s*(?:\d+\s*)?(?:players?|friends?|people)\b"
    r"|\bco-?op\b|\bmultiplayer\b|\bsolo\b|\bsquad\b",
    re.I,
)
SENTENCE = re.compile(r"[.!?]+(?:\s|$)")
NUMBER = re.compile(r"\d")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShortDescriptionStats(Model):
    chars: int
    words: int
    sentences: int
    mentions_players: bool
    """Mentions a player count or mode (co-op, multiplayer, solo, "4 friends")."""
    second_person: int
    """Occurrences of "you"/"your"."""
    exclamations: int
    has_numbers: bool


class AboutStats(Model):
    chars: int
    words: int
    paragraphs: int
    avg_paragraph_words: float
    max_paragraph_words: int
    headings: int
    lists: int
    list_items: int
    images: int
    animations: int
    """Animated GIFs and videos."""
    words_before_first_media: int | None
    structure: list[BlockKind]
    """Block sequence with consecutive repeats collapsed, e.g. [animation, paragraph, heading, list, …]."""


NameStyle = Literal["UPPER_SNAKE", "PascalCase", "camelCase", "lower_snake", "numbered", "mixed", "none"]


class AchievementStats(Model):
    count: int
    api_name_style: NameStyle
    name_words_avg: float | None
    description_words_avg: float | None
    descriptions_with_numbers: float | None
    """Share of descriptions that contain a number (counts, thresholds)."""
    hidden_share: float | None
    """Needs a Steam Web API key (schema); otherwise unknown."""
    kinds: dict[str, int]
    """Heuristic split: progression, collection, skill, secret_funny, other."""
    percent_median: float | None
    percent_p10: float | None
    percent_p90: float | None
    share_under_10_percent: float | None
    share_under_1_percent: float | None


class StoreStats(Model):
    short_description: ShortDescriptionStats
    about: AboutStats
    screenshots: int
    movies: int
    categories: list[str]
    genres: list[str]
    languages_interface: int
    languages_full_audio: int
    platforms: list[str]
    controller_support: str | None
    is_free: bool
    price_usd: float | None
    release_date: str | None
    early_access: bool


class ReferenceAnalysis(Model):
    analysis_version: Literal[1] = 1
    appid: int
    name: str
    tags: list[str] = Field(default_factory=list)
    """Our tags from references/games.json (genre, player mode), used to match references to a game."""
    fetched_on: dt.date
    store: StoreStats
    achievements: AchievementStats
    steam_features: dict[str, bool]


# ---------------------------------------------------------------------------------------- description structure


class _Blocks(HTMLParser):
    """Steam's HTML for "About This Game" -> blocks (kind, word count)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[BlockKind, int]] = []
        self.list_items: int = 0
        self._buf: list[str] = []
        self._in_heading = False
        self._list_depth = 0
        self._items = 0
        self._brs = 0

    def _flush(self) -> None:
        n = len(words(" ".join(self._buf)))
        if n:
            self.blocks.append(("heading" if self._in_heading else "paragraph", n))
        self._buf = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h1", "h2", "h3"):
            self._flush()
            self._in_heading = True
        elif tag in ("ul", "ol"):
            self._flush()
            self._list_depth += 1
            if self._list_depth == 1:
                self._items = 0
        elif tag == "li" and self._list_depth:
            self._items += 1
        elif tag == "img":
            self._flush()
            src = dict(attrs).get("src") or ""
            self.blocks.append(("animation" if re.search(r"\.gif\b", src, re.I) else "image", 0))
        elif tag in ("video", "source") and not (self.blocks and self.blocks[-1][0] == "animation" and tag == "source"):
            self._flush()
            self.blocks.append(("animation", 0))
        elif tag == "p":
            self._flush()
        elif tag == "br":
            self._brs += 1
            if self._brs >= 2 and not self._list_depth:
                self._flush()
            return
        self._brs = 0

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3"):
            self._flush()
            self._in_heading = False
        elif tag in ("ul", "ol") and self._list_depth:
            self._list_depth -= 1
            if self._list_depth == 0:
                self._buf = []
                self.blocks.append(("list", self._items))
                self.list_items += self._items
        elif tag == "p":
            self._flush()

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._brs = 0
        if not self._list_depth:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def about_stats(about_html: str) -> AboutStats:
    p = _Blocks()
    p.feed(about_html)
    p.close()
    blocks = p.blocks
    paragraphs = [n for k, n in blocks if k == "paragraph"]
    plain = re.sub(r"<[^>]+>", " ", about_html)
    media_at = next((i for i, (k, _) in enumerate(blocks) if k in ("image", "animation")), None)
    structure: list[BlockKind] = []
    for k, _ in blocks:
        if not structure or structure[-1] != k:
            structure.append(k)
    return AboutStats(
        chars=len(re.sub(r"\s+", " ", plain).strip()),
        words=len(words(about_html)),
        paragraphs=len(paragraphs),
        avg_paragraph_words=round(statistics.fmean(paragraphs), 1) if paragraphs else 0.0,
        max_paragraph_words=max(paragraphs, default=0),
        headings=sum(1 for k, _ in blocks if k == "heading"),
        lists=sum(1 for k, _ in blocks if k == "list"),
        list_items=p.list_items,
        images=sum(1 for k, _ in blocks if k == "image"),
        animations=sum(1 for k, _ in blocks if k == "animation"),
        words_before_first_media=None if media_at is None else sum(n for k, n in blocks[:media_at] if k != "list"),
        structure=structure,
    )


def short_stats(text: str) -> ShortDescriptionStats:
    w = words(text)
    return ShortDescriptionStats(
        chars=len(text),
        words=len(w),
        sentences=max(1, len(SENTENCE.findall(text))) if text.strip() else 0,
        mentions_players=bool(PLAYER_COUNT.search(text)),
        second_person=sum(1 for x in w if x in ("you", "your", "you're", "yours")),
        exclamations=text.count("!"),
        has_numbers=bool(NUMBER.search(text)),
    )


# ---------------------------------------------------------------------------------------- achievements

SKILL = re.compile(
    r"\bwithout\b|\bunder\b|\bin less than\b|\bno damage\b|\bflawless\b|\bperfect\b|\bonly\b|\bnever\b", re.I
)
COLLECT = re.compile(r"\b(collect|find|gather|discover|unlock|buy|own|earn)\b", re.I)
PROGRESS = re.compile(r"\b(complete|finish|reach|beat|climb|escape|survive|win|clear|defeat|first)\b", re.I)
FUNNY = re.compile(r"\b(die|died|fall|fell|accident|oops|fail|lose|lost|trip|embarrass|scream|drop|cry)\w*\b", re.I)


def achievement_kind(description: str, hidden: bool = False) -> str:
    if hidden or FUNNY.search(description):
        return "secret_funny"
    if SKILL.search(description):
        return "skill"
    if NUMBER.search(description) and COLLECT.search(description):
        return "collection"
    if PROGRESS.search(description) or NUMBER.search(description):
        return "progression"
    if COLLECT.search(description):
        return "collection"
    return "other"


def api_name_style(names: list[str]) -> NameStyle:
    if not names:
        return "none"

    def style(n: str) -> NameStyle:
        if re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)*", n):
            return "numbered" if re.fullmatch(r"[A-Z]+_?\d+", n) else "UPPER_SNAKE"
        if re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)+", n):
            return "lower_snake"
        if re.fullmatch(r"[A-Z][A-Za-z0-9]*", n):
            return "PascalCase"
        if re.fullmatch(r"[a-z][A-Za-z0-9]*", n):
            return "camelCase"
        return "mixed"

    counts: dict[NameStyle, int] = {}
    for name in names:
        s = style(name)
        counts[s] = counts.get(s, 0) + 1
    top, hits = max(counts.items(), key=lambda kv: kv[1])
    return top if hits / len(names) >= 0.8 else "mixed"


def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    idx = (len(s) - 1) * q
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (idx - lo), 1)


def achievement_stats(
    percentages: list[dict[str, Any]], texts: list[dict[str, Any]], schema: dict[str, Any] | None, total: int | None
) -> AchievementStats:
    names = [r["name"] for r in percentages]
    pct = [float(r["percent"]) for r in percentages]
    hidden_by_name: dict[str, bool] = {}
    if schema:
        for a in schema.get("availableGameStats", {}).get("achievements", []) or []:
            hidden_by_name[a["name"]] = bool(a.get("hidden"))
    descs = [str(t.get("description", "")) for t in texts]
    kinds = {"progression": 0, "collection": 0, "skill": 0, "secret_funny": 0, "other": 0}
    for d in descs:
        kinds[achievement_kind(d)] += 1
    count = max(len(names), len(texts), total or 0)
    return AchievementStats(
        count=count,
        api_name_style=api_name_style(names),
        name_words_avg=round(statistics.fmean(len(words(t["name"])) for t in texts), 1) if texts else None,
        description_words_avg=round(statistics.fmean(len(words(d)) for d in descs), 1) if descs else None,
        descriptions_with_numbers=round(sum(1 for d in descs if NUMBER.search(d)) / len(descs), 2) if descs else None,
        hidden_share=round(sum(hidden_by_name.values()) / len(hidden_by_name), 2) if hidden_by_name else None,
        kinds=kinds,
        percent_median=round(statistics.median(pct), 1) if pct else None,
        percent_p10=quantile(pct, 0.1) if pct else None,
        percent_p90=quantile(pct, 0.9) if pct else None,
        share_under_10_percent=round(sum(1 for p in pct if p < 10) / len(pct), 2) if pct else None,
        share_under_1_percent=round(sum(1 for p in pct if p < 1) / len(pct), 2) if pct else None,
    )


# ---------------------------------------------------------------------------------------- store data

FEATURES = {
    "achievements": ("steam achievements",),
    "cloud": ("steam cloud",),
    "leaderboards": ("steam leaderboards",),
    "trading_cards": ("steam trading cards",),
    "workshop": ("steam workshop",),
    "remote_play_together": ("remote play together",),
    "full_controller_support": ("full controller support",),
    "partial_controller_support": ("partial controller support",),
    "online_coop": ("online co-op",),
    "online_pvp": ("online pvp",),
    "local_coop": ("shared/split screen co-op",),
    "single_player": ("single-player",),
    "in_app_purchases": ("in-app purchases",),
}


def languages(supported_html: str) -> tuple[int, int]:
    """(interface languages, full-audio languages) from appdetails' supported_languages HTML."""
    text = re.split(r"<br\s*/?>", supported_html, maxsplit=1)[0]
    entries = [e for e in (x.strip() for x in text.split(",")) if e]
    return len(entries), sum(1 for e in entries if "<strong>*</strong>" in e)


def analyze(
    appid: int,
    appdetails: dict[str, Any],
    percentages: list[dict[str, Any]],
    texts: list[dict[str, Any]],
    schema: dict[str, Any] | None,
    fetched_on: dt.date,
    tags: list[str] | None = None,
) -> ReferenceAnalysis:
    d = appdetails
    categories = list(dict.fromkeys(c["description"] for c in d.get("categories", [])))
    genres = [g["description"] for g in d.get("genres", [])]
    cats = {c.lower() for c in categories}
    interface, audio = languages(d.get("supported_languages", ""))
    price = d.get("price_overview") or {}
    store = StoreStats(
        short_description=short_stats(html_to_text(d.get("short_description", ""))),
        about=about_stats(d.get("about_the_game") or d.get("detailed_description") or ""),
        screenshots=len(d.get("screenshots", [])),
        movies=len(d.get("movies", [])),
        categories=categories,
        genres=genres,
        languages_interface=interface,
        languages_full_audio=audio,
        platforms=sorted(k for k, v in (d.get("platforms") or {}).items() if v),
        controller_support=d.get("controller_support"),
        is_free=bool(d.get("is_free")),
        price_usd=round(price["initial"] / 100, 2) if price.get("currency") == "USD" and "initial" in price else None,
        release_date=(d.get("release_date") or {}).get("date"),
        early_access="early access" in {g.lower() for g in genres},
    )
    return ReferenceAnalysis(
        appid=appid,
        name=str(d.get("name", "")),
        tags=tags or [],
        fetched_on=fetched_on,
        store=store,
        achievements=achievement_stats(percentages, texts, schema, (d.get("achievements") or {}).get("total")),
        steam_features={k: any(label in cats for label in labels) for k, labels in FEATURES.items()},
    )


def html_to_text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()
