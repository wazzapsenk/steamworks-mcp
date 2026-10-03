"""Review study: what players praise and criticize in the game's closest games (or in the game itself after launch).

Two steps, like the market study:

1. ``study_reviews`` returns the most helpful positive and negative English reviews of each game for the host model to
   read in this session, and saves which games were asked (``.steam-mcp/market/reviews_pending.json``). The texts stay
   in the local cache. Nothing about the reviewers is fetched or kept.
2. ``save_review_study`` takes the model's labels per game, from a fixed list of themes: what its players praise,
   what they criticize, and one sentence in the model's own words. Notes that repeat 4 or more consecutive words of a
   review are rejected. ``.steam-mcp/market/reviews.json`` keeps the labels and the shares: never review text.

Briefs then show what players of these games value (to lead with it when the game delivers it) and what they
complain about (to answer it when the game really does better).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from steamworks_mcp.manifest.io import ProjectFiles, atomic_write
from steamworks_mcp.references.anticopy import find_overlaps
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher

THEMES = {
    "core_loop": "the moment-to-moment gameplay and how fun it is to repeat",
    "controls_feel": "controls, responsiveness, game feel",
    "difficulty": "difficulty, challenge, balance",
    "progression": "unlocks, upgrades, sense of getting stronger",
    "content_amount": "how much there is to do, variety",
    "length_value": "length and value for the price",
    "replayability": "reasons to play again",
    "story_characters": "story, writing, characters",
    "world_atmosphere": "setting, atmosphere, worldbuilding",
    "art_visuals": "art style and visuals",
    "audio_music": "music and sound",
    "co_op_friends": "playing with friends, co-op",
    "multiplayer_online": "online play, matchmaking, netcode, servers",
    "performance": "frame rate, loading, hardware demands",
    "bugs_stability": "bugs, crashes, save problems",
    "ui_ux": "menus, interface, tutorial, readability",
    "updates_support": "updates, developer communication, roadmap",
    "monetization": "price, DLC, microtransactions",
    "accessibility_options": "settings, accessibility, key rebinding",
    "steam_deck_controller": "Steam Deck and controller support",
    "localization": "translations and language support",
    "originality": "fresh idea, unique mechanic",
}
REVIEWS_PER_KIND = 10
KINDS: tuple[Literal["positive", "negative"], ...] = ("positive", "negative")
MIN_NOTES = 3
NOTE_MIN_RUN = 4
MAX_NOTE_CHARS = 220


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _themes(values: list[str]) -> list[str]:
    unknown = [v for v in values if v not in THEMES]
    if unknown:
        raise ValueError(f"unknown theme(s) {', '.join(unknown)}; use: {', '.join(THEMES)}")
    return list(dict.fromkeys(values))


class GameNote(Model):
    """The model's reading of one game's reviews."""

    appid: int
    praised: list[str] = Field(min_length=1, max_length=4)
    """What its players praise most, most important first (:data:`THEMES`)."""
    criticized: list[str] = Field(default_factory=list, max_length=4)
    """What its players criticize most, most important first."""
    insight: str = Field(min_length=10, max_length=MAX_NOTE_CHARS)
    """One sentence in the model's own words: what players of this game care about."""

    _praised = field_validator("praised")(_themes)
    _criticized = field_validator("criticized")(_themes)


class ReviewedGame(Model):
    appid: int
    name: str


class Pending(Model):
    started_on: dt.date
    games: list[ReviewedGame]


class ThemeShare(Model):
    theme: str
    share: float
    """Share of the studied games where this theme is among the praised (or criticized) ones."""


class ReviewStudy(Model):
    study_version: Literal[1] = 1
    recorded_on: dt.date
    games: list[ReviewedGame]
    notes: list[GameNote]
    praised: list[ThemeShare]
    criticized: list[ThemeShare]


def _dir(files: ProjectFiles) -> Path:
    return files.state_dir / "market"


