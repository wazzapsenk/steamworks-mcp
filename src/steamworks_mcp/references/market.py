"""Market study: what the store pages of a game's closest popular games do, as patterns the store-text briefs use.

Two steps, one tool each:

1. ``study_market`` picks the peers: games with the game's most specific store tags on Steam's "Popular New
   Releases" and "Top Sellers" lists, released in the last :data:`MAX_AGE_YEARS` years. It returns their store texts
   for the host model to read in this session and saves only the app ids and the tags used
   (``.steam-mcp/market/pending.json``). The texts stay in the local reference cache, like ``fetch_reference``'s.
2. ``save_market_study`` takes the model's labels for each page: a fixed vocabulary (how the short description opens
   and moves on, how About is built, the tone) and one technique note in the model's own words. Notes that repeat
   :data:`NOTE_MIN_RUN` or more consecutive words of the page are rejected. The pages are measured like the store
   patterns, and ``.steam-mcp/market/study.json`` keeps labels, notes and numbers: never page text.

Briefs then show the study (``market``) and add strategies built on it: the most common pattern among the peers,
and an opening few of them use that the game's own answers support. Drafts are anti-copy-checked against the
studied pages as well.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import ProjectFiles, atomic_write
from steamworks_mcp.manifest.state import is_empty
from steamworks_mcp.references.anticopy import find_overlaps
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher
from steamworks_mcp.references.patterns import GroupPatterns, group, measure

DEFAULT_PEERS = 10
MAX_PEERS = 15
MIN_NOTES = 3
MAX_AGE_YEARS = 3
MAX_ABOUT_CHARS = 3000
NOTE_MIN_RUN = 4
"""A technique note may not repeat this many consecutive words of the page it describes."""
MAX_NOTE_CHARS = 220
BROAD_TAGS = frozenset(
    {
        "indie", "action", "adventure", "casual", "simulation", "strategy", "rpg", "singleplayer", "multiplayer",
        "early access", "free to play", "2d", "3d", "great soundtrack", "atmospheric", "colorful", "cute",
        "fantasy", "family friendly",
    }
)  # fmt: skip
"""Store tags too broad to find a game's peers with; used only when the game has nothing more specific."""
MODE_TAGS = frozenset(
    {
        "co-op", "online co-op", "local co-op", "pvp", "online pvp", "local multiplayer", "split screen",
        "massively multiplayer", "4 player local", "controller", "team-based",
    }
)  # fmt: skip
"""Store tags that say how a game is played, not what it is: searched with a genre tag, never first."""

OPENINGS: dict[str, str] = {
    "genre_and_players": "Names the genre and who plays (solo, co-op, how many) first.",
    "player_fantasy": "Who the player gets to be and how that feels.",
    "core_action": "The concrete thing players do, in verbs.",
    "situation": "A typical moment or scenario the reader recognises.",
    "world_premise": "The setting or the story premise.",
    "twist": "The one unusual idea that sets the game apart.",
    "stakes": "A threat, a goal or a challenge to overcome.",
    "credentials": "The studio's earlier games, a sequel, awards or player numbers.",
}
"""How a short description opens: its first sentence's main job."""
MOVES: dict[str, str] = {
    **OPENINGS,
    "progression": "What grows or carries over between sessions.",
    "content_numbers": "Concrete amounts: levels, weapons, hours, modes.",
    "tone": "Sets the mood or makes a joke.",
    "invitation": "Ends with a call to play, a challenge or a promise.",
}
"""The job of each sentence (or clause) of a short description, in order."""
ABOUT_SHAPES: dict[str, str] = {
    "media_blocks": "Blocks of a GIF or image and a short paragraph, one per feature.",
    "headed_sections": "Sections under headers, mostly prose.",
    "feature_list": "Mostly a bullet list of features.",
    "prose": "Plain paragraphs with little structure.",
    "pitch_then_list": "A short pitch, then one feature list.",
}
"""How "About This Game" is built overall."""
ABOUT_SECTIONS: dict[str, str] = {
    "hook": "The opening pitch: what makes the game worth a look.",
    "core_loop": "What players do, step by step.",
    "players_modes": "Who plays and how: solo, co-op, PvP, player counts.",
    "progression": "What grows or carries over between sessions.",
    "content": "How much is in the game: levels, characters, items, hours.",
    "world_story": "The setting, the story, the characters.",
    "customization": "Building, crafting, cosmetics, choices.",
    "challenge": "Difficulty, mastery, replayability, procedural runs.",
    "roadmap": "Early Access plans, updates, the community's say.",
    "platforms_accessibility": "Steam Deck, controllers, accessibility, languages.",
    "feature_list": "A bullet list of features.",
    "credentials": "The studio, awards, press quotes, earlier games.",
}
"""The job of each part of "About This Game", in order."""
TONES: dict[str, str] = {
    "playful": "Light, funny, winking.",
    "epic": "Grand, heroic, cinematic.",
    "cozy": "Warm, calm, relaxing.",
    "dark": "Grim, eerie, unsettling.",
    "tense": "Urgent, high stakes, fast.",
    "mysterious": "Intriguing, holds things back.",
    "matter_of_fact": "Plain and informative.",
}

