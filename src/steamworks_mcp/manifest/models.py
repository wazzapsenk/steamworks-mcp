"""The values file, ``steamworks.yaml``: everything a game needs on Steam, as plain values.

Per-field metadata (status, source, confidence, gate, execution mode, evidence) lives in ``.steam-mcp/state.json``,
see :mod:`steamworks_mcp.manifest.state`. The user edits this file by hand too, so the models are permissive about
what is *missing* (``None`` / empty means "not known yet") and strict about what is *wrong* (unknown keys, bad codes).

Field paths (``store.short_description``, ``achievements.ACH_WIN.name``,
``apps.main.installation.launch_options.0.executable``) are resolved by :mod:`steamworks_mcp.manifest.paths`.
Two pieces of metadata steer that:

* ``x-key`` on a list field: list items are addressed by that attribute instead of their index.
* ``x-unit`` on a model: the whole object is one tracked field (for example a system-requirements block).
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from steamworks_mcp.languages import find_language, is_api_code

SCHEMA_VERSION = 1


def _language(code: str) -> str:
    if is_api_code(code):
        return code
    hint = find_language(code)
    suggestion = f' Did you mean "{hint.api}"?' if hint else ""
    raise ValueError(f'"{code}" is not a Steam API language code (e.g. "english", "schinese", "koreana").{suggestion}')


LanguageCode = Annotated[str, AfterValidator(_language)]
"""Steam API language code, e.g. ``english``, ``german``, ``schinese``, ``koreana``, ``brazilian``, ``latam``."""

API_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _api_name(name: str) -> str:
    if not API_NAME.match(name):
        raise ValueError(f'"{name}": API names may only contain letters, digits and underscores')
    return name


ApiName = Annotated[str, Field(max_length=128), AfterValidator(_api_name)]
"""Name used in game code and in Steamworks (achievements, stats, leaderboards): letters, digits, underscores."""

RelPath = Annotated[str, Field(description="Path relative to the folder that holds steamworks.yaml.")]
OsName = Literal["windows", "macos", "linux"]
TriState = bool | None
"""``None`` means "not known yet" and shows up as missing in gap reports."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_attribute_docstrings=True)


class Unit(Model):
    """A small object tracked as one field."""

    model_config = ConfigDict(json_schema_extra={"x-unit": True})


def keyed(key: str, description: str) -> Any:
    """A list whose items are addressed by ``key`` in field paths; keys must be unique."""
    return Field(default_factory=list, description=description, json_schema_extra={"x-key": key})


def _unique(items: list[Any], key: str, what: str) -> None:
    seen: set[str] = set()
    for item in items:
        value = getattr(item, key)
        if value in seen:
            raise ValueError(f'duplicate {what} "{value}"')
        seen.add(value)


# --------------------------------------------------------------------------------------------- game (writing inputs)


class Players(Unit):
    """How many people play together and how."""

    min: int | None = Field(default=None, ge=1)
    max: int | None = Field(default=None, ge=1)
    online_coop: TriState = None
    local_coop: TriState = None
    online_pvp: TriState = None
    local_pvp: TriState = None
    shared_split_screen: TriState = None


class SessionLength(Unit):
    """Typical length of one play session, in minutes."""

    min_minutes: int | None = Field(default=None, ge=1)
    max_minutes: int | None = Field(default=None, ge=1)


class PlatformFeatures(Model):
    remote_play_together: TriState = None
    steam_deck: Literal["verified", "playable", "unsupported", "unknown"] | None = None
    controller: Literal["full", "partial", "none"] | None = None
    """Steam's "Full controller support" / "Partial controller support" categories."""
    steam_input: TriState = None


class Engine(Model):
    name: str | None = None
    """e.g. ``unity``, ``godot``, ``unreal``."""
    version: str | None = None


