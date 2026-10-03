"""Store-page patterns of popular new releases: derived measurements per Steam genre and overall.

``scripts/build_store_patterns.py`` samples Steam's "Popular New Releases" list (at most :data:`MAX_GAMES` games),
measures each store page and writes ``data/store_patterns.json``: numbers, shares and Steam's own genre labels with
the recording date and the sampled app ids. Never text from the pages: store pages are copyrighted, and like
``fetch_reference`` the raw store data stays in the local reference cache. Briefs, the ``write_store_page`` prompt and
``steam://store-patterns`` use it as "what successful recent pages in this genre look like".
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from steamworks_mcp.references.analyze import (
    AboutStats,
    ShortDescriptionStats,
    about_stats,
    html_to_text,
    languages,
    quantile,
    short_stats,
)
from steamworks_mcp.references.anticopy import words
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher

MAX_GAMES = 80
MIN_GENRE_GAMES = 5
"""A genre gets its own group from this many sampled games on; smaller ones only count in ``overall``."""
SOURCE = (
    "Steam store 'Popular New Releases' (store.steampowered.com/search/?filter=popularnew&sort_by=Released_DESC) "
    "and /api/appdetails, English text, US store"
)

HEADER = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
COOP = re.compile(r"\bco-?op(?:erative)?\b", re.I)
MULTIPLAYER = re.compile(r"\bmulti-?player\b|\bpvp\b|\bversus\b", re.I)
STEAM_DECK = re.compile(r"\bsteam\s*deck\b", re.I)

_VERB_WORDS = """
    adapt arm assemble attack automate battle become befriend begin bend breed brew bring build buy capture care
    cast challenge chase choose claim climb collect combine command compete compose conquer control cook craft
    create customise customize decorate defend deliver design destroy dig discover dive dodge drive earn embark
    enjoy enter equip escape evolve expand experience explore face farm feed fight find fish fix fly follow forge
    gather go grow guide hack harness harvest heal help hide hold hunt immerse invade invent investigate join jump
    keep lead learn level live loot make manage map master match meet merge mine navigate negotiate outsmart outwit
    paint perform plan plant play prepare protect prove race raid raise rebuild recruit relax repair rescue research
    restore ride rise roam rule run sail save scout seek set settle shape share shoot slash smash sneak solve stack
    start steal step summon survive swim swap take tame team tell test track trade train travel uncover unite
    unleash unlock unwind upgrade venture wage wander wield wreck
"""
VERBS = frozenset(_VERB_WORDS.split())
"""Common imperative verbs that open store-page headers ("Build your crew"). A heuristic: some are nouns too."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------- one page


class PageMeasurements(Model):
    """One store page, measured. Kept in memory while building; only the aggregates are written."""

    appid: int
    genres: list[str]
    short: ShortDescriptionStats
    about: AboutStats
    verb_headers: int
    """Headers whose first word is a common imperative verb."""
    mentions_coop: bool
    mentions_multiplayer: bool
    mentions_steam_deck: bool
    screenshots: int
    trailers: int
    languages_interface: int
    languages_full_audio: int


def measure(appid: int, appdetails: dict[str, Any]) -> PageMeasurements:
    d = appdetails
    short = html_to_text(d.get("short_description", ""))
    about_html = d.get("about_the_game") or d.get("detailed_description") or ""
    headers = [words(html_to_text(h)) for h in HEADER.findall(about_html)]
    text = f"{short} {html_to_text(about_html)}"
    interface, audio = languages(d.get("supported_languages", ""))
    return PageMeasurements(
        appid=appid,
        genres=[g["description"] for g in d.get("genres", [])],
        short=short_stats(short),
        about=about_stats(about_html),
        verb_headers=sum(1 for h in headers if h and h[0] in VERBS),
        mentions_coop=bool(COOP.search(text)),
        mentions_multiplayer=bool(MULTIPLAYER.search(text)),
        mentions_steam_deck=bool(STEAM_DECK.search(text)),
        screenshots=len(d.get("screenshots", [])),
        trailers=len(d.get("movies", [])),
        languages_interface=interface,
        languages_full_audio=audio,
    )


