"""Interview questions: what to ask next, at most a few per turn, each with a suggested answer.

Questions come from three places: the gate rules (fields that are missing or still need approval, in gate order),
the ``game`` inputs every text generator needs, and the languages (the one texts are written in, the ones the game
supports, the ones its texts are translated into). Wording, type and options are derived from the schema (field
descriptions and types); a small catalog overrides the wording for the important ones. Values that were scanned are
shown for confirmation instead of being asked from scratch. Fields that generators write (store texts) are not asked.
"""

from __future__ import annotations

import datetime as dt
import re
import types
import typing
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from steamworks_mcp.gates.engine import RuleResult
from steamworks_mcp.gates.models import Confirmed
from steamworks_mcp.languages import all_languages, find_language
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.models import LanguageCode, Manifest
from steamworks_mcp.manifest.state import State, is_empty

Kind = Literal["text", "long_text", "int", "number", "bool", "choice", "multi", "list", "date", "path"]

GENERATED = {"store.short_description", "store.about"}
"""Written by generators (``generate``), not asked in the interview."""

GAME_INPUTS = [
    "game.name",
    "game.pitch",
    "game.genres",
    "game.comparable_games",
    "game.hook",
    "game.fantasy",
    "game.core_loop",
    "game.players",
    "game.session_length",
    "game.run_length",
    "game.progression",
    "game.launch_content",
    "game.usps",
    "game.target_audience",
    "game.tone",
    "game.platform_features.remote_play_together",
    "game.platform_features.controller",
    "game.platform_features.steam_deck",
]
"""Inputs for writing the store page; asked first (gate 1), in an order that builds the picture of the game."""

CORE_INPUTS = [
    "game.name",
    "game.pitch",
    "game.genres",
    "game.players",
    "game.core_loop",
    "game.usps",
    "game.target_audience",
    "game.tone",
]
"""The game inputs the store-text generators cannot do without; the others only make the text better."""

DESCRIBE = "game_description"
"""Synthetic question: one free answer about the game, which the assistant turns into ``game.*`` drafts."""
DESCRIBE_WHEN_EMPTY = 4
"""Ask for the description when at least this many core inputs are empty and none was answered or drafted yet."""
PREREQUISITES = "prerequisites_done"
"""Synthetic question: which of the account steps are done, as one multiple choice instead of one question each."""
PREREQUISITE_LABELS = {
    "prerequisites.partner_account": "Steamworks partner account",
    "prerequisites.steam_direct_fee_paid": "Steam Direct fee paid for this game",
    "prerequisites.tax_interview_done": "Tax interview",
    "prerequisites.bank_info_done": "Bank information",
    "prerequisites.identity_verified": "Identity verification",
    "prerequisites.restricted_account": "Separate, limited Steamworks user for automation",
}
NOT_ASKED = re.compile(
    r"^(assets\.|achievements\b|release\.coming_soon_since$|apps\.\w+\.cloud\.(byte|file)_quota$"
    r"|apps\.\w+\.builds\.depots\b)"
)
"""Fields a chat answer cannot fill well: files go into the game folder and through prepare_images, achievements
through their own flow, Cloud quotas and depots come from the scan; gap_report says how each gets done."""
MORE = "more_about_the_game"
"""Group of the optional game inputs, asked together in one round."""
SKIP_ANSWERS = {"skip", "pass", "later", "geç", "gec", "sonra", "überspringen", "passer"}
"""Answers that leave a field empty on purpose: it counts as answered and is not asked again."""

NONE_ANSWERS = {"none", "-", "nothing"}
"""Answers that mean "no languages" in a language list."""