class Game(Model):
    """What the game is. Inputs for the interview and for every text generator; not uploaded anywhere."""

    name: str | None = None
    """Product name as shown on Steam."""
    pitch: str | None = None
    """One sentence: what the player does and why it is fun."""
    genres: list[str] = Field(default_factory=list)
    """Free-text genre tags in your own words, e.g. ["co-op party", "physics comedy"]. Used to pick references."""
    players: Players = Field(default_factory=Players)
    session_length: SessionLength = Field(default_factory=SessionLength)
    core_loop: str | None = None
    usps: list[str] = Field(default_factory=list)
    """Unique selling points, most important first."""
    target_audience: str | None = None
    tone: str | None = None
    """e.g. "chaotic and funny", "cozy", "tense"."""
    platform_features: PlatformFeatures = Field(default_factory=PlatformFeatures)
    engine: Engine = Field(default_factory=Engine)
    reference_appids: list[int] = Field(default_factory=list)
    """Steam app ids of comparable games to learn patterns from (never copied)."""


# --------------------------------------------------------------------------------------------- gate 0


class Prerequisites(Model):
    """Gate 0: what has to exist before anything can be configured. Confirmed by the user, never scanned."""

    partner_account: TriState = None
    """A Steamworks partner account exists and the user can sign in."""
    steam_direct_fee_paid: TriState = None
    steam_direct_fee_paid_on: dt.date | None = None
    tax_interview_done: TriState = None
    bank_info_done: TriState = None
    identity_verified: TriState = None
    restricted_account: TriState = None
    """Automation (BROWSER mode, steamcmd) uses a separate Steamworks user with only the permissions it needs."""
    restricted_account_permissions: list[str] = Field(default_factory=list)
    """Permissions granted to that user, as named in Steamworks (Users & Permissions)."""


# --------------------------------------------------------------------------------------------- store page


class LanguageSupport(Unit):
    """One row of the store page's language table."""

    interface: bool = True
    full_audio: bool = False
    subtitles: bool = False


class Requirements(Unit):
    """Minimum or recommended system requirements for one OS (free text, as on the store page)."""

    os: str | None = None
    processor: str | None = None
    memory: str | None = None
    graphics: str | None = None
    directx: str | None = None
    network: str | None = None
    storage: str | None = None
    sound_card: str | None = None
    vr_support: str | None = None
    additional_notes: str | None = None


class PlatformRequirements(Model):
    minimum: Requirements | None = None
    recommended: Requirements | None = None


class SystemRequirements(Model):
    windows: PlatformRequirements | None = None
    macos: PlatformRequirements | None = None
    linux: PlatformRequirements | None = None


class StoreLinks(Model):
    website: str | None = None
    privacy_policy: str | None = None
    online_manual: str | None = None
    support: str | None = None
    social: dict[str, str] = Field(default_factory=dict)
    """e.g. {"discord": "https://…", "x": "https://…", "youtube": "https://…"}."""


class Legal(Model):
    legal_line: str | None = None
    """Copyright/legal line shown at the bottom of the store page, e.g. "© 2026 Example Studio"."""
    eula: RelPath | None = None
    """Custom EULA file, if any."""
    third_party_notices: str | None = None


class Store(Model):
    """The main game's store page."""

    short_description: str | None = None
    """Plain text, about 300 characters max, shown next to the header capsule."""
    about: str | None = None
    """"About This Game", Steam BBCode."""
    developers: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    franchise: str | None = None
    platforms: list[OsName] = Field(default_factory=list)
    """Operating systems ticked on the store page; each needs system requirements and a depot that launches."""
    genres: list[str] = Field(default_factory=list)
    """Steam store genres, e.g. ["Action", "Indie", "Casual"]."""
    tags: list[str] = Field(default_factory=list)
    """User tags you apply to the page, most important first."""
    categories: list[str] = Field(default_factory=list)
    """Steam categories, e.g. "Online Co-op", "Steam Achievements", "Steam Cloud", "Full controller support"."""
    supported_languages: dict[LanguageCode, LanguageSupport] = Field(default_factory=dict)
    system_requirements: SystemRequirements = Field(default_factory=SystemRequirements)
    links: StoreLinks = Field(default_factory=StoreLinks)
    legal: Legal = Field(default_factory=Legal)