def sample(
    fetcher: ReferenceFetcher, limit: int = MAX_GAMES, *, refresh: bool = False, log: Callable[[str], None] = print
) -> tuple[list[PageMeasurements], dict[str, str]]:
    """Measure the newest popular releases that are games with English store text: (measurements, raw texts).

    The raw texts are only for the caller's leak check; they never leave the machine.
    """
    pages: list[PageMeasurements] = []
    raw: dict[str, str] = {}
    for appid in fetcher.popular_new_releases():
        if len(pages) >= min(limit, MAX_GAMES):
            break
        try:
            d = fetcher.appdetails(appid, refresh=refresh).data
        except FetchError as exc:
            log(f"{appid}: skipped ({exc})")
            continue
        if d.get("type") != "game" or "english" not in str(d.get("supported_languages", "")).lower():
            continue
        pages.append(measure(appid, d))
        raw[f"{appid}:short"] = str(d.get("short_description", ""))
        raw[f"{appid}:about"] = str(d.get("about_the_game") or d.get("detailed_description") or "")
    return pages, raw


# ---------------------------------------------------------------------------------------- aggregates


class Dist(Model):
    """Median and middle half (25th to 75th percentile)."""

    median: float
    p25: float
    p75: float


class ShortPatterns(Model):
    chars: Dist
    words: Dist
    sentences: Dist
    second_person_share: float
    """Share of pages that address the player as "you"."""
    mentions_players_share: float
    """Share that mention a player count or mode (co-op, multiplayer, solo, "4 friends")."""
    numbers_share: float


class AboutPatterns(Model):
    words: Dist
    paragraphs: Dist
    paragraph_words: Dist
    """Average paragraph length of each page, in words."""
    max_paragraph_words: Dist
    with_headers_share: float
    headers: Dist | None
    """Header count of the pages that have headers."""
    headers_starting_with_verb_share: float | None
    """Share of all headers whose first word is a verb (heuristic: a list of common imperative verbs)."""
    with_lists_share: float
    list_items: Dist | None
    """Bullet points of the pages that have lists."""
    images: Dist
    animations: Dist
    """Animated GIFs and videos."""
    opens_with_media_share: float
    """Share whose first block is an image or an animation."""
    words_before_first_media: Dist | None


class MentionPatterns(Model):
    """Share of pages whose short description or About mentions it."""

    coop_share: float
    multiplayer_share: float
    steam_deck_share: float


class MediaPatterns(Model):
    screenshots: Dist
    trailers: Dist


class LanguagePatterns(Model):
    interface: Dist
    """Languages with interface support."""
    full_audio_share: float
    """Share of games with full audio in at least one language."""


class GroupPatterns(Model):
    games: int
    short_description: ShortPatterns
    about: AboutPatterns
    mentions: MentionPatterns
    media: MediaPatterns
    languages: LanguagePatterns


class StorePatterns(Model):
    patterns_version: Literal[1] = 1
    recorded_on: dt.date
    source: str
    appids: list[int]
    """The sampled games, so the file can be refreshed and checked."""
    overall: GroupPatterns
    genres: dict[str, GroupPatterns]
    """Per Steam genre (a game counts in each of its genres), for genres with enough sampled games."""


def _dist(values: list[float]) -> Dist:
    return Dist(median=round(statistics.median(values), 1), p25=quantile(values, 0.25), p75=quantile(values, 0.75))


def _maybe(values: list[float]) -> Dist | None:
    return _dist(values) if values else None


def _share(flags: list[bool]) -> float:
    return round(sum(flags) / len(flags), 2)


