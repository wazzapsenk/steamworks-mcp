"""Configuration from the environment and an optional ``.env`` file (a small parser of our own, no dependency).

Secrets (keys, tokens) never appear in ``repr()`` or logs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from steamworks_mcp.references import default_cache_dir

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_dotenv(text: str) -> dict[str, str]:
    """``KEY=value`` lines; ``#`` comments; single/double quotes; ``export`` prefix; no interpolation."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        key, value = m.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
            if raw.split("=", 1)[1].strip().startswith('"'):
                value = value.replace("\\n", "\n").replace('\\"', '"')
        else:
            value = re.sub(r"\s+#.*$", "", value)
        out[key] = value
    return out


def _csv(v: str | None) -> tuple[str, ...]:
    return tuple(x.strip() for x in (v or "").split(",") if x.strip())


def _flag(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    workspace_root: Path
    """Every project path a tool receives must be inside this folder."""
    browser_enabled: bool = False
    """STEAM_MCP_BROWSER=1: the opt-in BROWSER execution mode."""
    browser_remote: bool = False
    """STEAM_MCP_BROWSER_REMOTE=1: also allow BROWSER mode when serving over HTTP."""
    publisher_key: str | None = field(default=None, repr=False)
    web_api_key: str | None = field(default=None, repr=False)
    http_token: str | None = field(default=None, repr=False)
    allowed_hosts: tuple[str, ...] = ()
    """Extra Host header values accepted over HTTP (e.g. a tunnel's hostname)."""
    allowed_origins: tuple[str, ...] = ()
    public_url: str | None = None
    """STEAMWORKS_MCP_PUBLIC_URL: the https address clients reach the HTTP server at (e.g. a tunnel). Turns on the
    built-in OAuth sign-in for clients such as ChatGPT."""
    oauth_redirects: tuple[str, ...] = ()
    """Extra OAuth redirect URI prefixes accepted at client registration."""
    """Origin header values accepted over HTTP (browser-based clients)."""
    cache_dir: Path = field(default_factory=default_cache_dir)
    steamcmd_path: str | None = None
    steamcmd_username: str | None = None
    """The restricted builder account steamcmd logs in with (its password is never handled by this server)."""
    home_dir: Path = field(default_factory=lambda: Path.home() / ".steamworks-mcp")
    """Per-user data outside any repository: BROWSER consent and the browser profile."""

    @property
    def consent_path(self) -> Path:
        return self.home_dir / "consent.json"

    @property
    def browser_profile_dir(self) -> Path:
        return self.home_dir / "browser-profile"


class WorkspaceError(ValueError):
    pass


SETTINGS_FILE = "settings.env"
"""Per-user settings in the home folder (``steamworks-mcp setup`` writes it): the games folder and optional keys, the
same for every client, so client configurations need no environment variables."""


def home_dir(env: Mapping[str, str]) -> Path:
    h = (env.get("STEAMWORKS_MCP_HOME") or "").strip()
    return Path(h).expanduser() if h else Path.home() / ".steamworks-mcp"


def load_config(
    env: Mapping[str, str] | None = None, dotenv: Path | None = None, settings: Path | None = None
) -> Config:
    """The real environment wins over a ``.env`` (in the working folder, or ``dotenv``), which wins over the user's
    settings file. The settings file is read by default only with the real environment (``env`` is None)."""
    values: dict[str, str] = {}
    if settings is None and env is None:
        settings = home_dir(os.environ) / SETTINGS_FILE
    for path in (settings, dotenv if dotenv is not None else Path.cwd() / ".env"):
        if path is not None and path.is_file():
            values.update(parse_dotenv(path.read_text(encoding="utf-8")))
    values.update(env if env is not None else os.environ)

    def get(name: str) -> str | None:
        v = values.get(name, "").strip()
        return v or None

    root = get("STEAMWORKS_MCP_ROOT")
    return Config(
        workspace_root=Path(root).expanduser().resolve() if root else Path.cwd().resolve(),
        browser_enabled=_flag(get("STEAM_MCP_BROWSER")),
        browser_remote=_flag(get("STEAM_MCP_BROWSER_REMOTE")),
        publisher_key=get("STEAMWORKS_PUBLISHER_KEY"),
        web_api_key=get("STEAM_WEB_API_KEY"),
        http_token=get("STEAMWORKS_MCP_TOKEN"),
        allowed_hosts=_csv(get("STEAMWORKS_MCP_ALLOWED_HOSTS")),
        allowed_origins=_csv(get("STEAMWORKS_MCP_ALLOWED_ORIGINS")),
        public_url=(get("STEAMWORKS_MCP_PUBLIC_URL") or "").rstrip("/") or None,
        oauth_redirects=_csv(get("STEAMWORKS_MCP_OAUTH_REDIRECTS")),
        cache_dir=Path(c).expanduser() / "references" if (c := get("STEAMWORKS_MCP_CACHE")) else default_cache_dir(),
        steamcmd_path=get("STEAMCMD_PATH"),
        steamcmd_username=get("STEAMCMD_USERNAME"),
        home_dir=home_dir(values),
    )


def resolve_in_workspace(root: Path, user_path: str | None) -> Path:
    """Resolve a path a tool received; relative paths are relative to the workspace root. Refuses anything outside."""
    root = root.resolve()
    p = Path(user_path).expanduser() if user_path else root
    resolved = (p if p.is_absolute() else root / p).resolve()
    if resolved != root and root not in resolved.parents:
        raise WorkspaceError(
            f"{user_path!r} is outside the workspace root ({root}). Set STEAMWORKS_MCP_ROOT to allow it."
        )
    return resolved