LIST_NAMES: dict[Literal["popularnew", "topsellers"], str] = {"popularnew": "popular_new", "topsellers": "top_seller"}
_RELEASE_FORMATS = ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y", "%b %Y", "%B %Y", "%Y")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _known(vocabulary: dict[str, str], name: str) -> Callable[[str], str]:
    def check(value: str) -> str:
        if value not in vocabulary:
            raise ValueError(f"{name} {value!r} is not one of: {', '.join(vocabulary)}")
        return value

    return check


class PageNote(Model):
    """The model's reading of one peer's store page."""

    appid: int
    short_opening: str
    """How the short description opens (one of :data:`OPENINGS`)."""
    short_moves: list[str] = Field(min_length=1, max_length=6)
    """The job of each sentence of the short description, in order (:data:`MOVES`)."""
    about_shape: str
    """How About is built (:data:`ABOUT_SHAPES`)."""
    about_sections: list[str] = Field(default_factory=list, max_length=12)
    """The job of each part of About, in order (:data:`ABOUT_SECTIONS`)."""
    tone: str
    """:data:`TONES`."""
    technique: str = Field(min_length=10, max_length=MAX_NOTE_CHARS)
    """One sentence in the model's own words: what makes this page work (or not)."""

    _opening = field_validator("short_opening")(_known(OPENINGS, "short_opening"))
    _shape = field_validator("about_shape")(_known(ABOUT_SHAPES, "about_shape"))
    _tone = field_validator("tone")(_known(TONES, "tone"))

    @field_validator("short_moves")
    @classmethod
    def _moves(cls, v: list[str]) -> list[str]:
        return [_known(MOVES, "short_moves item")(m) for m in v]

    @field_validator("about_sections")
    @classmethod
    def _sections(cls, v: list[str]) -> list[str]:
        return [_known(ABOUT_SECTIONS, "about_sections item")(s) for s in v]


class Peer(Model):
    appid: int
    name: str
    released: str | None
    lists: list[str]
    """Which store lists it was found on: popular_new, top_seller."""
    genres: list[str]


class Pending(Model):
    """A study that was started: the peers the model was shown, before their labels came back."""

    started_on: dt.date
    tags: list[str]
    peers: list[Peer]


class Share(Model):
    label: str
    share: float
    """Share of the labelled pages (0.4 = 40%)."""


class MarketStudy(Model):
    study_version: Literal[1] = 1
    recorded_on: dt.date
    tags: list[str]
    """The store tags the peers were searched with."""
    peers: list[Peer]
    """The labelled peers."""
    notes: list[PageNote]
    openings: list[Share]
    first_moves: list[Share]
    """The first two sentence jobs of the short descriptions, e.g. "genre_and_players > twist"."""
    about_shapes: list[Share]
    about_sections: list[Share]
    """Share of pages with each part."""
    about_order: list[str]
    """The parts on at least two pages and a third of them, in their usual order."""
    tones: list[Share]
    numbers: GroupPatterns
    """The peers measured like the store patterns."""