CATALOG: dict[str, dict[str, Any]] = {
    "game.name": {"q": "What is the game's name as it should appear on Steam?"},
    "game.pitch": {"q": "In one sentence: what do players do, and why is it fun?", "kind": "long_text"},
    "game.genres": {"q": "Which genres describe it, in your own words? (comma-separated)", "suggest_from": "genres"},
    "game.comparable_games": {
        "q": "Which two or three games do your players already love? ('For fans of X and Y.')",
        "help": "Used to position the page and pick the genre's vocabulary; never named on the store page (Valve "
        "bans references to other products there).",
        "kind": "list",
    },
    "game.hook": {
        "q": "What is the hook: the one thing that makes it different from other games in its genre?",
        "kind": "long_text",
    },
    "game.fantasy": {
        "q": "What is the player fantasy: who do players get to be, and what does that feel like?",
        "kind": "long_text",
    },
    "game.players.min": {"q": "Can it be played solo? Minimum number of players (1 means solo works)."},
    "game.players.max": {"q": "Maximum number of players playing together?"},
    "game.players.online_coop": {"q": "Can players team up online (online co-op)?"},
    "game.players.local_coop": {"q": "Can players team up on one screen or one PC (local co-op)?"},
    "game.players.online_pvp": {"q": "Can players play against each other online (PvP)?"},
    "game.players.local_pvp": {"q": "Can players play against each other on one screen or one PC (local PvP)?"},
    "game.players.shared_split_screen": {"q": "Is there split-screen play on one screen?"},
    "game.session_length.min_minutes": {"q": "How long is a typical session, at the short end (minutes)?"},
    "game.session_length.max_minutes": {"q": "And at the long end (minutes)?"},
    "game.run_length": {
        "q": "How long does one run, match, round or level take? (Free text, e.g. '20-40 minute runs'; "
        "'same as a session' is fine.)"
    },
    "game.progression": {
        "q": "What carries over between sessions and grows: unlocks, upgrades, story, ranks? Say 'none' if every "
        "session starts fresh.",
        "kind": "long_text",
    },
    "game.launch_content": {
        "q": "What is in the game at launch? List it with numbers where you can (e.g. '4 maps', '30 weapons', "
        "'a 6-hour story', 'endless mode').",
        "kind": "list",
    },
    "game.core_loop": {
        "q": "Describe the core loop in player verbs (e.g. scavenge, build, defend, upgrade): what does a player do "
        "again and again?",
        "kind": "long_text",
    },
    "game.usps": {
        "q": "Which 2-4 selling points should the page make, most important first? (The hook can be the first.)",
        "kind": "list",
    },
    "game.target_audience": {"q": "Who is it for?"},
    "game.tone": {"q": "What tone should the store page have (e.g. chaotic and funny, cozy, tense)?"},
    "game.platform_features.remote_play_together": {"q": "Does it support Steam Remote Play Together?"},
    "game.platform_features.controller": {
        "q": "Controller support: full (every menu playable with a gamepad), partial, or none?"
    },
    "game.platform_features.steam_deck": {
        "q": "Steam Deck status, if known (verified, playable, unsupported, unknown)?"
    },
    "apps.main.appid": {"q": "What is the game's Steam app id (Steamworks > App Admin)?"},
    "prerequisites.partner_account": {"q": "Do you have a Steamworks partner account you can sign in to?"},
    "prerequisites.steam_direct_fee_paid": {"q": "Has the Steam Direct fee for this game been paid?"},
    "prerequisites.steam_direct_fee_paid_on": {
        "q": "On which date was the Steam Direct fee paid? (YYYY-MM-DD; release must be 21+ days later)"
    },
    "prerequisites.tax_interview_done": {"q": "Is the tax interview completed?"},
    "prerequisites.bank_info_done": {"q": "Is the bank information entered and valid?"},
    "prerequisites.identity_verified": {"q": "Is identity verification completed?"},
    "prerequisites.restricted_account": {
        "q": "Will automation (steamcmd uploads, optional browser mode) run as a separate Steamworks user "
        "with only the permissions it needs?"
    },
    "content.ai.uses_ai": {
        "q": "Were AI tools used to make any content in the game, or does it generate content with AI while "
        "running? (Steam requires a disclosure; the answer is never assumed.)"
    },
    "content.survey_completed": {"q": "Is the Content Survey in Steamworks completed (all three sections)?"},
    "store.platforms": {"q": "Which operating systems does the game support?", "kind": "multi"},
    "store.tags": {"q": "Which user tags fit best? Give at least 5, most important first.", "kind": "list"},
    "source_language": {
        "q": "Which language do you write the store page, achievements and other texts in? Everything is written "
        "once in this language and translated from it."
    },
    "store.supported_languages": {
        "q": "Which languages does the game itself support (menus and in-game text)? This fills the store page's "
        "language table; translating the store page is a separate question.",
        "help": "Full audio or subtitles in a language: set_field(field='store.supported_languages.<language>', "
        "value={interface: true, full_audio: true, subtitles: true}).",
    },
    "target_languages": {
        "q": "Which languages should the store page, achievements and other player-facing texts be translated "
        "into? Your assistant translates them from the source language. Suggested: English (Steam needs it) and the "
        "languages the game supports.",
        "help": "Answer 'none' to keep every text in the source language only.",
    },
    "store.genres": {"q": "Which Steam genres fit (e.g. Action, Casual, Indie, Strategy)?", "kind": "list"},
    "store.developers": {"q": "Developer name(s) as shown on the store page?", "kind": "list"},
    "store.publishers": {"q": "Publisher name(s)? (Your own studio when you self-publish.)", "kind": "list"},
    "store.support.url": {"q": "Support website for players? (Website, e-mail or phone: one of them is enough.)"},
    "store.support.email": {"q": "Support e-mail address? (Website, e-mail or phone: one of them is enough.)"},
    "store.support.phone": {"q": "Support phone number? (Website, e-mail or phone: one of them is enough.)"},
    "store.controller.xbox": {
        "q": "Xbox controllers: is the whole game playable with one (full), only parts of it (partial), or not at "
        "all (none)? Steam asks even when controllers are not supported."
    },
    "store.accessibility": {
        "q": "Which accessibility features does the game have (e.g. subtitles, resizable UI, difficulty levels, "
        "save anytime, color alternatives)? Say 'none' if it has none.",
    },
    "release.planned_date": {"q": "Planned release date? (YYYY-MM-DD; Steam keeps it hidden until you show it)"},
    "release.display_date": {"q": "What should the store show before the exact date (e.g. 'Q2 2027', 'Coming soon')?"},
    "release.coming_soon_since": {"q": "On which date did the store page go public as Coming Soon? (YYYY-MM-DD)"},
    "pricing.free_to_play": {"q": "Is the game free to play?"},
    "pricing.base_price_usd": {"q": "Base price in USD (Steam suggests regional prices from it)?"},
    "pricing.submitted": {"q": "Has the price been submitted in Steamworks for approval?"},
    "assets.key_art": {
        "q": "Path to the key art (large artwork without text), relative to steamworks.yaml?",
        "kind": "path",
    },
    "assets.logo": {"q": "Path to the logo (transparent PNG), relative to steamworks.yaml?", "kind": "path"},
    "assets.trailers": {
        "q": "Path(s) to your trailer video file(s), relative to steamworks.yaml? Steam needs at least one trailer "
        "before release; say 'not yet' if you have none.",
        "kind": "list",
    },
    "achievements": {
        "q": "Which achievements should the game have? Describe each in plain words (e.g. 'finish the tutorial', "
        "'win 10 matches'); your assistant turns them into Steam achievements.",
        "kind": "list",
    },
    "store.categories": {
        "q": "Which Steam features does the game have (e.g. Single-player, Online Co-op, Steam Achievements, Steam "
        "Cloud, Full controller support)?",
        "kind": "list",
    },
    "apps.main.cloud.enabled": {"q": "Should players' save files sync through Steam Cloud?"},
    "apps.main.installation.install_folder": {
        "q": "Folder name the game installs into under steamapps/common (usually the game's name)?"
    },
    "apps.main.installation.redistributables": {
        "q": "Which runtimes must Steam install with the game (e.g. Visual C++ 2022, DirectX)? Say 'none' if it "
        "needs none; Unity and Godot games usually need none.",
    },
    "release.store_review_passed": {"q": "Has Valve approved the store page review?"},
    "release.build_review_passed": {"q": "Has Valve approved the build review?"},
}


