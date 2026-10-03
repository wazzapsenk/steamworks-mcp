"""``steamworks-mcp remote``: make the server reachable for ChatGPT and claude.ai in one step.

Those apps connect from the internet, so the server needs a public https address. This command keeps an access code
in the settings file (made once), opens a Cloudflare quick tunnel to the local server (or uses ``--url`` for an
address you run yourself), starts the HTTP server with sign-in (OAuth, approved with the access code) and prints
exactly what to paste into ChatGPT and claude.ai. Ctrl+C stops both. The output is plain ASCII for Windows consoles.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import platform
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from steamworks_mcp.config import load_config, parse_dotenv
from steamworks_mcp.setup_cli import settings_path, write_settings

TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
WAIT_SECONDS = 45
INSTALL = {
    "Windows": "winget install --id Cloudflare.cloudflared",
    "Darwin": "brew install cloudflared",
    "Linux": "see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
}


def tunnel_url(line: str) -> str | None:
    m = TUNNEL_URL.search(line)
    return m.group(0) if m else None


def find_cloudflared() -> str | None:
    if found := shutil.which("cloudflared"):
        return found
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "cloudflared" / "cloudflared.exe",
        Path("/opt/homebrew/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
    ]
    return next((str(p) for p in candidates if p.is_file()), None)


def access_code(path: Path) -> str:
    """The token clients approve with: from the environment or the settings file, else a new one saved there."""
    if token := os.environ.get("STEAMWORKS_MCP_TOKEN", "").strip():
        return token
    current = parse_dotenv(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if (token := current.get("STEAMWORKS_MCP_TOKEN", "").strip()) and len(token) >= 24:
        return token
    token = secrets.token_hex(24)
    write_settings(path, {"STEAMWORKS_MCP_TOKEN": token})
    return token


def games_folder(path: Path, ask: Callable[[str], str] = input) -> bool:
    """Make sure the games folder is set (environment or settings file); ask for it when it is not and a person is
    at the keyboard. False when it stays unset."""
    if os.environ.get("STEAMWORKS_MCP_ROOT", "").strip():
        return True
    current = parse_dotenv(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if current.get("STEAMWORKS_MCP_ROOT", "").strip():
        return True
    if not sys.stdin.isatty():
        return False
    answer = ask("Where are your games? (the folder that holds your game projects) ").strip().strip('"')
    folder = Path(answer).expanduser().resolve() if answer else None
    if folder is None or not folder.is_dir():
        print(f"{answer or 'Nothing'} is not a folder.", flush=True)
        return False
    write_settings(path, {"STEAMWORKS_MCP_ROOT": str(folder)})
    return True


def start_tunnel(command: list[str], wait: float = WAIT_SECONDS) -> tuple[subprocess.Popen[str], str]:
    """Start a quick tunnel and return it with its public address (read from its output)."""
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace"
    )
    lines: queue.Queue[str] = queue.Queue()

    def pump() -> None:  # keep reading so the tunnel never blocks on a full pipe
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=pump, daemon=True).start()
    seen: list[str] = []
    while True:
        try:
            line = lines.get(timeout=wait)
        except queue.Empty:
            proc.terminate()
            raise RuntimeError("The tunnel did not report an address in time:\n" + "".join(seen[-10:])) from None
        seen.append(line)
        if url := tunnel_url(line):
            return proc, url


def instructions(url: str, token: str, temporary: bool) -> str:
    endpoint = f"{url}/mcp"
    lines = [
        "",
        "steamworks-mcp is reachable at:",
        f"    {endpoint}",
        "",
        "ChatGPT: Settings > Apps & Connectors > Advanced settings: turn on Developer mode, then create a connector",
        "  (in the desktop app: Settings > Plugins > MCPs > Add).",
        f"    URL: {endpoint}    Authentication: OAuth",
        "claude.ai: Settings > Connectors > Add custom connector.",
        f"    URL: {endpoint}",
        "",
        "When a page of steamworks-mcp asks for the access code, type:",
        f"    {token}",
        "",
        "Anyone with this code can read and change the games in your games folder: don't share it.",
    ]
    if temporary:
        lines += [
            "This address works while this window stays open. Next time you get a new one: update the connector's",
            "URL then (or use --url with an address of your own, see docs/ADVANCED.md).",
        ]
    lines += ["Stop with Ctrl+C.", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="steamworks-mcp remote", description="Make steamworks-mcp reachable for ChatGPT and claude.ai."
    )
    parser.add_argument("--port", type=int, default=8787, help="local port (default 8787)")
    parser.add_argument("--url", help="your own public https address that forwards to the port (no quick tunnel)")
    args = parser.parse_args(argv)

    from steamworks_mcp.__main__ import build_http_app, http_problems

    if not games_folder(settings_path()):
        print("Tell me where your games are first: steamworks-mcp setup --only-settings", flush=True)
        return 2
    token = access_code(settings_path())
    tunnel: subprocess.Popen[str] | None = None
    if args.url:
        url = args.url.rstrip("/")
    else:
        exe = find_cloudflared()
        if exe is None:
            print(
                "This needs cloudflared (free, from Cloudflare) to open a secure address. Install it with:", flush=True
            )
            print("    " + INSTALL.get(platform.system(), INSTALL["Linux"]))
            print("then run this command again. Or pass --url with an https address of your own.", flush=True)
            return 2
        print("Opening a secure address (Cloudflare quick tunnel)...", flush=True)
        try:
            tunnel, url = start_tunnel([exe, "tunnel", "--url", f"http://127.0.0.1:{args.port}", "--no-autoupdate"])
        except (OSError, RuntimeError) as exc:
            print(f"error: {exc}", flush=True)
            return 2
    config = dataclasses.replace(load_config(), public_url=url, http_token=token)
    if config.browser_enabled and not config.browser_remote:
        config = dataclasses.replace(config, browser_enabled=False)
        print(
            "Note: the BROWSER mode stays off over the internet (STEAM_MCP_BROWSER_REMOTE=1 turns it on).", flush=True
        )
    problems = http_problems(config, "127.0.0.1")
    if problems:
        print("\n".join(f"error: {p}" for p in problems), flush=True)
        if tunnel:
            tunnel.terminate()
        return 2
    print(instructions(url, token, temporary=tunnel is not None), flush=True)
    print(f"Games folder: {config.workspace_root}", flush=True)
    import uvicorn

    try:
        uvicorn.run(
            build_http_app(config, "127.0.0.1", args.port), host="127.0.0.1", port=args.port, log_level="warning"
        )
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