# ---------------------------------------------------------------------------------------------- files


def _dir(files: ProjectFiles) -> Path:
    return files.state_dir / "market"


def load_pending(files: ProjectFiles) -> Pending | None:
    p = _dir(files) / "pending.json"
    return Pending.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def load_study(files: ProjectFiles) -> MarketStudy | None:
    p = _dir(files) / "study.json"
    return MarketStudy.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def studied_appids(files: ProjectFiles) -> list[int]:
    """Every peer the model was shown for this project (started or saved): the anti-copy check covers them."""
    ids: list[int] = []
    for found in (load_pending(files), load_study(files)):
        if found is not None:
            ids += [p.appid for p in found.peers]
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------------------------------------- peers


def game_tags(values: dict[str, Any]) -> list[str]:
    """Tag names to search with: the store tags (most important first), else the Steam genres and the game's own
    genre words."""
    store = values.get("store") or {}
    game = values.get("game") or {}
    names = list(store.get("tags") or [])
    if not names:
        names = [store.get("primary_genre") or "", *(store.get("genres") or []), *(game.get("genres") or [])]
    return [str(n) for n in dict.fromkeys(n for n in names if n)]


def _released(details: dict[str, Any]) -> dt.date | None:
    text = str((details.get("release_date") or {}).get("date") or "").strip()
    for f in _RELEASE_FORMATS:
        try:
            return dt.datetime.strptime(text, f).date()
        except ValueError:
            continue
    return None


def _usable(details: dict[str, Any], today: dt.date) -> bool:
    if details.get("type") != "game" or "english" not in str(details.get("supported_languages", "")).lower():
        return False
    if (details.get("release_date") or {}).get("coming_soon"):
        return False
    released = _released(details)
    return released is None or (today - released).days <= MAX_AGE_YEARS * 366


def search_sets(tags: list[str]) -> list[list[str]]:
    """Tag combinations to search with, closest first: the two most important genre tags, the first genre tag
    with the first mode tag ("Physics" + "Co-op"), the first genre tag alone, the first two mode tags, the first
    mode tag alone. Broad tags ("Indie") stand in for genre tags only when the game has nothing else."""
    core = [t for t in tags if t.lower() not in BROAD_TAGS | MODE_TAGS]
    modes = [t for t in tags if t.lower() in MODE_TAGS]
    broad = [t for t in tags if t.lower() in BROAD_TAGS]
    if not core and not modes:
        core = broad
    sets = [core[:2], core[:1] + modes[:1], core[:1], modes[:2], modes[:1]]
    out: list[list[str]] = []
    for i, s in enumerate(sets):
        if s and s not in out and (len(s) == 2 or i in (2, 4)):  # a pair, or one of the single-tag fallbacks
            out.append(s)
    return out


