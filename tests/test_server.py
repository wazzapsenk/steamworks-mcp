"""MCP server end to end through an in-memory client, plus the HTTP transport's safety checks."""

from __future__ import annotations

import shutil
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client

from steamworks_mcp.__main__ import build_http_app, http_problems, main
from steamworks_mcp.config import Config, WorkspaceError, load_config, parse_dotenv, resolve_in_workspace
from steamworks_mcp.manifest.io import ManifestFile, ProjectFiles, load_state
from steamworks_mcp.server import create_server

FIXTURE = Path(__file__).parent / "fixtures" / "unity_min_project"
TOKEN = "t" * 32

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURE, tmp_path / "game")
    return tmp_path


async def call(config: Config, tool: str, **args: Any) -> dict[str, Any]:
    async with Client(create_server(config)) as client:
        result = await client.call_tool(tool, args)
    assert not result.is_error, getattr(result.content[0], "text", result.content)
    assert result.structured_content is not None
    return dict(result.structured_content)


async def test_init_project_creates_files_and_scans(workspace: Path) -> None:
    config = Config(workspace_root=workspace)
    out = await call(config, "init_project", path="game", appid=1000000)
    assert set(out["created"]) == {"steamworks.yaml", ".steam-mcp/.gitignore", ".steam-mcp/state.json"}
    assert out["scan"]["summary"]["applied"] > 5
    game = workspace / "game"
    m = ManifestFile.load(game / "steamworks.yaml").manifest
    assert [a.id for a in m.achievements] == ["ACH_FIRST_FORT", "ACH_TEN_FORTS"]
    assert m.apps.main.cloud.auto_cloud[0].pattern == "*.sav"
    assert m.apps.demo is not None and m.apps.demo.appid == 1000001
    state = load_state(ProjectFiles(game))
    assert state.get("apps.main.appid").status == "approved"  # given by the user to init_project
    assert state.get("game.name").status == "draft" and state.get("game.name").source == "scan"
    assert state.get("achievements.ACH_FIRST_FORT.name").evidence[0].line == 5
    assert (game / ".steam-mcp" / "scan" / "unity.json").exists()
    assert "never_read_this_user" not in (game / ".steam-mcp" / "scan" / "steam_build_pipeline.json").read_text("utf-8")


async def test_second_init_keeps_files_and_scan_respects_approved_values(workspace: Path) -> None:
    config = Config(workspace_root=workspace)
    await call(config, "init_project", path="game", scan=False)
    path = workspace / "game" / "steamworks.yaml"
    original = path.read_text("utf-8")
    assert "game: {}\n" in original
    path.write_text(original.replace("game: {}\n", "game:\n  name: Fort Night  # final name\n"), "utf-8")
    again = await call(config, "init_project", path="game", scan=False)
    assert "steamworks.yaml" in again["existing"]
    report = await call(config, "scan_project", path="game")
    conflict = next(c for c in report["conflicts"] if c["field"] == "game.name")
    assert conflict["current"] == "Fort Night" and conflict["scanned"] == "Pillow Fort Panic"
    text = path.read_text("utf-8")
    assert "name: Fort Night  # final name" in text  # value and comment kept


async def test_rescan_updates_drafts_but_not_approved(workspace: Path) -> None:
    config = Config(workspace_root=workspace)
    await call(config, "init_project", path="game")
    report = await call(config, "scan_project", path="game")
    assert report["summary"]["applied"] == 0 and report["summary"]["conflicts"] == 0


async def test_paths_outside_the_workspace_are_refused(workspace: Path) -> None:
    async with Client(create_server(Config(workspace_root=workspace / "game"))) as client:
        result = await client.call_tool("init_project", {"path": "../"})
    assert result.is_error
    assert "outside the workspace root" in getattr(result.content[0], "text", "")