class Trailer(Model):
    path: RelPath | None = None
    url: str | None = None
    title: str | None = None


class Focus(Unit):
    """Point of the key art that crops keep in view (0-1 from the left / top)."""

    x: float = Field(default=0.5, ge=0.0, le=1.0)
    y: float = Field(default=0.5, ge=0.0, le=1.0)


class Assets(Model):
    """Source art. Store images are derived from these (cropped, never stretched); artwork is never generated."""

    key_art: RelPath | None = None
    """Large art without text, used for capsules, library hero and page background."""
    key_art_focus: Focus = Field(default_factory=Focus)
    logo: RelPath | None = None
    """Transparent logo PNG, placed on capsules and used as the library logo."""
    screenshots_dir: RelPath = "store/screenshots"
    overrides: dict[str, RelPath] = Field(default_factory=dict)
    """Hand-made images that replace derived ones, keyed by asset id (e.g. "header_capsule", "library_hero")."""
    trailers: list[Trailer] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------- content


MatureDescriptor = Literal[
    "some_nudity_or_sexual_content",
    "frequent_violence_or_gore",
    "adult_only_sexual_content",
    "gratuitous_sexual_content",
    "general_mature_content",
]


class AiDisclosure(Model):
    """Steam's AI-generated content disclosure. Always asked, never assumed or scanned."""

    uses_ai: TriState = None
    pre_generated: str | None = None
    """How content made with AI tools during development is used."""
    live_generated: str | None = None
    """What is generated while the game runs, if anything."""
    guardrails: str | None = None
    """For live-generated content: what prevents illegal or infringing output."""


class Content(Model):
    """Content survey and disclosures."""

    survey_completed: TriState = None
    mature_descriptors: list[MatureDescriptor] = Field(default_factory=list)
    mature_description: str | None = None
    ai: AiDisclosure = Field(default_factory=AiDisclosure)
    ratings: dict[str, str] = Field(default_factory=dict)
    """Optional age ratings, e.g. {"pegi": "7", "esrb": "E10+"}."""


# --------------------------------------------------------------------------------------------- stats & achievements


class AchievementProgress(Unit):
    """A stat that drives the achievement's progress bar."""

    stat: ApiName
    min: float = 0
    max: float


class Achievement(Model):
    id: ApiName
    """API name used in game code, e.g. ``ACH_FIRST_WIN``."""
    name: str | None = None
    """Display name in the source language."""
    description: str | None = None
    hidden: bool = False
    icon: RelPath | None = None
    """Unlocked icon (any size/format; converted to 256x256 JPG)."""
    icon_locked: RelPath | None = None
    """Locked icon. Defaults to a greyscale version of ``icon``."""
    progress: AchievementProgress | None = None
    category: Literal["progression", "skill", "secret_funny", "collection", "other"] | None = None
    """Kind of achievement, used to balance a set."""


class Stat(Model):
    name: ApiName
    type: Literal["int", "float", "avgrate"] = "int"
    display_name: str | None = None
    default: float = 0
    min: float | None = None
    max: float | None = None
    max_change: float | None = None
    increment_only: bool = False
    aggregated: bool = False
    """Keep a global total across all players."""
    set_by: Literal["client", "game_server", "official_game_server"] = "client"
    window: float | None = None
    """Averaging window for ``avgrate`` stats."""


class Leaderboard(Model):
    name: ApiName
    display_name: str | None = None
    sort_method: Literal["ascending", "descending"] = "descending"
    display_type: Literal["numeric", "seconds", "milliseconds"] = "numeric"
    only_trusted_writes: bool = False
    only_friends_reads: bool = False


# ------------------------------------------------------------------------------------ per-app technical settings

