"""Reference data served by ``get_spec_info`` (and, later, as MCP resources)."""

from __future__ import annotations

from typing import Any

from steamworks_mcp.capabilities import capabilities
from steamworks_mcp.data import load_yaml
from steamworks_mcp.gates.engine import gate_files
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.references import bundled_analysis, catalog
from steamworks_mcp.references.patterns import store_patterns
from steamworks_mcp.style_guides import guide

KINDS = (
    "schema, gates, gate:<0-3>, capabilities, store_rules, asset_specs, events, references, reference:<appid>, "
    "style_guide:<id>, store_patterns, store_patterns:<Steam genre>"
)


def spec_info(kind: str) -> dict[str, Any]:
    kind = kind.strip()
    if kind == "schema":
        return {"json_schema": Manifest.model_json_schema()}
    if kind == "gates":
        return {
            "gates": [
                {"gate": g.gate, "title": g.title, "summary": g.summary, "rules": len(g.rules)} for g in gate_files()
            ]
        }
    if kind.startswith("gate:"):
        n = int(kind.split(":", 1)[1])
        g = next((g for g in gate_files() if g.gate == n), None)
        if g is None:
            raise ValueError(f"No gate {n}; gates are 0-3.")
        return g.model_dump(mode="json", by_alias=True, exclude_none=True)
    if kind == "capabilities":
        return capabilities().model_dump(mode="json")
    if kind in ("store_rules", "asset_specs", "events"):
        return dict(load_yaml(f"{kind}.yaml"))
    if kind == "references":
        return {"games": [g.model_dump() for g in catalog()]}
    if kind.startswith("reference:"):
        a = bundled_analysis(int(kind.split(":", 1)[1]))
        if a is None:
            raise ValueError("No bundled analysis for that app id; see get_spec_info('references').")
        return a.model_dump(mode="json")
    if kind.startswith("style_guide:"):
        sg = guide(kind.split(":", 1)[1])
        if sg is None:
            raise ValueError("Unknown style guide.")
        return {"meta": sg.meta.model_dump(), "guide": sg.body}
    if kind == "store_patterns" or kind.startswith("store_patterns:"):
        patterns = store_patterns()
        if patterns is None:
            raise ValueError("No store patterns bundled; run scripts/build_store_patterns.py.")
        if kind == "store_patterns":
            return patterns.model_dump(mode="json")
        genre = kind.split(":", 1)[1].strip()
        found = next((g for g in patterns.genres if g.lower() == genre.lower()), None)
        if found is None:
            raise ValueError(f"No pattern group for {genre!r}; groups: {', '.join(patterns.genres)}.")
        return {
            "genre": found,
            "recorded_on": patterns.recorded_on.isoformat(),
            **patterns.genres[found].model_dump(mode="json"),
        }
    raise ValueError(f'Unknown kind "{kind}". Kinds: {KINDS}.')
