"""Export packages: files Steam imports, images, SteamPipe scripts and the checklist."""

from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from steamworks_mcp.export.package import export_package, store_localization_json
from steamworks_mcp.fields import mark_applied, set_fields
from steamworks_mcp.localization.store import set_translations
from steamworks_mcp.project import Project

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "example-game"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "game"
    shutil.copytree(EXAMPLE, root)
    for rel, size in (
        ("store/art/keyart.png", (3840, 2160)),
        ("store/art/logo.png", (1200, 400)),
        ("achievements/first_fort.png", (512, 512)),
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", size, (90, 40, 160, 255)).save(root / rel)
    return Project.open(root)


def test_gate_1_package(project: Project) -> None:
    set_translations(
        project.values(),
        project.files.root,
        project.state,
        "german",
        {"store.short_description": "Baut Deckenburgen mit bis zu vier Freunden."},
    )
    out = export_package(project.values(), project.state, project.files, 1)
    folder = project.files.root / out["folder"]
    data = json.loads((folder / "store" / "store_localization.json").read_text("utf-8"))
    assert set(data["languages"]) == {"english", "german"}  # french/schinese have no text yet: left out, never blanked
    assert set(data["languages"]["english"]) == {"app[content][short_description]", "app[content][about]"}
    assert set(data["languages"]["german"]) == {"app[content][short_description]"}
    assert (folder / "store" / "german" / "short_description.txt").read_text("utf-8").startswith("Baut")
    with Image.open(folder / "images" / "header_capsule.png") as im:
        assert im.size == (920, 430)
    checklist = (folder / "CHECKLIST.md").read_text("utf-8")
    assert "- [x] **Steam tags**" in checklist and "usable screenshot" in checklist
    assert "Upload `images/header_capsule.png`" in checklist
    assert "mark_applied(['checklist.store_presence_checklist_complete'])" in checklist


def test_done_items_are_ticked(project: Project) -> None:
    mark_applied(project, ["checklist.store_presence_checklist_complete"])
    checklist = (
        project.files.root
        / export_package(project.values(), project.state, project.files, 1)["folder"]
        / "CHECKLIST.md"
    ).read_text("utf-8")
    assert "- [x] **Store presence checklist complete**" in checklist
    assert "- [x] **Steam tags**" in checklist  # passing rules are ticked too


def test_gate_2_package(project: Project) -> None:
    out = export_package(project.values(), project.state, project.files, 2)
    assert any("depot_id" in n for n in out["notes"])
    set_fields(project, {"apps.main.builds.depots.0.depot_id": 1000002})
    out = export_package(project.values(), project.state, project.files, 2)
    folder = project.files.root / out["folder"]
    readme = (folder / "steam" / "README.md").read_text("utf-8")
    assert "+login <builder account>" in readme and "by hand" in readme
    assert (folder / "steam" / "app_build_1000000.vdf").exists() and (
        folder / "steam" / "depot_build_1000002.vdf"
    ).exists()
    assert (folder / "achievements" / "ACH_FIRST_FORT_locked.jpg").exists()
    rows = list(csv.reader(io.StringIO((folder / "achievements" / "achievements.csv").read_text("utf-8-sig"))))
    assert rows[0] == ["api_name", "hidden", "field", "english", "german", "french", "schinese"]
    assert ["ACH_FRIENDLY_FIRE", "yes", "name", "It Was the Cat", "", "", ""] in rows
    checklist = (folder / "CHECKLIST.md").read_text("utf-8")
    assert "## Steam Cloud" in checklist and "WinAppDataLocalLow" in checklist and "## Achievements" in checklist


def test_reexport_cleans_old_files(project: Project) -> None:
    folder = project.files.root / export_package(project.values(), project.state, project.files, 1)["folder"]
    (folder / "stale.txt").write_text("old")
    export_package(project.values(), project.state, project.files, 1)
    assert not (folder / "stale.txt").exists()


def test_store_json_shape_matches_steam_import(project: Project) -> None:
    data = store_localization_json(project.values(), project.files.root, "2000000")
    assert data["itemid"] == "2000000" and list(data["languages"]) == ["english"]


def test_gates_0_and_3(project: Project) -> None:
    for gate in (0, 3):
        out = export_package(project.values(), project.state, project.files, gate)
        assert out["files"] == ["CHECKLIST.md"]
    with pytest.raises(ValueError):
        export_package(project.values(), project.state, project.files, 4)