@dataclass
class Question:
    id: str
    """Field path the answer is written to."""
    question: str
    kind: Kind
    group: str
    gate: int
    options: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    """Display names of the options, e.g. "Chinese (Simplified)" for Steam's code "schinese"."""
    suggestion: Any = None
    """Pre-filled answer (scanned value, current draft or a sensible default) the user can confirm or change."""
    suggestion_source: str | None = None
    confirm: bool = False
    """True when the field already has a draft value (e.g. from the scan): the user only needs to confirm it."""
    help: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    optional: bool = False
    """Only makes the store text better: the user may answer "skip"."""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "question": self.question, "type": self.kind}
        if self.optional:
            out["optional"] = True
        if self.options:
            out["options"] = self.options
        if self.labels:
            out["option_labels"] = self.labels
        if self.suggestion is not None:
            out["suggestion"] = self.suggestion
            out["suggestion_source"] = self.suggestion_source
        if self.confirm:
            out["confirm"] = True
        if self.help:
            out["help"] = self.help
        if self.evidence:
            out["evidence"] = self.evidence[:3]
        return out


# ------------------------------------------------------------------------------------------ schema introspection


def _strip(annotation: Any) -> Any:
    while True:
        origin = get_origin(annotation)
        if origin is typing.Annotated:
            annotation = get_args(annotation)[0]
        elif origin in (Union, types.UnionType):
            args = [a for a in get_args(annotation) if a is not type(None)]
            if len(args) != 1:
                return annotation
            annotation = args[0]
        else:
            return annotation