async def test_scan_without_engine_project(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    config = Config(workspace_root=tmp_path)
    out = await call(config, "init_project", path="empty")
    assert out["scan"]["scanners"] == []


async def test_server_info_has_no_secrets(tmp_path: Path) -> None:
    config = Config(workspace_root=tmp_path, publisher_key="SECRETKEY0123456789", http_token=TOKEN)
    info = await call(config, "server_info")
    assert info["publisher_key_configured"] is True
    assert "SECRETKEY" not in str(info) and TOKEN not in str(info)
    assert "SECRETKEY" not in repr(config)


# ------------------------------------------------------------------------------------------------ config


def test_dotenv_parser() -> None:
    env = parse_dotenv("# c\nA=1\nexport B=\"two words\"\nC=three # note\nD='x # y'\nnot a line\n")
    assert env == {"A": "1", "B": "two words", "C": "three", "D": "x # y"}


def test_environment_wins_over_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("STEAMWORKS_MCP_TOKEN=from-file\nSTEAM_MCP_BROWSER=1\n")
    c = load_config({"STEAMWORKS_MCP_TOKEN": "from-env"}, dotenv)
    assert c.http_token == "from-env" and c.browser_enabled


def test_resolve_in_workspace(tmp_path: Path) -> None:
    assert resolve_in_workspace(tmp_path, "a/b") == (tmp_path / "a" / "b").resolve()
    with pytest.raises(WorkspaceError):
        resolve_in_workspace(tmp_path / "a", str(tmp_path))


# ------------------------------------------------------------------------------------------------ HTTP


def test_http_refuses_unsafe_setups(tmp_path: Path) -> None:
    assert any("STEAMWORKS_MCP_TOKEN" in p for p in http_problems(Config(workspace_root=tmp_path), "127.0.0.1"))
    assert any("at least" in p for p in http_problems(Config(workspace_root=tmp_path, http_token="short"), "127.0.0.1"))
    assert any(
        "ALLOWED_HOSTS" in p for p in http_problems(Config(workspace_root=tmp_path, http_token=TOKEN), "0.0.0.0")
    )
    browser = Config(workspace_root=tmp_path, http_token=TOKEN, browser_enabled=True)
    assert any("BROWSER_REMOTE" in p for p in http_problems(browser, "127.0.0.1"))
    assert http_problems(Config(workspace_root=tmp_path, http_token=TOKEN), "127.0.0.1") == []


def test_cli_exits_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("STEAMWORKS_MCP_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["--http"]) == 2
    assert "STEAMWORKS_MCP_TOKEN" in capsys.readouterr().err


@pytest.fixture
def http_server(tmp_path: Path) -> Iterator[str]:
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = build_http_app(Config(workspace_root=tmp_path, http_token=TOKEN), "127.0.0.1", port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def test_http_requires_the_token_and_a_known_host(http_server: str) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    assert httpx.post(http_server, json=body, headers=headers).status_code == 401
    assert httpx.post(http_server, json=body, headers={**headers, "authorization": "Bearer wrong"}).status_code == 401
    ok = httpx.post(http_server, json=body, headers={**headers, "authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200, ok.text
    evil = httpx.post(
        http_server, json=body, headers={**headers, "authorization": f"Bearer {TOKEN}", "host": "evil.example"}
    )
    assert evil.status_code == 421


async def test_resources_and_prompts(workspace: Path) -> None:
    config = Config(workspace_root=workspace)
    await call(config, "init_project", path="game", scan=False)
    async with Client(create_server(config)) as client:
        templates = {t.uri_template for t in (await client.list_resource_templates()).resource_templates}
        assert {"steam://gates/{n}", "steam://manifest/{project}", "steam://style-guide/{genre}"} <= templates
        gate = await client.read_resource("steam://gates/1")
        assert '"rules"' in gate.contents[0].text  # type: ignore[union-attr]
        manifest = await client.read_resource("steam://manifest/game")
        assert '"values"' in manifest.contents[0].text  # type: ignore[union-attr]
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert {"release_assistant", "write_store_page", "design_achievements", "review_gate"} <= prompts
        msg = await client.get_prompt("review_gate", {"path": "game", "gate": "2"})
        assert "gap_report(gate=2)" in msg.messages[0].content.text  # type: ignore[union-attr]
