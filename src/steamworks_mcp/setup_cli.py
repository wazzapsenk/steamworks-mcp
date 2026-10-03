"""``steamworks-mcp setup`` and ``steamworks-mcp doctor``: connect the server to the user's apps, and check it.

``setup`` asks where the games are, optionally takes keys, writes them to the per-user settings file
(``~/.steamworks-mcp/settings.env``) and registers the server with the apps the user picks (Claude Desktop, Claude
Code, Cursor, Codex). Every config file it changes is backed up first, and a file it cannot parse is left alone.
``doctor`` checks the installation and says how to fix what is missing.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from steamworks_mcp import __version__
from steamworks_mcp.config import SETTINGS_FILE, home_dir, load_config, parse_dotenv

SERVER_NAME = "steamworks"
SOURCE = "git+https://github.com/wazzapsenk/steamworks-mcp"
CODEX_TIMEOUT = 900  # build uploads and Steamworks writes can take minutes

Ask = Callable[[str], str]


# ---------------------------------------------------------------------------------------------------- the command


def launch_command() -> list[str]:
    """How the apps start this server: this very installation, unless it runs from a throwaway uvx cache."""
    exe = Path(sys.executable)
    if any(part.lower() in ("uv", "archive-v0") for part in exe.parts) and "cache" in str(exe).lower():
        return ["uvx", "--from", SOURCE, "steamworks-mcp"]
    return [str(exe), "-m", "steamworks_mcp"]


# ---------------------------------------------------------------------------------------------------- settings file


def settings_path() -> Path:
    return home_dir(os.environ) / SETTINGS_FILE


def write_settings(path: Path, updates: dict[str, str]) -> None:
    """Set ``KEY=value`` lines, keeping the file's other lines and comments."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else ["# steamworks-mcp settings"]
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip().removeprefix("export ").strip()
        if "=" in line and not line.lstrip().startswith("#") and key in remaining:
            out.append(f"{key}={_quote(remaining.pop(key))}")
        else:
            out.append(line)
    out += [f"{k}={_quote(v)}" for k, v in remaining.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)  # keys live here


def _quote(value: str) -> str:
    """Single quotes keep backslashes as they are (Windows paths); the settings parser reads both."""
    if not any(c in value for c in " #'\""):
        return value
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------------------------------- clients


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    config: Path | None
    """The config file, or None for clients configured through their own command (Claude Code)."""

    def installed(self) -> bool:
        if self.id == "claude-code":
            return shutil.which("claude") is not None
        return self.config is not None and self.config.parent.is_dir()


def clients(home: Path | None = None) -> list[Client]:
    home = home or Path.home()
    system = platform.system()
    if system == "Windows":
        desktop = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude"
    elif system == "Darwin":
        desktop = home / "Library" / "Application Support" / "Claude"
    else:
        desktop = home / ".config" / "Claude"
    return [
        Client("claude-desktop", "Claude Desktop", desktop / "claude_desktop_config.json"),
        Client("claude-code", "Claude Code", None),
        Client("cursor", "Cursor", home / ".cursor" / "mcp.json"),
        Client("codex", "Codex", home / ".codex" / "config.toml"),
    ]


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    backup = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup)
    return backup


def add_to_json(path: Path, command: list[str]) -> str:
    """Claude Desktop and Cursor: ``mcpServers.steamworks`` in a JSON file."""
    data: dict[str, object] = {}
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return f"left alone: {path} is not valid JSON ({exc.msg}, line {exc.lineno}). Fix it and run setup again."
        if not isinstance(loaded, dict):
            return f"left alone: {path} does not hold a JSON object."
        data = loaded
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    entry = {"command": command[0], "args": command[1:]}
    if servers.get(SERVER_NAME) == entry:
        return f"already set up in {path}"
    servers[SERVER_NAME] = entry
    data["mcpServers"] = servers
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"added to {path}" + (f" (backup: {backup.name})" if backup else "")