def field_info(path: str) -> tuple[Any, str]:
    """(annotation, description) of the schema field at ``path``."""
    model: type[BaseModel] = Manifest
    annotation: Any = Manifest
    description = ""
    for seg in fp.split(path):
        tp = _strip(annotation)
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            info = tp.model_fields.get(seg)
            if info is None:
                return Any, ""
            # rebuild_annotation keeps Annotated metadata (LanguageCode is told apart from str by its validator)
            model, annotation, description = tp, info.rebuild_annotation(), info.description or ""
            continue
        origin = get_origin(tp)
        if origin is list:
            annotation = get_args(tp)[0]
        elif origin is dict:
            annotation = get_args(tp)[1]
        else:
            return Any, ""
    del model
    return annotation, description


def language_field(annotation: Any) -> Literal["one", "list", "dict"] | None:
    """Whether a field holds one Steam language code, a list of them, or a dict keyed by language."""
    tp = annotation
    if get_origin(tp) in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        tp = args[0] if len(args) == 1 else tp
    if tp == LanguageCode:
        return "one"
    if get_origin(tp) in (list, dict) and get_args(tp)[0] == LanguageCode:
        return "list" if get_origin(tp) is list else "dict"
    return None


def kind_of(annotation: Any) -> tuple[Kind, list[str]]:
    if lang := language_field(annotation):
        return ("choice" if lang == "one" else "multi"), [lg.api for lg in all_languages()]
    tp = _strip(annotation)
    if tp is bool:
        return "bool", []
    if tp is int:
        return "int", []
    if tp in (float, Decimal):
        return "number", []
    if tp is dt.date:
        return "date", []
    if get_origin(tp) is Literal:
        return "choice", [str(a) for a in get_args(tp)]
    if get_origin(tp) is list:
        inner = _strip(get_args(tp)[0])
        if get_origin(inner) is Literal:
            return "multi", [str(a) for a in get_args(inner)]
        return "list", []
    return "text", []


def _unit_children(path: str) -> list[str]:
    annotation, _ = field_info(path)
    tp = _strip(annotation)
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return [f"{path}.{name}" for name in tp.model_fields]
    return []


def _humanize(path: str) -> str:
    last = fp.split(path)[-1].replace("_", " ")
    return re.sub(r"\busd\b", "USD", last)


# ---------------------------------------------------------------------------------------------------- selection


def _synthetic(path: str, gate: int, values: dict[str, Any], group: str) -> Question | None:
    if path == DESCRIBE:
        return Question(
            DESCRIBE,
            "Tell me about your game in a few sentences: what it is, what players do again and again, who it is for, "
            "how many play together (online or on one screen), and the mood.",
            "long_text",
            group,
            gate,
            help="Your assistant fills the store-page inputs from this and shows them to you to confirm in one go.",
        )
    if path == PREREQUISITES:
        options = [p for p in PREREQUISITE_LABELS if fp.get(values, p) is None]
        return Question(
            PREREQUISITES,
            "Which of these Steamworks account steps are already done? Pick all that are; the rest become steps on "
            "your checklist, with links.",
            "multi",
            group,
            gate,
            options=options,
            labels={p: PREREQUISITE_LABELS[p] for p in options},
            help="Answer 'none' or 'not yet' if none of them are done.",
        )
    return None


