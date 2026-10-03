"""steamworks-mcp setup and doctor: the settings file and the apps' config files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steamworks_mcp import setup_cli
from steamworks_mcp.config import load_config, parse_dotenv

COMMAND = ["C:\\Program Files\\Python313\\python.exe", "-m", "steamworks_mcp"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "home" / "AppData" / "Roaming"))
    monkeypatch.setenv("STEAMWORKS_MCP_HOME", str(tmp_path / "home" / ".steamworks-mcp"))
    for var in ("STEAMWORKS_MCP_ROOT", "STEAMWORKS_PUBLISHER_KEY", "STEAM_MCP_BROWSER", "STEAMCMD_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)  # no real `claude` CLI
    (tmp_path / "home").mkdir()
    monkeypatch.chdir(tmp_path)  # no .env of the repository
    return tmp_path / "home"


def test_settings_keep_comments_and_windows_paths(tmp_path: Path) -> None:
    path = tmp_path / "settings.env"
    path.write_text("# mine\nSTEAM_MCP_BROWSER=0\nOTHER=x\n", "utf-8")
    setup_cli.write_settings(path, {"STEAM_MCP_BROWSER": "1", "STEAMWORKS_MCP_ROOT": "C:\\new games\\here"})
    text = path.read_text("utf-8")
    assert text.startswith("# mine\nSTEAM_MCP_BROWSER=1\nOTHER=x\n")
    assert parse_dotenv(text)["STEAMWORKS_MCP_ROOT"] == "C:\\new games\\here"


def test_settings_file_is_the_lowest_layer(tmp_path: Path) -> None:
    settings = tmp_path / "settings.env"
    settings.write_text(f"STEAMWORKS_MCP_ROOT={tmp_path}\nSTEAM_MCP_BROWSER=1\n", "utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("STEAM_MCP_BROWSER=0\n", "utf-8")
    config = load_config({}, dotenv, settings)
    assert config.workspace_root == tmp_path.resolve() and not config.browser_enabled


def test_json_config_is_merged_with_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}), "utf-8")
    assert setup_cli.add_to_json(path, COMMAND).startswith("added to")
    data = json.loads(path.read_text("utf-8"))
    assert data["theme"] == "dark" and set(data["mcpServers"]) == {"other", "steamworks"}
    assert data["mcpServers"]["steamworks"] == {"command": COMMAND[0], "args": COMMAND[1:]}
    assert (tmp_path / "claude_desktop_config.json.bak").is_file()
    assert setup_cli.add_to_json(path, COMMAND).startswith("already set up")


def test_broken_json_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{ not json", "utf-8")
    assert setup_cli.add_to_json(path, COMMAND).startswith("left alone")
    assert path.read_text("utf-8") == "{ not json"


def test_codex_table_is_appended_once(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "x"\n', "utf-8")
    setup_cli.add_to_codex(path, COMMAND)
    text = path.read_text("utf-8")
    assert text.startswith('model = "x"\n\n[mcp_servers.steamworks]\n')
    assert 'command = "C:\\\\Program Files\\\\Python313\\\\python.exe"' in text
    assert setup_cli.add_to_codex(path, COMMAND).startswith("already set up")
    import tomllib

    assert tomllib.loads(text)["mcp_servers"]["steamworks"]["args"] == ["-m", "steamworks_mcp"]


def test_setup_without_questions(home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    games = tmp_path / "games"
    (games / "MyGame" / "ProjectSettings").mkdir(parents=True)
    (games / "MyGame" / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0\n", "utf-8")
    code = setup_cli.setup(["--root", str(games), "--client", "cursor", "--yes"])
    assert code == 0
    settings = parse_dotenv((home / ".steamworks-mcp" / "settings.env").read_text("utf-8"))
    assert settings["STEAMWORKS_MCP_ROOT"] == str(games.resolve())
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text("utf-8"))
    assert cursor["mcpServers"]["steamworks"]["args"][-1] in ("steamworks_mcp", "steamworks-mcp")
    out = capsys.readouterr().out
    assert "0 tracked, 1 not tracked yet" in out and "Cursor: added to" in out


def test_setup_asks_and_saves_keys(home: Path, tmp_path: Path) -> None:
    answers = iter([str(tmp_path), "y", "n", "n", "n", "n"])
    code = setup_cli.setup([], ask=lambda _: next(answers), secret=lambda _: "KEY0123456789")
    assert code == 0
    settings = parse_dotenv((home / ".steamworks-mcp" / "settings.env").read_text("utf-8"))
    assert settings["STEAMWORKS_PUBLISHER_KEY"] == "KEY0123456789" and settings["STEAM_MCP_BROWSER"] == "1"


def test_doctor_offline(home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    setup_cli.write_settings(home / ".steamworks-mcp" / "settings.env", {"STEAMWORKS_MCP_ROOT": str(tmp_path)})
    code = setup_cli.doctor(["--offline"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "[ok]  Games folder:" in out and "Publisher Web API key not set" in out
    assert "KEY" not in out and out.isascii()  # Windows consoles (cp1252) print ASCII only