def load_pending(files: ProjectFiles) -> Pending | None:
    p = _dir(files) / "reviews_pending.json"
    return Pending.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def load_study(files: ProjectFiles) -> ReviewStudy | None:
    p = _dir(files) / "reviews.json"
    return ReviewStudy.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def start(
    fetcher: ReferenceFetcher, files: ProjectFiles, appids: list[int], per_kind: int = REVIEWS_PER_KIND
) -> dict[str, Any]:
    per_kind = max(3, min(per_kind, 25))
    games, out_games, skipped = [], [], []
    for appid in appids:
        try:
            name = str(fetcher.appdetails(appid).data.get("name") or appid)
            positive = fetcher.reviews(appid, "positive", count=per_kind).data
            negative = fetcher.reviews(appid, "negative", count=per_kind).data
        except FetchError as exc:
            skipped.append(f"{appid}: {exc}")
            continue
        if not positive and not negative:
            skipped.append(f"{appid}: no English reviews")
            continue
        games.append(ReviewedGame(appid=appid, name=name))
        out_games.append(
            {
                "appid": appid,
                "name": name,
                "positive": [{"hours": r["hours_at_review"], "text": r["review"]} for r in positive],
                "negative": [{"hours": r["hours_at_review"], "text": r["review"]} for r in negative],
            }
        )
    if not games:
        raise ValueError("None of these games has English reviews to study. " + "; ".join(skipped))
    pending = Pending(started_on=dt.date.today(), games=games)
    atomic_write(_dir(files) / "reviews_pending.json", pending.model_dump_json(indent=2) + "\n")
    out: dict[str, Any] = {
        "games": out_games,
        "themes": THEMES,
        "task": "Read each game's reviews (the most helpful positive and negative ones) and label the game: praised "
        "(1-4 themes, most important first), criticized (0-4) and one insight sentence in your own words. Judge by "
        "how often and how strongly a theme comes up, not by a single review.",
        "rules": "Reviews are for analysis only: never quote them, never put reviewers' words on the store page.",
        "submit": "save_review_study(path, notes=[{appid, praised, criticized, insight}, ...])",
    }
    if skipped:
        out["skipped"] = skipped
    return out


def _shares(counts: Counter[str], total: int) -> list[ThemeShare]:
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [ThemeShare(theme=k, share=round(n / total, 2)) for k, n in ranked]


def save(fetcher: ReferenceFetcher, files: ProjectFiles, notes: list[dict[str, Any]]) -> dict[str, Any]:
    pending = load_pending(files)
    if pending is None:
        raise ValueError("No review study was started: call study_reviews(path) first.")
    by_id = {g.appid: g for g in pending.games}
    problems: list[str] = []
    checked: dict[int, GameNote] = {}
    for raw in notes:
        try:
            note = GameNote.model_validate(raw)
        except ValueError as exc:
            problems.append(f"{raw.get('appid', '?') if isinstance(raw, dict) else '?'}: {exc}")
            continue
        if note.appid not in by_id:
            problems.append(f"{note.appid}: not one of the games study_reviews returned")
            continue
        texts = {
            f"{kind} review {i}": r["review"]
            for kind in KINDS
            for i, r in enumerate(fetcher.reviews(note.appid, kind, count=None).data)
        }
        if overlaps := find_overlaps(note.insight, texts, min_run=NOTE_MIN_RUN):
            problems.append(
                f"{note.appid}: insight repeats a review ({' '.join(overlaps[0].words)}); say it in your own words"
            )
            continue
        checked[note.appid] = note
    if problems:
        raise ValueError("Not saved. Fix these notes and send them all again: " + "; ".join(problems))
    if len(checked) < min(MIN_NOTES, len(by_id)):
        raise ValueError(f"Label at least {min(MIN_NOTES, len(by_id))} games ({len(checked)} so far).")
    labelled = list(checked.values())
    total = len(labelled)
    study = ReviewStudy(
        recorded_on=dt.date.today(),
        games=[by_id[n.appid] for n in labelled],
        notes=labelled,
        praised=_shares(Counter(t for n in labelled for t in n.praised), total),
        criticized=_shares(Counter(t for n in labelled for t in n.criticized), total),
    )
    atomic_write(_dir(files) / "reviews.json", study.model_dump_json(indent=2) + "\n")
    out: dict[str, Any] = {
        "saved": total,
        "file": (_dir(files) / "reviews.json").relative_to(files.root).as_posix(),
        "praised": [s.model_dump() for s in study.praised[:5]],
        "criticized": [s.model_dump() for s in study.criticized[:5]],
        "next": "The store-text briefs (generate store_short / store_long) now include what these players value.",
    }
    if missing := [g.name for g in pending.games if g.appid not in checked]:
        out["not_labelled"] = missing
    return out


def brief_section(study: ReviewStudy | None) -> dict[str, Any]:
    if study is None:
        return {
            "status": "not_studied",
            "tip": "study_reviews(path) reads what players praise and criticize in the closest games. Offer it to "
            "the user before writing; it is optional.",
        }
    return {
        "status": "studied",
        "recorded_on": study.recorded_on.isoformat(),
        "games": len(study.notes),
        "praised": [{**s.model_dump(), "means": THEMES[s.theme]} for s in study.praised[:6]],
        "criticized": [{**s.model_dump(), "means": THEMES[s.theme]} for s in study.criticized[:6]],
        "insights": [n.insight for n in study.notes],
        "how_to_use": "What players of the closest games value and complain about (shares of the studied games). "
        "Lead with a praised theme this game truly delivers; answer a common complaint only where the game really "
        "does better, in its own terms. Never name other games or quote reviews.",
    }
