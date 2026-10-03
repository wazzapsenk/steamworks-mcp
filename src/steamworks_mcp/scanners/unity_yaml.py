"""Tolerant reader for Unity's serialized YAML (``%TAG !u!`` documents such as ProjectSettings.asset).

Each ``--- !u!<class> &<id>`` document is parsed on its own. When a document trips the YAML parser (Unity writes a
few constructs plain YAML dislikes), the top-level ``key: value`` lines are still recovered.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_DOC = re.compile(r"^--- !u!\d+ &-?\d+(?: stripped)?[ \t]*$", re.M)
_KV = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*): ?(.*)$")


def documents(text: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(type name, body), ...]``, e.g. ``[("PlayerSettings", {...})]``."""
    out: list[tuple[str, dict[str, Any]]] = []
    for chunk in _DOC.split(text)[1:]:
        data: Any
        try:
            data = YAML(typ="safe", pure=True).load(chunk)
        except Exception:
            data = _fallback(chunk)
        if isinstance(data, dict) and len(data) == 1:
            ((name, body),) = data.items()
            out.append((str(name), body if isinstance(body, dict) else {}))
    return out


def _fallback(chunk: str) -> dict[str, Any]:
    lines = chunk.strip("\n").splitlines()
    if not lines or not lines[0].endswith(":"):
        return {}
    body: dict[str, Any] = {}
    for line in lines[1:]:
        m = _KV.match(line)
        if m:
            body[m.group(1)] = m.group(2).strip()
    return {lines[0][:-1]: body}


def read(path: Path) -> list[tuple[str, dict[str, Any]]]:
    try:
        return documents(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def first(path: Path, type_name: str) -> dict[str, Any]:
    return next((body for name, body in read(path) if name == type_name), {})