def build_question(
    path: str, gate: int, values: dict[str, Any], state: State, group: str, root: Path | None = None
) -> Question:
    if synthetic := _synthetic(path, gate, values, group):
        return synthetic
    annotation, description = field_info(path)
    kind, options = kind_of(annotation)
    spec = CATALOG.get(path, {})
    kind = spec.get("kind", kind)
    first = re.split(r"(?<!e\.g)(?<!i\.e)\.\s", description)[0].rstrip(".?")
    text = spec.get("q") or (first + "?" if description else f"What is the {_humanize(path)}?")
    current = fp.get(values, path)
    if isinstance(current, dict) and kind == "multi":
        current = list(current)  # a dict keyed by language: the chosen languages are the answer
    fs = state.fields.get(path)
    q = Question(
        path, text, kind, group, gate, options, help=spec.get("help") or (description if spec.get("q") else "")
    )
    if language_field(annotation):
        q.labels = {lg.api: lg.name for lg in all_languages()}
    if path == "target_languages":
        source = values.get("source_language")
        q.options = [o for o in q.options if o != source]
        supported = [lang for lang in fp.get(values, "store.supported_languages") or {} if lang != source]
        if source and source != "english" and "english" not in supported:
            supported.insert(0, "english")  # Steam falls back to English, so the store needs English text
        if is_empty(current) and supported:
            q.suggestion, q.suggestion_source = supported, "store.supported_languages"
    if kind == "path" and not is_empty(current) and root is not None and not (root / str(current)).is_file():
        q.help = f"{current} was not found next to steamworks.yaml; give the right path."
    elif not is_empty(current):
        q.suggestion, q.suggestion_source, q.confirm = (
            current,
            (fs.source if fs and fs.source else "current value"),
            True,
        )
        if fs and fs.evidence:
            q.evidence = [e.model_dump() for e in fs.evidence]
    elif fs and fs.evidence:
        q.evidence = [e.model_dump() for e in fs.evidence]
    if group == MORE:
        q.optional = True
        q.help = (q.help + " " if q.help else "") + "Optional: answer 'skip' to leave it out."
    return q


def _expand_units(path: str, values: dict[str, Any], state: State) -> list[str]:
    """A unit (e.g. game.players) is asked through its sub-fields; only the empty ones unless it is a draft."""
    kids = _unit_children(path)
    if not kids:
        return [path]
    fs = state.fields.get(path)
    draft = fs is not None and fs.status in ("draft", "needs_review")
    return [k for k in kids if draft or is_empty(fp.get(values, k))]


def _drafted(path: str, state: State) -> bool:
    """A value waiting for the user's OK (in ``path`` or the unit holding it)."""
    unit = unit_ancestor(path)
    return any(
        (fs := state.fields.get(p)) is not None and fs.status in ("draft", "needs_review") for p in (path, unit) if p
    )


def _open(path: str, values: dict[str, Any], state: State) -> bool:
    """Still to ask: a value (or part of it) waits for confirmation, or it is empty and the user never answered it
    (an empty answer such as "no translations" counts as answered)."""
    if any(
        fs.status in ("draft", "needs_review")
        for p, fs in state.fields.items()
        if p == path or p.startswith(path + ".")
    ):
        return True
    fs = state.fields.get(path)
    return is_empty(fp.get(values, path)) and not (fs is not None and fs.source == "user")