def add_to_codex(path: Path, command: list[str]) -> str:
    """Codex: a ``[mcp_servers.steamworks]`` table in config.toml (appended; an existing table is kept)."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if f"[mcp_servers.{SERVER_NAME}]" in text:
        return f"already set up in {path} (edit [mcp_servers.{SERVER_NAME}] there to change it)"
    block = (
        f"\n[mcp_servers.{SERVER_NAME}]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = [{', '.join(json.dumps(a) for a in command[1:])}]\n"
        'default_tools_approval_mode = "writes"\n'
        f"tool_timeout_sec = {CODEX_TIMEOUT}\n"
    )
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + ("\n" if text else "") + block, encoding="utf-8")
    return f"added to {path}" + (f" (backup: {backup.name})" if backup else "")


def add_to_claude_code(command: list[str], run: Callable[[list[str]], int] | None = None) -> str:
    args = ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", *command]
    if shutil.which("claude") is None:
        return "Claude Code was not found. Once it is installed, run: " + " ".join(args)
    code = (run or (lambda a: subprocess.run(a, check=False).returncode))(args)
    return (
        "added for every project (user scope)"
        if code == 0
        else "claude mcp add failed; run it yourself: " + " ".join(args)
    )


def connect(client: Client, command: list[str]) -> str:
    if client.id == "claude-code":
        return add_to_claude_code(command)
    assert client.config is not None
    if client.id == "codex":
        return add_to_codex(client.config, command)
    return add_to_json(client.config, command)


def is_connected(client: Client) -> bool | None:
    """Whether the client already knows this server; None when that cannot be read."""
    if client.id == "claude-code":
        if shutil.which("claude") is None:
            return None
        res = subprocess.run(["claude", "mcp", "get", SERVER_NAME], capture_output=True, text=True, check=False)
        return res.returncode == 0
    if client.config is None or not client.config.is_file():
        return False
    text = client.config.read_text(encoding="utf-8", errors="replace")
    if client.id == "codex":
        return f"[mcp_servers.{SERVER_NAME}]" in text
    try:
        servers = json.loads(text).get("mcpServers") or {}
    except (json.JSONDecodeError, AttributeError):
        return None
    return SERVER_NAME in servers


# ---------------------------------------------------------------------------------------------------- setup


def setup(argv: list[str], ask: Ask = input, secret: Ask = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(prog="steamworks-mcp setup", description="Connect steamworks-mcp to your apps.")
    parser.add_argument("--root", help="the folder that holds your game projects")
    parser.add_argument(
        "--client",
        action="append",
        choices=[c.id for c in clients()],
        help="an app to connect (repeat for several); default: ask",
    )
    parser.add_argument("--yes", action="store_true", help="accept the defaults without asking")
    parser.add_argument(
        "--only-settings",
        action="store_true",
        help="save the settings only, connect no app (for the Claude Code and Cursor plugins)",
    )
    args = parser.parse_args(argv)
    path = settings_path()
    current = parse_dotenv(path.read_text(encoding="utf-8")) if path.is_file() else {}
    print(f"steamworks-mcp {__version__} setup\n")

    # 1. games folder
    default_root = args.root or current.get("STEAMWORKS_MCP_ROOT") or str(Path.cwd())
    root_text = (
        default_root if args.yes or args.root else (ask(f"Where are your games? [{default_root}] ") or default_root)
    )
    root = Path(root_text).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a folder.")
        return 2
    from steamworks_mcp.status import workspace

    found = workspace(root)
    print(f"  Games folder: {root} ({len(found['games'])} tracked, {len(found['not_tracked_yet'])} not tracked yet)")
    updates = {"STEAMWORKS_MCP_ROOT": str(root)}

    # 2. optional keys
    if not args.yes:
        print("\nOptional, press Enter to skip (you can add them later in " + str(path) + "):")
        key = secret("  Steamworks publisher Web API key (builds, branches, leaderboards): ").strip()
        if key:
            updates["STEAMWORKS_PUBLISHER_KEY"] = key
        browser = ask("  Let the assistant fill Steamworks pages in a browser window you sign in to? [y/N] ")
        if browser.strip().lower() in ("y", "yes"):
            updates["STEAM_MCP_BROWSER"] = "1"
    write_settings(path, updates)
    print(f"  Saved to {path}")

    # 3. apps
    if args.only_settings:
        print("\nDone. Restart the apps that use steamworks-mcp so they read the new settings.")
        return 0
    available = clients()
    if args.client:
        chosen = [c for c in available if c.id in args.client]
    else:
        detected = [c for c in available if c.installed()]
        if args.yes:
            chosen = detected
        else:
            print("\nConnect to which apps?")
            chosen = []
            for c in available:
                default = "Y/n" if c in detected else "y/N"
                answer = ask(f"  {c.name}{'' if c in detected else ' (not found)'}? [{default}] ").strip().lower()
                if answer in ("y", "yes") or (not answer and c in detected):
                    chosen.append(c)
    command = launch_command()
    print()
    for c in chosen:
        print(f"  {c.name}: {connect(c, command)}")
    if not chosen:
        print("  No app chosen. The command to run the server: " + " ".join(command))
    if updates.get("STEAM_MCP_BROWSER") == "1":
        try:
            import playwright  # noqa: F401
        except ImportError:
            print("\n  The browser mode needs one more package: uv sync --extra browser (or pip install playwright).")
    print('\nDone. Restart the apps you connected, then ask your assistant: "Where are my games on Steam?"')
    return 0


# ---------------------------------------------------------------------------------------------------- doctor


@dataclass
class Check:
    ok: bool | None
    """True fine, False needs fixing, None optional and off."""
    text: str
    fix: str = ""

    def line(self) -> str:
        mark = {True: "[ok]  ", False: "[fix] ", None: "[--]  "}[self.ok]
        return mark + self.text + (f"\n       -> {self.fix}" if self.fix and self.ok is not True else "")


def _browser_found() -> bool:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path("/Applications/Google Chrome.app"),
        Path("/Applications/Microsoft Edge.app"),
    ]
    names = ("google-chrome", "google-chrome-stable", "chrome", "microsoft-edge", "msedge")
    return any(p.exists() for p in candidates) or any(shutil.which(n) for n in names)


def checks(online: bool = True) -> list[Check]:
    config = load_config()
    out = [
        Check(sys.version_info >= (3, 11), f"Python {platform.python_version()}", "steamworks-mcp needs Python 3.11+"),
        Check(True, f"steamworks-mcp {__version__}"),
    ]
    path = settings_path()
    root_set = "STEAMWORKS_MCP_ROOT" in os.environ or (
        path.is_file() and "STEAMWORKS_MCP_ROOT" in parse_dotenv(path.read_text(encoding="utf-8"))
    )
    if config.workspace_root.is_dir():
        from steamworks_mcp.status import workspace

        found = workspace(config.workspace_root)
        out.append(
            Check(
                root_set or None,
                f"Games folder: {config.workspace_root} ({len(found['games'])} tracked, "
                f"{len(found['not_tracked_yet'])} not tracked yet)",
                "" if root_set else "not set, so the folder the app starts in is used: run steamworks-mcp setup",
            )
        )
    else:
        out.append(Check(False, f"Games folder {config.workspace_root} does not exist", "run steamworks-mcp setup"))
    out.append(
        Check(
            None if not config.publisher_key else True,
            "Publisher Web API key " + ("set" if config.publisher_key else "not set (optional: builds, leaderboards)"),
        )
    )
    if config.steamcmd_path:
        exists = Path(config.steamcmd_path).is_file()
        out.append(
            Check(
                exists and bool(config.steamcmd_username),
                f"steamcmd: {config.steamcmd_path}",
                "" if exists else "STEAMCMD_PATH does not point to a file",
            )
        )
    else:
        out.append(Check(None, "steamcmd not set (optional: build uploads)"))
    if config.browser_enabled:
        try:
            import playwright  # noqa: F401

            has_playwright = True
        except ImportError:
            has_playwright = False
        out.append(Check(has_playwright, "Browser mode on", "" if has_playwright else "uv sync --extra browser"))
        browser = _browser_found()
        out.append(
            Check(
                browser,
                "Chrome or Edge " + ("found" if browser else "not found"),
                "" if browser else "install Google Chrome or Microsoft Edge",
            )
        )
    else:
        out.append(Check(None, "Browser mode off (optional: the assistant fills Steamworks pages for you)"))
    if online:
        import httpx

        try:
            ok = httpx.get("https://store.steampowered.com/api/appdetails?appids=480", timeout=8).status_code == 200
        except httpx.HTTPError:
            ok = False
        out.append(
            Check(
                ok,
                "Steam store reachable" if ok else "Steam store not reachable",
                "" if ok else "check the internet connection; offline work still runs",
            )
        )
    from steamworks_mcp.remote import find_cloudflared

    tunnel = find_cloudflared()
    out.append(
        Check(
            True if tunnel else None,
            "cloudflared "
            + ("found" if tunnel else "not found")
            + " (for ChatGPT and claude.ai: steamworks-mcp remote)",
        )
    )
    for c in clients():
        state = is_connected(c)
        if state:
            out.append(Check(True, f"{c.name}: connected"))
        elif c.installed():
            out.append(Check(None, f"{c.name}: installed, not connected", f"steamworks-mcp setup --client {c.id}"))
    return out


def doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="steamworks-mcp doctor", description="Check the installation.")
    parser.add_argument("--offline", action="store_true", help="skip the network check")
    args = parser.parse_args(argv)
    results = checks(online=not args.offline)
    print("\n".join(c.line() for c in results))
    bad = [c for c in results if c.ok is False]
    print(f"\n{len(bad)} thing(s) to fix." if bad else "\nEverything needed is in place.")
    return 1 if bad else 0
