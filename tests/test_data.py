"""Bundled data files are valid, consistent with each other and with the schema, and generated docs are current."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client

from steamworks_mcp import __version__
from steamworks_mcp.capabilities import capabilities
from steamworks_mcp.config import Config
from steamworks_mcp.data import data_files, load_yaml
from steamworks_mcp.gates.models import AssetCheck, GateFile, StoreRules
from steamworks_mcp.languages import all_languages, find_language
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import ManifestFile
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.server import create_server
from steamworks_mcp.validate.rules_data import AssetSpecsFile, EventsFile, StoreRulesFile

ROOT = Path(__file__).resolve().parents[1]
GATE_FILES = data_files("gates")


def gates() -> list[GateFile]:
    return [GateFile.model_validate(load_yaml(f)) for f in GATE_FILES]


def test_four_gates() -> None:
    assert [g.gate for g in gates()] == [0, 1, 2, 3]
    assert [f"gates/gate_{n}.yaml" for n in range(4)] == GATE_FILES


def test_rule_ids_are_unique_across_gates() -> None:
    ids = [r.id for g in gates() for r in g.rules]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("gate", range(4))
def test_gate_rules_reference_real_fields(gate: int) -> None:
    g = gates()[gate]
    bad = [p for p in g.field_refs() if not fp.is_valid(p)]
    assert not bad


def test_gate_rules_reference_known_assets_and_store_rules() -> None:
    assets = {a.id for a in AssetSpecsFile.model_validate(load_yaml("asset_specs.yaml")).assets}
    rules = {r.id for r in StoreRulesFile.model_validate(load_yaml("store_rules.yaml")).rules}
    for g in gates():
        for r in g.rules:
            if isinstance(r.check, AssetCheck):
                assert r.check.asset in assets, r.id
            if isinstance(r.check, StoreRules):
                assert set(r.check.rules) <= rules, r.id


def test_every_valve_rule_cites_a_page_and_quote() -> None:
    for g in gates():
        for r in g.rules:
            if r.origin == "valve" and r.check.kind != "info":
                assert r.source_doc, r.id


def test_required_assets_belong_to_a_gate() -> None:
    specs = AssetSpecsFile.model_validate(load_yaml("asset_specs.yaml"))
    assert len({a.id for a in specs.assets}) == len(specs.assets)
    for a in specs.assets:
        if a.required:
            assert a.gate in (1, 2), a.id


def test_store_rules() -> None:
    rules = StoreRulesFile.model_validate(load_yaml("store_rules.yaml")).rules
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    short = next(r for r in rules if r.id == "short_description_max_length")
    assert short.check == "deterministic" and short.params == {"max": 300} and short.severity == "error"


def test_events() -> None:
    data = EventsFile.model_validate(load_yaml("events.yaml"))
    ids = [e.id for e in data.events]
    assert len(ids) == len(set(ids))
    for e in data.events:
        if e.starts and e.ends:
            assert str(e.starts)[:10] <= str(e.ends)[:10], e.id
    assert any(e.kind == "next_fest" for e in data.events)


def test_capabilities() -> None:
    caps = capabilities()
    ids = [a.id for a in caps.areas]
    assert len(ids) == len(set(ids))
    publishing = next(a for a in caps.areas if a.id == "publishing")
    assert publishing.default_mode == "MANUAL" and publishing.browser.status == "never"


def test_languages() -> None:
    langs = all_languages()
    assert len({lang.api for lang in langs}) == len(langs) == 31
    assert find_language("Korean") is not None and find_language("korean").api == "koreana"  # type: ignore[union-attr]


def test_example_manifest_is_valid() -> None:
    m = ManifestFile.load(ROOT / "examples" / "example-game" / "steamworks.yaml").manifest
    assert m.apps.demo is not None


def test_schema_doc_covers_every_section() -> None:
    text = (ROOT / "docs" / "SCHEMA.md").read_text(encoding="utf-8")
    for name in Manifest.model_fields:
        if name != "schema_version":
            assert f"`{name}`" in text or f"`{name}`," in text, name


def test_generated_docs_are_current() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_docs.py"), "--check"], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_generated_skills_are_current() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_skills.py"), "--check"], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_generated_cursor_rules_are_current() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_rules.py"), "--check"], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_handwritten_skills_name_every_tool_of_their_prompt() -> None:
    spec = importlib.util.spec_from_file_location("gen_skills", ROOT / "scripts" / "gen_skills.py")
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    async def tools_and_prompts() -> tuple[set[str], dict[str, str]]:
        async with Client(create_server(Config(workspace_root=ROOT))) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            found = await gen.prompts()
        return tools, {name: text for name, _, _, _, text in found}

    tools, prompts = anyio.run(tools_and_prompts)
    assert set(prompts) == set(gen.NAMES)
    for name in gen.HANDWRITTEN:
        skill = (ROOT / "skills" / gen.NAMES[name] / "SKILL.md").read_text(encoding="utf-8")
        used = {t for t in tools if re.search(rf"\b{t}\b", prompts[name])}
        assert used and not {t for t in used if not re.search(rf"\b{t}\b", skill)}, name


def test_plugin_manifests_agree() -> None:
    """The Claude Code plugin, its marketplace entry and the Cursor plugin describe the same release."""
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    cursor = json.loads((ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = re.sub(r"\.dev\d+$", "", __version__)
    entry = market["plugins"][0]
    assert claude["version"] == cursor["version"] == entry["version"] == version
    assert claude["name"] == cursor["name"] == entry["name"] and entry["source"] == "./"
    assert claude["description"] == cursor["description"]
    for manifest, root_var in ((claude, "${CLAUDE_PLUGIN_ROOT}"), (cursor, "${CURSOR_PLUGIN_ROOT}")):
        server = manifest["mcpServers"]["steamworks"]
        assert server["command"] == "uv" and server["args"] == [
            "run",
            "--quiet",
            "--project",
            root_var,
            "steamworks-mcp",
        ]
    assert (ROOT / cursor["skills"]).is_dir() and any((ROOT / cursor["rules"]).glob("*.mdc"))


def test_mcp_registry_entry_matches_the_package() -> None:
    """server.json (the MCP Registry entry) names this package and version, and the README proves ownership."""
    import tomllib

    entry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    package = entry["packages"][0]
    assert entry["version"] == package["version"] == project["version"] == __version__
    assert package["registryType"] == "pypi" and package["identifier"] == project["name"]
    assert package["transport"] == {"type": "stdio"}
    assert len(entry["description"]) <= 100 and len(entry["title"]) <= 100
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {entry['name']} -->" in readme
