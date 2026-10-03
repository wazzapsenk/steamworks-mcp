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
    """Origin header values accepted over HTTP (browser-based clients)."""
    cache_dir: Path = field(default_factory=default_cache_dir)
    steamcmd_path: str | None = None


class WorkspaceError(ValueError):
    pass


def load_config(env: Mapping[str, str] | None = None, dotenv: Path | None = None) -> Config:
    values: dict[str, str] = {}
    path = dotenv if dotenv is not None else Path.cwd() / ".env"
    if path.is_file():
        values.update(parse_dotenv(path.read_text(encoding="utf-8")))
    values.update(env if env is not None else os.environ)  # the real environment wins over .env

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
        cache_dir=Path(c).expanduser() / "references" if (c := get("STEAMWORKS_MCP_CACHE")) else default_cache_dir(),
        steamcmd_path=get("STEAMCMD_PATH"),
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