AutoCloudRoot = Literal[
    "gameinstall",
    "SteamCloudDocuments",
    "WinMyDocuments",
    "WinAppDataLocal",
    "WinAppDataLocalLow",
    "WinAppDataRoaming",
    "WinSavedGames",
    "WindowsHome",
    "MacHome",
    "MacAppSupport",
    "MacDocuments",
    "LinuxHome",
    "LinuxXdgDataHome",
    "LinuxXdgConfigHome",
    "AndroidExternalData",
    "AndroidInternalData",
]
"""Auto-Cloud roots as Steamworks stores them; ``gameinstall`` is shown as "App Install Directory"."""

CloudOs = Literal["all", "windows", "macos", "linux", "android"]


class AutoCloudPath(Model):
    root: AutoCloudRoot
    subdirectory: str = ""
    pattern: str = Field(min_length=1)
    """File pattern, e.g. ``*.sav`` or ``*`` (Steamworks accepts an empty pattern; this tool does not)."""
    os: CloudOs = "all"
    recursive: bool = False


class AutoCloudOverride(Model):
    root: AutoCloudRoot
    """Root used in the base path that this override changes."""
    os: Literal["windows", "macos", "linux", "android"]
    use_instead: AutoCloudRoot
    add_path: str = ""
    replace_path: bool = False


class Cloud(Model):
    enabled: TriState = None
    byte_quota: int | None = Field(default=None, ge=0, le=10_000_000_000)
    """Bytes per user (Steamworks maximum 10,000,000,000)."""
    file_quota: int | None = Field(default=None, ge=0, le=10_000)
    """Files per user (Steamworks maximum 10,000)."""
    shared_appid: int | None = None
    """Share saves with another app (e.g. demo -> full game)."""
    developers_only: TriState = None
    sync_on_suspend: TriState = None
    auto_cloud: list[AutoCloudPath] = Field(default_factory=list)
    overrides: list[AutoCloudOverride] = Field(default_factory=list)


LaunchType = Literal[
    "default",
    "config",
    "vr",
    "openvroverlay",
    "openxr",
    "othervr",
    "server",
    "editor",
    "manual",
    "benchmark",
    "safemode",
    "option1",
    "option2",
    "option3",
]


class LaunchOption(Model):
    executable: str = Field(min_length=1)
    """Path inside the depot, e.g. ``MyGame.exe`` (Steamworks accepts an empty value; this tool does not)."""
    arguments: str = ""
    working_dir: str = ""
    description: str | None = None
    """Shown to players when there is more than one option; localized like other texts."""
    type: LaunchType = "default"
    os: Literal["all", "windows", "macos", "linux", "android"] = "windows"
    arch: Literal["all", "32", "64"] = "64"
    beta_key: str = ""
    owns_dlc: str = ""


class Installation(Model):
    install_folder: str | None = None
    launch_options: list[LaunchOption] = Field(default_factory=list)


class Depot(Model):
    name: str
    """Your label for the depot, e.g. ``windows``; used as its key in field paths."""
    depot_id: int | None = None
    os: Literal["all", "windows", "macos", "linux"] = "windows"
    arch: Literal["all", "32", "64"] = "64"
    content_root: RelPath | None = None
    """Build output folder for this depot."""
    exclude: list[str] = Field(default_factory=list)
    """File patterns left out of the depot, e.g. ``*.pdb``."""


class Branch(Model):
    name: str
    description: str | None = None
    password_protected: bool = False


class Builds(Model):
    depots: list[Depot] = keyed("name", "Depots of this app, addressed by name.")
    branches: list[Branch] = keyed("name", "Beta branches besides 'default', addressed by name.")
    set_live_on: str | None = None
    """Branch an uploaded build is set live on (never ``default`` on a released game without the user)."""

    @model_validator(mode="after")
    def _unique_keys(self) -> Builds:
        _unique(self.depots, "name", "depot")
        _unique(self.branches, "name", "branch")
        return self