def pending_fields(results: list[RuleResult], values: dict[str, Any], state: State) -> list[tuple[str, int, str]]:
    """``(field, gate, group)`` to ask about, in order, without duplicates:

    1. while most core game inputs are empty, one free description of the game (``game_description``);
    2. the core game inputs (drafts from that description are confirmed together);
    3. the languages: the source language has a default, so it is confirmed while the translation languages are
       still open; those are asked after the supported languages, which they default to;
    4. the optional game inputs, in one round the user may skip;
    5. the account steps (``prerequisites_done``), as one multiple choice;
    6. what the gate rules still miss, gate by gate.

    Yes/no confirmations answered "no" are not asked again: they are steps to do in Steamworks first. Fields a chat
    answer cannot fill (:data:`NOT_ASKED`) are left to gap_report.
    """
    out: list[tuple[str, int, str]] = []
    seen: set[str] = set()

    def add(path: str, gate: int, group: str) -> None:
        if path in seen or path in GENERATED or path.startswith("checklist.") or NOT_ASKED.match(path):
            return
        seen.add(path)
        for sub in _expand_units(path, values, state):
            if sub not in seen or sub == path:
                seen.add(sub)
                out.append((sub, gate, group))

    def add_rules(gates: set[int]) -> None:
        for r in sorted(results, key=lambda r: (r.gate, r.rule.severity != "required")):
            if r.gate not in gates or r.status not in ("fail", "review", "warn"):
                continue
            for path in r.missing + r.needs_approval:
                if "*" in path or (isinstance(r.rule.check, Confirmed) and fp.get(values, path) is False):
                    continue
                add(path, r.gate, r.rule.id)

    untouched = not any(p == c or p.startswith(c + ".") for p in state.fields for c in CORE_INPUTS)
    if untouched and sum(is_empty(fp.get(values, p)) for p in CORE_INPUTS) >= DESCRIBE_WHEN_EMPTY:
        out.append((DESCRIBE, 1, "game"))  # asked once: after it, the gaps are asked one by one
    for path in CORE_INPUTS:
        fs = state.fields.get(path)
        if is_empty(fp.get(values, path)) or (fs and fs.status in ("draft", "needs_review")):
            add(path, 1, "game")
    if _open("store.supported_languages", values, state):
        add("store.supported_languages", 1, "languages")
    elif _open("target_languages", values, state):
        add("source_language", 1, "languages")  # its default is confirmed once, with the translation languages
        add("target_languages", 1, "languages")
    for path in GAME_INPUTS:
        if path not in CORE_INPUTS and _open(path, values, state):
            add(path, 1, MORE)
    asked_at_once = [p for p in PREREQUISITE_LABELS if fp.get(values, p) is None]
    if asked_at_once:
        out.append((PREREQUISITES, 0, "prerequisites"))
    seen.update(PREREQUISITE_LABELS)
    add_rules({0, 1, 2, 3})
    return out


def next_questions(
    results: list[RuleResult], values: dict[str, Any], state: State, limit: int = 3, root: Path | None = None
) -> tuple[list[Question], int]:
    """At most ``limit`` questions, keeping a group together; plus how many fields remain after these.

    Three rounds ignore ``limit``: the game description comes alone, drafted game inputs are confirmed all at once
    (one table to check), and the optional game inputs come together (one "skip" answers them all)."""
    pending = pending_fields(results, values, state)
    if not pending:
        return [], 0
    first_group = pending[0][2]
    if pending[0][0] == DESCRIBE:
        chosen = pending[:1]
    elif first_group == MORE:
        chosen = [p for p in pending if p[2] == MORE]
    else:
        chosen = [p for p in pending if p[2] == first_group][:limit]
    drafted = [p for p in pending if p[2] == "game" and _drafted(p[0], state)]
    if first_group == "game" and len(drafted) > len(chosen) and all(_drafted(p[0], state) for p in chosen):
        chosen = drafted
        limit = len(chosen)
    if pending[0][0] == DESCRIBE or first_group == MORE:
        limit = len(chosen)
    for p in pending:
        if len(chosen) >= limit:
            break
        if p not in chosen and p[1] == chosen[0][1] and p[2] not in (MORE, "game"):
            chosen.append(p)
    questions = [build_question(path, gate, values, state, group, root) for path, gate, group in chosen]
    return questions, len(pending) - len(questions)


# ---------------------------------------------------------------------------------------------------- answers


