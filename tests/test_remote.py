"""steamworks-mcp remote: the access code, the tunnel address and the instructions (no network, no real tunnel)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from steamworks_mcp import remote
from steamworks_mcp.config import parse_dotenv


def test_tunnel_url_from_cloudflared_output() -> None:
    line = "2026-10-03T15:00:00Z INF |  https://ocean-blue-river-7.trycloudflare.com                    |"
    assert remote.tunnel_url(line) == "https://ocean-blue-river-7.trycloudflare.com"
    assert remote.tunnel_url("2026-10-03T15:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...") is None


def test_access_code_is_made_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEAMWORKS_MCP_TOKEN", raising=False)
    settings = tmp_path / "settings.env"
    first = remote.access_code(settings)
    assert len(first) == 48 and parse_dotenv(settings.read_text("utf-8"))["STEAMWORKS_MCP_TOKEN"] == first
    assert remote.access_code(settings) == first
    monkeypatch.setenv("STEAMWORKS_MCP_TOKEN", "x" * 30)
    assert remote.access_code(settings) == "x" * 30


def test_instructions() -> None:
    text = remote.instructions("https://a-b.trycloudflare.com", "c" * 48, temporary=True)
    assert text.isascii()
    assert "URL: https://a-b.trycloudflare.com/mcp    Authentication: OAuth" in text
    assert "c" * 48 in text and "new one" in text and "Ctrl+C" in text
    assert "new one" not in remote.instructions("https://steam.example.com", "c" * 48, temporary=False)


def test_games_folder_is_asked_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEAMWORKS_MCP_ROOT", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    settings = tmp_path / "settings.env"
    assert remote.games_folder(settings, ask=lambda _: f'"{tmp_path}"')
    assert parse_dotenv(settings.read_text("utf-8"))["STEAMWORKS_MCP_ROOT"] == str(tmp_path.resolve())
    assert remote.games_folder(settings, ask=lambda _: pytest.fail("asked twice"))
    assert not remote.games_folder(tmp_path / "other.env", ask=lambda _: str(tmp_path / "missing"))


def fake_tunnel(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake_cloudflared.py"
    script.write_text("import sys, time\n" + body, encoding="utf-8")
    return [sys.executable, str(script)]


def test_start_tunnel_reads_the_address(tmp_path: Path) -> None:
    command = fake_tunnel(
        tmp_path,
        "print('INF Requesting new quick Tunnel', flush=True)\n"
        "print('INF |  https://fake-quick-tunnel.trycloudflare.com  |', flush=True)\n"
        "time.sleep(30)\n",
    )
    proc, url = remote.start_tunnel(command, wait=20)
    try:
        assert url == "https://fake-quick-tunnel.trycloudflare.com"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_start_tunnel_gives_up(tmp_path: Path) -> None:
    command = fake_tunnel(tmp_path, "print('INF starting', flush=True)\ntime.sleep(30)\n")
    with pytest.raises(RuntimeError, match="did not report an address"):
        remote.start_tunnel(command, wait=2)


def test_without_cloudflared_it_says_how_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("STEAMWORKS_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("STEAMWORKS_MCP_ROOT", str(tmp_path))
    monkeypatch.setattr(remote, "find_cloudflared", lambda: None)
    assert remote.main([]) == 2
    out = capsys.readouterr().out
    assert "cloudflared" in out and "--url" in out and out.isascii()
