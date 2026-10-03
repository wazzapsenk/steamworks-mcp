"""The README's numbers and lists match the server, the skills and the rules."""

from __future__ import annotations

import re
from pathlib import Path

import anyio
from mcp import Client

from steamworks_mcp.config import Config
from steamworks_mcp.data import load_yaml
from steamworks_mcp.gates.engine import gate_files
from steamworks_mcp.server import create_server
from steamworks_mcp.validate.code import rules_file
from steamworks_mcp.validate.rules_data import StoreRulesFile

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def tools() -> set[str]:
    async def names() -> set[str]:
        async with Client(create_server(Config(workspace_root=ROOT, browser_enabled=True))) as client:
            return {t.name for t in (await client.list_tools()).tools}

    return anyio.run(names)


def number(label: str) -> int:
    m = re.search(rf"\*?\*?(\d+) {label}\b", README)
    assert m, label
    return int(m.group(1))


def test_counts() -> None:
    skills = [p for p in (ROOT / "skills").iterdir() if p.is_dir()]
    assert number("tools") == len(tools())
    assert number("skills") == len(skills) == number("recipes")
    assert number("code rules") == len(rules_file().rules)
    assert number("release checks") == sum(len(g.rules) for g in gate_files())
    assert number("store-text rules") == len(StoreRulesFile.model_validate(load_yaml("store_rules.yaml")).rules)


def test_every_tool_skill_and_rule_group_is_listed() -> None:
    missing = [t for t in sorted(tools()) if f"`{t}`" not in README]
    missing += [p.name for p in sorted((ROOT / "skills").iterdir()) if p.is_dir() and f"`{p.name}`" not in README]
    missing += [g.id for g in rules_file().groups if f"`{g.id}`" not in README]
    assert not missing, missing


def test_local_links_exist() -> None:
    for doc in (ROOT / "README.md", ROOT / "docs" / "ADVANCED.md"):
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            if not target.startswith(("http://", "https://", "mailto:")):
                assert (doc.parent / target).exists(), f"{doc.name}: {target}"