TRUE = {"yes", "y", "true", "1", "evet", "ja", "oui"}
FALSE = {"no", "n", "false", "0", "hayir", "hayır", "nein", "non", "not yet", "henüz değil", "noch nicht", "pas encore"}
""""Not yet" is a no: for the yes/no confirmations it means a step still to do in Steamworks."""


def _option_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower()).replace("visualc", "vc")


def _language_codes(value: Any) -> Any:
    """Steam API codes from language names or codes ("German, Simplified Chinese" style answers welcome)."""
    if isinstance(value, str) and value.strip().lower() in (*NONE_ANSWERS, "no"):  # alone, "no" is not Norwegian
        return []
    items = re.split(r"[,\n]", value) if isinstance(value, str) else value
    if not isinstance(items, list):
        return value
    codes = []
    for item in items:
        text = str(item).strip()
        if text and text.lower() not in NONE_ANSWERS:
            lang = find_language(text)
            codes.append(lang.api if lang else text)  # unknown names stay, so the schema's error names them
    return list(dict.fromkeys(codes))


def coerce(path: str, value: Any, current: Any = None) -> Any:
    """Turn a chat answer into the field's type (lists from comma-separated text, yes/no into booleans, language
    names into Steam codes). ``current`` is the field's value now: a language table keeps its rows' details."""
    if isinstance(value, str) and value.strip().lower() in SKIP_ANSWERS:
        return None
    annotation, _ = field_info(path)
    kind, options = kind_of(annotation)
    lang = language_field(annotation)
    if lang == "one" and isinstance(value, str):
        found = find_language(value)
        return found.api if found else value.strip()
    if lang == "list":
        return _language_codes(value)
    if lang == "dict" and not isinstance(value, dict):
        rows = current if isinstance(current, dict) else {}
        codes = _language_codes(value)
        return {c: rows.get(c) or {} for c in codes} if isinstance(codes, list) else value
    if isinstance(value, str):
        v = value.strip()
        if kind in ("list", "multi"):
            if v.lower() in NONE_ANSWERS | FALSE:
                return ["none"] if "none" in options else []
            items = [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]
            if options:  # "Resizable UI" or "Visual C++ 2022" for the codes resizable_ui and vc2022
                by_key = {_option_key(o): o for o in options}
                return [by_key.get(_option_key(x), x) for x in items]
            item = _strip(get_args(_strip(annotation))[0]) if get_origin(_strip(annotation)) is list else None
            model_fields = item.model_fields if isinstance(item, type) and issubclass(item, BaseModel) else {}
            if key := next((k for k in ("path", "executable") if k in model_fields), None):
                return [{key: x} for x in items]  # trailers as file paths, launch options as the file to start
            return items
        if kind == "bool":
            if v.lower() in TRUE:
                return True
            if v.lower() in FALSE:
                return False
        if kind in ("int", "number") and v == "":
            return None
        return v
    return value


def prerequisite_answers(value: Any) -> dict[str, bool]:
    """The ``prerequisites_done`` answer as one yes/no per account step: the steps named are done, the others not
    yet. Steps can be named by field path or in words ("bank, tax interview")."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("all", "all of them", "hepsi"):
            return dict.fromkeys(PREREQUISITE_LABELS, True)
        items = [] if text in NONE_ANSWERS | FALSE else [x for x in re.split(r"[,\n]", value) if x.strip()]
    else:
        items = [str(x) for x in value or []]
    done = set()
    for item in items:
        key = _option_key(item)
        if not key:
            continue
        for path, label in PREREQUISITE_LABELS.items():
            if item.strip() == path or key in _option_key(label) or key in _option_key(path.split(".")[-1]):
                done.add(path)
                break
        else:
            raise ValueError(f"'{item.strip()}' is not one of: {', '.join(PREREQUISITE_LABELS.values())}.")
    return {path: path in done for path in PREREQUISITE_LABELS}


def unit_ancestor(path: str) -> str | None:
    """The tracked unit field that contains ``path`` (answers below a unit are recorded on the unit)."""
    segs = fp.split(path)
    for i in range(len(segs) - 1, 0, -1):
        parent = ".".join(segs[:i])
        try:
            node = fp.resolve_node(parent)
        except fp.FieldPathError:
            return None
        if node.kind == "unit":
            return parent
    return None