def group(pages: list[PageMeasurements]) -> GroupPatterns:
    s = [p.short for p in pages]
    a = [p.about for p in pages]
    with_headers = [x for x in a if x.headings]
    with_lists = [x for x in a if x.lists]
    header_count = sum(x.headings for x in a)
    return GroupPatterns(
        games=len(pages),
        short_description=ShortPatterns(
            chars=_dist([x.chars for x in s]),
            words=_dist([x.words for x in s]),
            sentences=_dist([x.sentences for x in s]),
            second_person_share=_share([x.second_person > 0 for x in s]),
            mentions_players_share=_share([x.mentions_players for x in s]),
            numbers_share=_share([x.has_numbers for x in s]),
        ),
        about=AboutPatterns(
            words=_dist([x.words for x in a]),
            paragraphs=_dist([x.paragraphs for x in a]),
            paragraph_words=_dist([x.avg_paragraph_words for x in a]),
            max_paragraph_words=_dist([x.max_paragraph_words for x in a]),
            with_headers_share=_share([x.headings > 0 for x in a]),
            headers=_maybe([x.headings for x in with_headers]),
            headers_starting_with_verb_share=(
                round(sum(p.verb_headers for p in pages) / header_count, 2) if header_count else None
            ),
            with_lists_share=_share([x.lists > 0 for x in a]),
            list_items=_maybe([x.list_items for x in with_lists]),
            images=_dist([x.images for x in a]),
            animations=_dist([x.animations for x in a]),
            opens_with_media_share=_share([bool(x.structure) and x.structure[0] in ("image", "animation") for x in a]),
            words_before_first_media=_maybe(
                [x.words_before_first_media for x in a if x.words_before_first_media is not None]
            ),
        ),
        mentions=MentionPatterns(
            coop_share=_share([p.mentions_coop for p in pages]),
            multiplayer_share=_share([p.mentions_multiplayer for p in pages]),
            steam_deck_share=_share([p.mentions_steam_deck for p in pages]),
        ),
        media=MediaPatterns(
            screenshots=_dist([p.screenshots for p in pages]), trailers=_dist([p.trailers for p in pages])
        ),
        languages=LanguagePatterns(
            interface=_dist([p.languages_interface for p in pages]),
            full_audio_share=_share([p.languages_full_audio > 0 for p in pages]),
        ),
    )


def build(pages: list[PageMeasurements], recorded_on: dt.date, source: str = SOURCE) -> StorePatterns:
    if not pages:
        raise ValueError("No store pages were measured.")
    by_genre: dict[str, list[PageMeasurements]] = {}
    for p in pages:
        for g in dict.fromkeys(p.genres):
            by_genre.setdefault(g, []).append(p)
    return StorePatterns(
        recorded_on=recorded_on,
        source=source,
        appids=[p.appid for p in pages],
        overall=group(pages),
        genres={g: group(ps) for g, ps in sorted(by_genre.items()) if len(ps) >= MIN_GENRE_GAMES},
    )


# ---------------------------------------------------------------------------------------- bundled data


@cache
def store_patterns() -> StorePatterns | None:
    f = resources.files("steamworks_mcp.data").joinpath("store_patterns.json")
    if not f.is_file():
        return None
    return StorePatterns.model_validate_json(f.read_text(encoding="utf-8"))


def for_game(values: dict[str, Any], data: StorePatterns | None = None) -> tuple[str, GroupPatterns] | None:
    """The group a game compares with: its primary Steam genre, else its most specific Steam genre (the one with the
    fewest sampled games), else all genres."""
    data = data or store_patterns()
    if data is None:
        return None
    store = values.get("store") or {}
    known = {g.lower(): g for g in data.genres}
    primary = known.get(str(store.get("primary_genre") or "").lower())
    if primary:
        return primary, data.genres[primary]
    candidates = [known[g.lower()] for g in store.get("genres") or [] if g.lower() in known]
    if candidates:
        best = min(candidates, key=lambda g: data.genres[g].games)
        return best, data.genres[best]
    return "all genres", data.overall


def brief_section(values: dict[str, Any], section: Literal["short", "long"]) -> dict[str, Any] | None:
    """What a brief shows of the patterns: the game's group, only the parts that matter for ``section``."""
    data = store_patterns()
    found = for_game(values, data)
    if data is None or found is None:
        return None
    name, g = found
    parts = (
        {"short_description": g.short_description, "mentions": g.mentions}
        if section == "short"
        else {"about": g.about, "mentions": g.mentions, "media": g.media}
    )
    return {
        "group": name,
        "games": g.games,
        "recorded_on": data.recorded_on.isoformat(),
        **{k: v.model_dump() for k, v in parts.items()},
        "how_to_use": "Store pages of popular new Steam releases in this group: the median and the middle half "
        "(p25-p75) of each measure, and shares (0.6 = 60% of pages). Aim inside the middle half unless the style "
        "guide or rubric says otherwise. Numbers only: there is no text to imitate.",
    }