class AppProfile(Model):
    """One Steam app: the main game, its demo or its playtest. Each has its own app id and technical settings."""

    appid: int | None = Field(default=None, gt=0)
    name: str | None = None
    """Defaults to the game name (+ " Demo" / " Playtest")."""
    installation: Installation = Field(default_factory=Installation)
    cloud: Cloud = Field(default_factory=Cloud)
    builds: Builds = Field(default_factory=Builds)


class Apps(Model):
    main: AppProfile = Field(default_factory=AppProfile)
    demo: AppProfile | None = None
    playtest: AppProfile | None = None


# --------------------------------------------------------------------------------------------- pricing & release


class Pricing(Model):
    free_to_play: TriState = None
    base_price_usd: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    """Base price in USD; Steam suggests regional prices from it."""
    regional_pricing: Literal["steam_recommended", "custom"] | None = None
    launch_discount_percent: int | None = Field(default=None, ge=0, le=90)
    submitted: TriState = None
    """Price submitted in Steamworks (it needs Valve approval before release)."""


class EarlyAccessAnswers(Model):
    """The Early Access questions on the store page (all required for an Early Access release)."""

    why: str | None = None
    """Why Early Access?"""
    duration: str | None = None
    """Approximately how long will this game be in Early Access?"""
    full_version_plans: str | None = None
    """How is the full version planned to differ from the Early Access version?"""
    current_state: str | None = None
    """What is the current state of the Early Access version?"""
    pricing_plans: str | None = None
    """Will the game be priced differently during and after Early Access?"""
    community_involvement: str | None = None
    """How are you planning on involving the Community in your development process?"""


class Release(Model):
    coming_soon_since: dt.date | None = None
    """Date the store page went public as Coming Soon."""
    planned_date: dt.date | None = None
    display_date: str | None = None
    """What the store shows before an exact date is set, e.g. "Q2 2027" or "Coming soon"."""
    early_access: TriState = None
    early_access_answers: EarlyAccessAnswers = Field(default_factory=EarlyAccessAnswers)
    store_review_passed: TriState = None
    build_review_passed: TriState = None
    events: list[str] = Field(default_factory=list)
    """Steam events this release takes part in (ids from data/events.yaml), e.g. a Next Fest edition."""


# --------------------------------------------------------------------------------------------- root


class Manifest(Model):
    """Root of ``steamworks.yaml``."""

    schema_version: Literal[1] = 1
    game: Game = Field(default_factory=Game)
    source_language: LanguageCode = "english"
    """Language the texts in this file are written in."""
    target_languages: list[LanguageCode] = Field(default_factory=list)
    """Languages to translate into; translations live in ``localization/<language>.yaml``."""
    apps: Apps = Field(default_factory=Apps)
    prerequisites: Prerequisites = Field(default_factory=Prerequisites)
    store: Store = Field(default_factory=Store)
    assets: Assets = Field(default_factory=Assets)
    content: Content = Field(default_factory=Content)
    achievements: list[Achievement] = keyed("id", "Achievements of the main game, addressed by API name.")
    stats: list[Stat] = keyed("name", "Stats of the main game, addressed by API name.")
    leaderboards: list[Leaderboard] = keyed("name", "Leaderboards of the main game, addressed by API name.")
    pricing: Pricing = Field(default_factory=Pricing)
    release: Release = Field(default_factory=Release)

    @field_validator("target_languages")
    @classmethod
    def _distinct_targets(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("target_languages contains a language twice")
        return v

    @model_validator(mode="after")
    def _cross_field(self) -> Manifest:
        _unique(self.achievements, "id", "achievement id")
        _unique(self.stats, "name", "stat name")
        _unique(self.leaderboards, "name", "leaderboard name")
        if self.source_language in self.target_languages:
            raise ValueError("target_languages must not contain the source language")
        stats = {s.name for s in self.stats}
        for a in self.achievements:
            if a.progress and stats and a.progress.stat not in stats:
                raise ValueError(f'achievement "{a.id}" tracks progress with unknown stat "{a.progress.stat}"')
        return self
