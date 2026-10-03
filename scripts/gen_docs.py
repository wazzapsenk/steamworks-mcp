"""Regenerate the generated docs: docs/CAPABILITIES.md and the JSON Schemas in docs/schema/.

uv run python scripts/gen_docs.py          # write
uv run python scripts/gen_docs.py --check  # exit 1 if anything is out of date (used by the tests)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from steamworks_mcp.capabilities import render_markdown
from steamworks_mcp.gates.models import GateFile
from steamworks_mcp.manifest.drafts import Draft
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.manifest.state import State

ROOT = Path(__file__).resolve().parents[1]


def outputs() -> dict[Path, str]:
    def schema(model: type, title: str) -> str:
        data = model.model_json_schema()  # type: ignore[attr-defined]
        data = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": title, **data}
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    return {
        ROOT / "docs" / "CAPABILITIES.md": render_markdown(),
        ROOT / "docs" / "schema" / "steamworks.schema.json": schema(Manifest, "steamworks.yaml"),
        ROOT / "docs" / "schema" / "state.schema.json": schema(State, ".steam-mcp/state.json"),
        ROOT / "docs" / "schema" / "draft.schema.json": schema(Draft, ".steam-mcp/drafts/<field>/<id>.json"),
        ROOT / "docs" / "schema" / "gate.schema.json": schema(GateFile, "data/gates/gate_N.yaml"),
    }


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("out of date (run: uv run python scripts/gen_docs.py):", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
