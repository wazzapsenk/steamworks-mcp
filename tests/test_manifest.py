from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.drafts import Draft
from steamworks_mcp.manifest.io import (
    ManifestError,
    ManifestFile,
    ProjectFiles,
    from_v01,
    load_drafts,
    load_state,
    new_manifest_text,
    save_draft,
    save_state,
)
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.manifest.state import State, TransitionError, value_hash

ROOT = Path(__file__).resolve().parents[1]


def manifest(**data: Any) -> Manifest:
    return Manifest.model_validate(data)


# ------------------------------------------------------------------------------------------------ models


def test_empty_manifest_is_valid() -> None:
    m = manifest()
    assert m.source_language == "english"
    assert m.apps.main.appid is None


@pytest.mark.parametrize("code", ["klingon", "Turkish", "zh-CN", "korean"])
def test_language_codes_must_be_steam_api_codes(code: str) -> None:
    with pytest.raises(ValidationError, match="Steam API language code"):
        manifest(target_languages=[code])


def test_language_error_suggests_the_api_code() -> None:
    with pytest.raises(ValidationError, match='Did you mean "koreana"'):
        manifest(target_languages=["korean"])


def test_source_language_not_in_targets() -> None:
    with pytest.raises(ValidationError, match="source language"):
        manifest(target_languages=["english"])


@pytest.mark.parametrize("name", ["ACH WIN", "ach-win", "ÄCH", ""])
def test_api_names(name: str) -> None:
    with pytest.raises(ValidationError):
        manifest(achievements=[{"id": name}])


def test_duplicate_achievement_ids_are_rejected() -> None:
    # Steamworks itself accepts duplicate API names (see docs/STEAMWORKS_INTERNALS.md), so this tool must not.
    with pytest.raises(ValidationError, match="duplicate achievement id"):
        manifest(achievements=[{"id": "ACH_A"}, {"id": "ACH_A"}])


def test_progress_must_use_a_defined_stat() -> None:
    with pytest.raises(ValidationError, match="unknown stat"):
        manifest(stats=[{"name": "WINS"}], achievements=[{"id": "ACH", "progress": {"stat": "KILLS", "max": 10}}])


@pytest.mark.parametrize(
    ("cloud", "message"),
    [
        ({"byte_quota": 10_000_000_001}, "less than or equal"),
        ({"file_quota": 10_001}, "less than or equal"),
        ({"auto_cloud": [{"root": "NotARealRoot", "pattern": "*"}]}, "Input should be"),
        ({"auto_cloud": [{"root": "WinAppDataLocalLow", "pattern": ""}]}, "at least 1 character"),
    ],
)
def test_cloud_values_steamworks_would_silently_accept(cloud: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        manifest(apps={"main": {"cloud": cloud}})


def test_launch_option_needs_an_executable() -> None:
    with pytest.raises(ValidationError):
        manifest(apps={"main": {"installation": {"launch_options": [{"executable": ""}]}}})


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        manifest(store={"shortDescription": "camelCase is v0.1"})


# ------------------------------------------------------------------------------------------------ field paths

SAMPLE = manifest(
    target_languages=["german"],
    store={"tags": ["Co-op"], "supported_languages": {"english": {}, "german": {"full_audio": False}}},
    achievements=[{"id": "ACH_WIN", "name": "Win"}],
    apps={"main": {"appid": 1000000, "installation": {"launch_options": [{"executable": "Game.exe"}]}}},
).model_dump(mode="json")


def test_iter_fields_addresses_keyed_indexed_dict_and_unit_fields() -> None:
    fields = dict(fp.iter_fields(SAMPLE))
    assert fields["achievements.ACH_WIN.name"] == "Win"
    assert "achievements.ACH_WIN.id" not in fields
    assert fields["apps.main.installation.launch_options.0.executable"] == "Game.exe"
    assert fields["store.supported_languages.german"] == {"interface": True, "full_audio": False, "subtitles": False}
    assert fields["store.tags"] == ["Co-op"]
    assert fields["store.short_description"] is None  # missing values are visible
    assert not any(p.startswith("apps.demo.") for p in fields)  # absent optional sections are not
    assert not any(p.startswith("store.system_requirements.windows") for p in fields)


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("store.short_description", True),
        ("achievements.*.icon", True),
        ("achievements.ACH_WIN.progress", True),
        ("apps.demo.cloud.byte_quota", True),
        ("apps.main.installation.launch_options.*.executable", True),
        ("apps.main.installation.launch_options.first.executable", False),
        ("store.supported_languages.*", True),
        ("store.nope", False),
        ("store..tags", False),
    ],
)
def test_schema_paths(path: str, ok: bool) -> None:
    assert fp.is_valid(path) is ok


def test_get_and_matches() -> None:
    assert fp.get(SAMPLE, "achievements.ACH_WIN.name") == "Win"
    assert fp.get(SAMPLE, "achievements.NOPE.name") is None
    assert fp.get(SAMPLE, "apps.main.installation.launch_options.0.executable") == "Game.exe"
    assert fp.matches("achievements.*.name", "achievements.ACH_WIN.name")
    assert not fp.matches("achievements.*.name", "achievements.ACH_WIN.description")