def find_peers(
    fetcher: ReferenceFetcher,
    values: dict[str, Any],
    tags: list[str] | None = None,
    limit: int = DEFAULT_PEERS,
    *,
    today: dt.date | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[list[Peer], list[str], list[str]]:
    """The game's peers: (peers, tags searched with, tag names Steam does not know)."""
    today = today or dt.date.today()
    limit = max(MIN_NOTES, min(limit, MAX_PEERS))
    names = tags or game_tags(values)
    known = fetcher.store_tags()
    unknown = [n for n in names if n.lower() not in known]
    usable = [n for n in names if n.lower() in known]
    if not usable:
        raise ValueError(
            "No Steam store tags to search with"
            + (f" ({', '.join(unknown)} are not Steam tags)" if unknown else "")
            + ": set store.tags (Steam's tag names, most important first) or pass tags=[...]."
        )
    own = fp.get(values, "apps.main.appid")
    peers: list[Peer] = []
    seen: set[int] = {int(own)} if own else set()
    searched: list[str] = []
    for tag_set in search_sets(usable):
        if len(peers) >= limit:
            break
        searched += [t for t in tag_set if t not in searched]
        ids = [known[t.lower()] for t in tag_set]
        found: dict[int, list[str]] = {}
        lists = {name: fetcher.search(name, tags=ids, count=limit * 3) for name in LIST_NAMES}
        # Alternate between the lists so the peers are both new and proven.
        for row in range(max(len(v) for v in lists.values())):
            for name, appids in lists.items():
                if row < len(appids):
                    found.setdefault(appids[row], []).append(LIST_NAMES[name])
        for appid, on in found.items():
            if len(peers) >= limit:
                break
            if appid in seen:
                continue
            seen.add(appid)
            try:
                d = fetcher.appdetails(appid).data
            except FetchError as exc:
                log(f"{appid}: skipped ({exc})")
                continue
            if not _usable(d, today):
                continue
            released = _released(d)
            peers.append(
                Peer(
                    appid=appid,
                    name=str(d.get("name") or appid),
                    released=released.isoformat() if released else None,
                    lists=on,
                    genres=[g["description"] for g in d.get("genres", [])],
                )
            )
    return peers, searched, unknown


_HEADER = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
_IMG = re.compile(r"<img[^>]*src=\"([^\"]*)\"[^>]*>", re.I)
_VIDEO = re.compile(r"<video.*?</video>", re.I | re.S)


def readable(about_html: str, limit: int = MAX_ABOUT_CHARS) -> str:
    """About This Game as plain lines: "## header", "- item", "[GIF]" / "[image]" / "[video]" where media are."""
    s = _VIDEO.sub("\n[video]\n", about_html)
    s = _IMG.sub(lambda m: "\n[GIF]\n" if m.group(1).lower().split("?")[0].endswith(".gif") else "\n[image]\n", s)
    s = _HEADER.sub(lambda m: f"\n## {re.sub(r'<[^>]+>', ' ', m.group(1)).strip()}\n", s)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>|</p>|<p[^>]*>|</?ul[^>]*>|</?ol[^>]*>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in s.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …(cut)"


def page_texts(details: dict[str, Any]) -> dict[str, str]:
    return {
        "short": str(details.get("short_description", "")),
        "about": str(details.get("about_the_game") or details.get("detailed_description") or ""),
    }


def vocabulary() -> dict[str, dict[str, str]]:
    return {
        "short_opening": OPENINGS,
        "short_moves": MOVES,
        "about_shape": ABOUT_SHAPES,
        "about_sections": ABOUT_SECTIONS,
        "tone": TONES,
    }


def start(
    fetcher: ReferenceFetcher,
    values: dict[str, Any],
    files: ProjectFiles,
    tags: list[str] | None = None,
    limit: int = DEFAULT_PEERS,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Pick the peers, save them as the pending study and return their pages for the model to label."""
    today = today or dt.date.today()
    skipped: list[str] = []
    peers, searched, unknown = find_peers(fetcher, values, tags, limit, today=today, log=skipped.append)
    if len(peers) < MIN_NOTES:
        raise ValueError(
            f"Only {len(peers)} popular games with the tags {', '.join(searched)} (released in the last "
            f"{MAX_AGE_YEARS} years, English store text). Try other tags: study_market(path, tags=[...])."
        )
    atomic_write(
        _dir(files) / "pending.json",
        Pending(started_on=today, tags=searched, peers=peers).model_dump_json(indent=2) + "\n",
    )
    pages = []
    for p in peers:
        texts = page_texts(fetcher.appdetails(p.appid).data)
        pages.append(
            {
                **p.model_dump(),
                "short_description": html.unescape(re.sub(r"<[^>]+>", " ", texts["short"])).strip(),
                "about": readable(texts["about"]),
            }
        )
    out: dict[str, Any] = {
        "status": "label_these",
        "tags_searched": searched,
        "pages": pages,
        "task": "Read every page and label it with the vocabulary below: how the short description opens "
        "(short_opening), the job of each of its sentences in order (short_moves), how About is built "
        "(about_shape), the job of each of its parts in order (about_sections) and the tone. Add `technique`: "
        f"one sentence (at most {MAX_NOTE_CHARS} characters) in your own words on what makes the page work or "
        "what holds it back. Never quote the page.",
        "vocabulary": vocabulary(),
        "rules": "These pages are for analysis only. Never reuse their wording, never name these games on the "
        "store page (Valve bans references to other products) and never copy a page's structure word for word: "
        "drafts are checked against these texts.",
        "submit": "save_market_study(path, notes=[{appid, short_opening, short_moves, about_shape, "
        "about_sections, tone, technique}, …]), one note per page.",
    }
    if unknown:
        out["not_steam_tags"] = unknown
    if skipped:
        out["skipped"] = skipped
    return out


# ---------------------------------------------------------------------------------------------- save


def _shares(counts: Counter[str], total: int) -> list[Share]:
    return [Share(label=k, share=round(n / total, 2)) for k, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def _order(notes: list[PageNote]) -> list[str]:
    """Parts on at least two pages and a third of them, by their average relative position."""
    positions: dict[str, list[float]] = {}
    for n in notes:
        parts = list(dict.fromkeys(n.about_sections))
        for i, s in enumerate(parts):
            positions.setdefault(s, []).append(i / max(1, len(parts) - 1))
    common = [s for s, ps in positions.items() if len(ps) >= 2 and len(ps) * 3 >= len(notes)]
    return sorted(common, key=lambda s: (sum(positions[s]) / len(positions[s]), s))


def save(
    fetcher: ReferenceFetcher,
    files: ProjectFiles,
    notes: list[dict[str, Any]],
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Check the model's notes, measure the labelled pages and save the study."""
    pending = load_pending(files)
    if pending is None:
        raise ValueError("No market study was started: call study_market(path) first.")
    by_id = {p.appid: p for p in pending.peers}
    problems: list[str] = []
    checked: dict[int, PageNote] = {}
    for raw in notes:
        try:
            note = PageNote.model_validate(raw)
        except ValueError as exc:
            problems.append(f"{raw.get('appid', '?') if isinstance(raw, dict) else '?'}: {exc}")
            continue
        if note.appid not in by_id:
            problems.append(f"{note.appid}: not one of the pages study_market returned")
            continue
        overlaps = find_overlaps(note.technique, page_texts(fetcher.appdetails(note.appid).data), min_run=NOTE_MIN_RUN)
        if overlaps:
            problems.append(
                f"{note.appid}: technique repeats the page ({' '.join(overlaps[0].words)}); say it in your own words"
            )
            continue
        checked[note.appid] = note
    if problems:
        raise ValueError("Not saved. Fix these notes and send them all again: " + "; ".join(problems))
    if len(checked) < MIN_NOTES:
        raise ValueError(f"Label at least {MIN_NOTES} pages ({len(checked)} so far).")
    labelled = list(checked.values())
    pages = [measure(n.appid, fetcher.appdetails(n.appid).data) for n in labelled]
    total = len(labelled)
    study = MarketStudy(
        recorded_on=today or dt.date.today(),
        tags=pending.tags,
        peers=[by_id[n.appid] for n in labelled],
        notes=labelled,
        openings=_shares(Counter(n.short_opening for n in labelled), total),
        first_moves=_shares(Counter(" > ".join(n.short_moves[:2]) for n in labelled), total),
        about_shapes=_shares(Counter(n.about_shape for n in labelled), total),
        about_sections=_shares(Counter(s for n in labelled for s in set(n.about_sections)), total),
        about_order=_order(labelled),
        tones=_shares(Counter(n.tone for n in labelled), total),
        numbers=group(pages),
    )
    atomic_write(_dir(files) / "study.json", study.model_dump_json(indent=2) + "\n")
    missing = [p.name for p in pending.peers if p.appid not in checked]
    out: dict[str, Any] = {
        "saved": total,
        "file": (_dir(files) / "study.json").relative_to(files.root).as_posix(),
        "study": summary(study),
        "next": "generate(path, section='store_short') now includes the study and strategies built on it.",
    }
    if missing:
        out["not_labelled"] = missing
    return out


# ---------------------------------------------------------------------------------------------- briefs


def summary(study: MarketStudy) -> dict[str, Any]:
    top = study.openings[0]
    return {
        "games": len(study.notes),
        "most_common_opening": f"{top.label} ({top.share:.0%})",
        "most_common_about_shape": f"{study.about_shapes[0].label} ({study.about_shapes[0].share:.0%})",
        "usual_about_order": study.about_order,
    }


def _with_meaning(shares: list[Share], meanings: dict[str, str]) -> list[dict[str, Any]]:
    return [{**s.model_dump(), "means": meanings.get(s.label, "")} for s in shares]


def brief_section(study: MarketStudy | None, section: Literal["short", "long"]) -> dict[str, Any]:
    if study is None:
        return {
            "status": "not_studied",
            "tip": "study_market(path) compares the page with the game's closest popular Steam games first (what "
            "their pages open with, how they are built). Offer it to the user before writing.",
        }
    common: dict[str, Any] = {
        "status": "studied",
        "recorded_on": study.recorded_on.isoformat(),
        "games": len(study.notes),
        "searched_with_tags": study.tags,
        "tones": [s.model_dump() for s in study.tones],
        "techniques": [n.technique for n in study.notes],
        "how_to_use": "What the game's closest popular games do on their store pages (shares of the studied "
        "pages). Use what works, then make the hook unmistakably this game's. Never name these games and never "
        "echo their wording.",
    }
    if section == "short":
        return {
            **common,
            "openings": _with_meaning(study.openings, OPENINGS),
            "first_moves": [s.model_dump() for s in study.first_moves[:5]],
            "numbers": study.numbers.short_description.model_dump(),
        }
    return {
        **common,
        "about_shapes": _with_meaning(study.about_shapes, ABOUT_SHAPES),
        "about_sections": _with_meaning(study.about_sections, ABOUT_SECTIONS),
        "about_order": study.about_order,
        "numbers": {"about": study.numbers.about.model_dump(), "media": study.numbers.media.model_dump()},
    }


CONTRAST_ANSWERS = {
    "twist": "game.hook",
    "player_fantasy": "game.fantasy",
    "core_action": "game.core_loop",
    "situation": "game.core_loop",
    "stakes": "game.core_loop",
    "world_premise": "game.pitch",
}
"""Openings a contrasting variant can use, with the answer each one is built from."""


def strategies(study: MarketStudy | None, values: dict[str, Any]) -> dict[str, str]:
    """Short-description strategies built on the study: the peers' common pattern, and a rare opening."""
    if study is None:
        return {}
    top, first = study.openings[0], study.first_moves[0]
    out = {
        "market_common": f"Follow what most of the {len(study.notes)} studied games do: {top.share:.0%} open with "
        f"{top.label} ({OPENINGS[top.label]}) and the most common first two sentences are {first.label}. Fill "
        "that pattern with this game's own answers.",
    }
    share = {s.label: s.share for s in study.openings}
    candidates = [
        (share.get(o, 0.0), o, answer)
        for o, answer in CONTRAST_ANSWERS.items()
        if o != top.label and not is_empty(fp.get(values, answer))
    ]
    if candidates:
        s, opening, answer = min(candidates)
        out["market_contrast"] = (
            f"Stand out: only {s:.0%} of the studied games open with {opening} ({OPENINGS[opening]}) Open that "
            f"way, built from {answer}."
        )
    return out
