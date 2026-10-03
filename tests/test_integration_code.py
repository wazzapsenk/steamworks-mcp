"""integration_code: SDK code built from steamworks.yaml, for each engine, passing check_code's own rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from steamworks_mcp import present
from steamworks_mcp.generate import integration
from steamworks_mcp.validate import code

VALUES: dict[str, Any] = {
    "game": {"engine": {"name": "unity"}},
    "apps": {"main": {"appid": 1234560}},
    "achievements": [
        {"id": "ACH_WIN", "name": "Winner", "description": "Win a match."},
        {"id": "ACH_TEN", "progress": {"stat": "WINS", "max": 10}},
    ],
    "stats": [{"name": "WINS", "type": "int"}, {"name": "BEST_TIME", "type": "float"}],
    "leaderboards": [{"name": "FASTEST", "sort_method": "ascending", "display_type": "seconds"}],
}


@pytest.mark.parametrize("target", list(integration.TARGETS))
def test_every_target_uses_the_names_and_passes_check_code(tmp_path: Path, target: str) -> None:
    out = integration.generate(VALUES, tmp_path, tmp_path / "out", None, target)
    text = "\n".join(out["files"].values())
    for name in ("ACH_WIN", "ACH_TEN", "WINS", "BEST_TIME", "FASTEST"):
        assert name in text, (target, name)
    assert "1234560" in text and "@@" not in text
    game = tmp_path / "game"
    for name, body in out["files"].items():
        if name.endswith((".cs", ".gd", ".h")):
            dest = game / ("Assets/Scripts" if name.endswith(".cs") else "src") / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
    findings = code.check_code(game, VALUES)["findings"]
    lifecycle = {"init_result_unchecked", "callbacks_not_run", "no_restart_check", "no_shutdown", "stats_not_stored"}
    assert not [f for f in findings if f["rule"] in lifecycle], (target, findings)
    assert all((tmp_path / "out" / target / name).is_file() for name in out["files"])


def test_target_from_the_engine_and_the_wrapper(tmp_path: Path) -> None:
    assert integration.detect_target({"game": {"engine": {"name": "godot"}}}, tmp_path) == "godot"
    assert integration.detect_target({}, tmp_path) == "cpp"
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Assets" / "Boot.cs").write_text("void A() { SteamClient.Init(1); }", encoding="utf-8")
    assert integration.detect_target(VALUES, tmp_path) == "unity-facepunch"


def test_features_and_notes(tmp_path: Path) -> None:
    out = integration.generate({"game": {"engine": {"name": "unity"}}}, tmp_path, tmp_path / "out", ["init", "cloud"])
    assert set(out["files"]) == {"SteamBootstrap.cs", "SaveLocation.cs"}
    assert any("480" in n for n in out["notes"])
    with pytest.raises(ValueError, match="Unknown feature"):
        integration.generate(VALUES, tmp_path, tmp_path / "out", ["overlay"])
    shown = present.integration_code({**out, "folder": ".steam-mcp/exports/code/unity-steamworks-net"})
    assert shown["summary"].startswith("Wrote 2 files for Unity + Steamworks.NET")
    assert "| SteamBootstrap.cs | Assets/Scripts/, on a GameObject in the first scene |" in shown["display"]