def test_set_in_creates_and_removes() -> None:
    data: dict[str, Any] = {}
    fp.set_in(data, "achievements.ACH_A.name", "A")
    fp.set_in(data, "achievements.ACH_A.hidden", True)
    fp.set_in(data, "apps.main.installation.launch_options.0", {"executable": "Game.exe"})
    fp.set_in(data, "store.supported_languages.german", {"interface": True})
    assert data["achievements"] == [{"id": "ACH_A", "name": "A", "hidden": True}]
    assert Manifest.model_validate(data).apps.main.installation.launch_options[0].executable == "Game.exe"
    fp.set_in(data, "achievements.ACH_A", None)
    assert data["achievements"] == []
    with pytest.raises(fp.FieldPathError, match="skips"):
        fp.set_in(data, "apps.main.installation.launch_options.5.executable", "x")


# ------------------------------------------------------------------------------------------------ state


def test_user_values_are_approved_everything_else_is_a_draft() -> None:
    s = State()
    assert s.record_value("store.short_description", "Hi", "user").status == "approved"
    assert s.record_value("store.about", "Generated", "generated", confidence=0.4).status == "draft"
    assert s.record_value("game.engine.name", "unity", "scan", confidence=0.9).status == "draft"
    assert s.record_value("store.tags", [], "user").status == "missing"


def test_approve_then_apply_then_edit_drops_to_needs_review() -> None:
    s = State()
    s.record_value("store.about", "v1", "generated")
    with pytest.raises(TransitionError, match="only approved"):
        s.mark_applied("store.about", "v1")
    s.approve("store.about", "v1")
    with pytest.raises(TransitionError, match="changed since"):
        s.mark_applied("store.about", "v2")
    assert s.mark_applied("store.about", "v1").status == "applied"
    changed = s.reconcile({"store": {"about": "v2"}})
    assert "store.about" in changed
    assert s.get("store.about").status == "needs_review"
    assert s.get("store.about").value_hash == value_hash("v2")


def test_reconcile_untracked_value_is_user_approved_and_removed_value_is_missing() -> None:
    s = State()
    s.reconcile(SAMPLE)
    assert s.get("achievements.ACH_WIN.name").status == "approved"
    assert s.get("achievements.ACH_WIN.name").source == "user"
    assert s.get("store.short_description").status == "missing"
    without = Manifest.model_validate({**SAMPLE, "achievements": []}).model_dump(mode="json")
    s.reconcile(without)
    assert s.get("achievements.ACH_WIN.name").status == "missing"


def test_value_hash_ignores_key_order() -> None:
    assert value_hash({"a": 1, "b": 2}) == value_hash({"b": 2, "a": 1})


# ------------------------------------------------------------------------------------------------ files

COMMENTED = """\
# my notes about this game
schema_version: 1
game:
  name: Example Game  # working title
source_language: english
target_languages: [german]
store:
  # keep this under 300 characters
  short_description: Old text.
"""


def test_set_keeps_comments_and_validates(tmp_path: Path) -> None:
    mf = ManifestFile.parse(tmp_path / "steamworks.yaml", COMMENTED)
    mf.set("store.short_description", "New text.")
    mf.set("achievements.ACH_WIN.name", "Win")
    out = mf.dumps()
    assert "# my notes about this game" in out
    assert "# working title" in out
    assert "# keep this under 300 characters" in out
    assert "short_description: New text." in out
    with pytest.raises(ManifestError, match="target_languages"):
        mf.set("target_languages", ["klingon"])
    assert mf.manifest.target_languages == ["german"]  # failed change left nothing behind


def test_invalid_file_reports_keyed_paths(tmp_path: Path) -> None:
    text = "achievements:\n  - id: ACH_A\n    hidden: maybe\n"
    with pytest.raises(ManifestError, match=r"achievements\.ACH_A\.hidden"):
        ManifestFile.parse(tmp_path / "steamworks.yaml", text)


def test_new_manifest_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "steamworks.yaml"
    path.write_text(new_manifest_text("Example Game", 1000000), encoding="utf-8")
    mf = ManifestFile.load(path)
    assert mf.manifest.game.name == "Example Game"
    assert mf.manifest.apps.main.appid == 1000000
    mf.set("store.tags", ["Co-op", "Party"])
    mf.save()
    assert ManifestFile.load(path).manifest.store.tags == ["Co-op", "Party"]


def test_state_and_drafts_persist(tmp_path: Path) -> None:
    files = ProjectFiles(tmp_path)
    s = State()
    s.record_value("store.short_description", "Hi", "user")
    save_state(files, s)
    assert load_state(files).get("store.short_description").status == "approved"
    save_draft(files, Draft(id="fantasy-1", field="store.short_description", value="A", strategy="fantasy"))
    save_draft(files, Draft(id="mechanic-1", field="store.short_description", value="B", strategy="mechanic"))
    assert [d.id for d in load_drafts(files, "store.short_description")] == ["fantasy-1", "mechanic-1"]
    assert (tmp_path / ".steam-mcp" / "drafts" / "store.short_description" / "fantasy-1.json").exists()


def test_v01_manifest_imports() -> None:
    old = YAML(typ="safe").load((ROOT / "legacy/ts/examples/demo-game/steamworks.yaml").read_text(encoding="utf-8"))
    new = Manifest.model_validate(from_v01(old))
    assert new.game.name == old["name"]
    assert [a.id for a in new.achievements] == [a["id"] for a in old["achievements"]]
    assert new.store.short_description
