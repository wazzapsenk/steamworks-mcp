"""Command line: ``steamworks-mcp`` (stdio) or ``steamworks-mcp --http`` (Streamable HTTP).

HTTP is for remote clients such as ChatGPT. It binds to 127.0.0.1 by default, always requires a bearer token
(``STEAMWORKS_MCP_TOKEN``, 24+ characters) and validates Host/Origin headers. Listening on another interface also
needs ``--host`` and ``STEAMWORKS_MCP_ALLOWED_HOSTS``. BROWSER mode over HTTP additionally needs
``STEAM_MCP_BROWSER_REMOTE=1``.
"""

from __future__ import annotations

import argparse
import hmac
import sys
from pathlib import Path
from typing import Any

from steamworks_mcp.config import Config, load_config
from steamworks_mcp.server import create_server

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MIN_TOKEN = 24


class BearerAuth:
    """ASGI middleware: every HTTP request needs ``Authorization: Bearer <token>`` (constant-time comparison)."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"")
            if not (auth[:7].lower() == b"bearer " and hmac.compare_digest(auth[7:].strip(), self.token)):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def http_problems(config: Config, host: str) -> list[str]:
    problems = []
    if not config.http_token:
        problems.append("HTTP mode needs a bearer token: set STEAMWORKS_MCP_TOKEN (e.g. `openssl rand -hex 24`).")
    elif len(config.http_token) < MIN_TOKEN:
        problems.append(f"STEAMWORKS_MCP_TOKEN must be at least {MIN_TOKEN} characters.")
    if host not in LOOPBACK and not config.allowed_hosts:
        problems.append(f"Listening on {host} needs STEAMWORKS_MCP_ALLOWED_HOSTS (the hostnames clients will use).")
    if config.browser_enabled and not config.browser_remote:
        problems.append("BROWSER mode over HTTP needs STEAM_MCP_BROWSER_REMOTE=1 in addition to STEAM_MCP_BROWSER=1.")
    return problems


def build_http_app(config: Config, host: str, port: int, path: str = "/mcp") -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]")]
    if host not in LOOPBACK:
        hosts.append(f"{host}:{port}")
    for h in config.allowed_hosts:
        hosts += [h, f"{h}:*"]
    security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=list(config.allowed_origins))
    app = create_server(config).streamable_http_app(streamable_http_path=path, transport_security=security, host=host)
    assert config.http_token
    return BearerAuth(app, config.http_token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steamworks-mcp", description="Steamworks release assistant (MCP server).")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP interface (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path (default /mcp)")
    parser.add_argument("--env-file", help=".env file to read (default: ./.env if present)")
    args = parser.parse_args(argv)
    config = load_config(dotenv=Path(args.env_file) if args.env_file else None)

    if not args.http:
        create_server(config).run()
        return 0
    problems = http_problems(config, args.host)
    if problems:
        print("\n".join(f"error: {p}" for p in problems), file=sys.stderr)
        return 2
    if args.host not in LOOPBACK:
        print(
            f"warning: serving on {args.host}:{args.port}. Anyone who can reach it and knows the token can read and "
            f"write game projects under {config.workspace_root}.",
            file=sys.stderr,
        )
    import uvicorn

    uvicorn.run(
        build_http_app(config, args.host, args.port, args.path), host=args.host, port=args.port, log_level="warning"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
