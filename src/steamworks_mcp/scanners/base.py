"""Scanner plugin interface. A scanner reads a game project (never writes to it) and reports findings, each with
file + line evidence and a confidence. Findings become ``draft`` values with ``source: scan``; they never overwrite
values the user approved.

Third-party scanners can register through the ``steamworks_mcp.scanners`` entry-point group (an object or class
with ``name``, ``detect(root)`` and ``scan(root)``).
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from steamworks_mcp.manifest.state import Evidence

SKIP_DIRS = {
    "Library",
    "Temp",
    "Logs",
    "obj",
    "Build",
    "Builds",
    "UserSettings",
    ".git",
    ".vs",
    ".idea",
    "node_modules",
}
MAX_FILE_BYTES = 2_000_000


@dataclass
class Finding:
    field: str
    """Field path in steamworks.yaml (``achievements.ACH_WIN`` with ``kind="item"`` creates a keyed list item)."""
    value: Any
    confidence: float
    evidence: list[Evidence] = dataclasses.field(default_factory=list)
    kind: Literal["value", "item"] = "value"
    note: str = ""


@dataclass
class ScanWarning:
    code: str
    message: str
    evidence: list[Evidence] = dataclasses.field(default_factory=list)


@dataclass
class ScanResult:
    scanner: str
    findings: list[Finding] = dataclasses.field(default_factory=list)
    warnings: list[ScanWarning] = dataclasses.field(default_factory=list)
    facts: dict[str, Any] = dataclasses.field(default_factory=dict)
    """Raw observations kept in .steam-mcp/scan/ for generators (e.g. code achievements, save paths, art files)."""


class Scanner(Protocol):
    name: str

    def detect(self, root: Path) -> bool: ...

    def scan(self, root: Path) -> ScanResult: ...


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def ev(root: Path, path: Path, line: int | None = None, note: str = "") -> Evidence:
    return Evidence(file=rel(root, path), line=line, note=note)


def iter_files(root: Path, suffixes: tuple[str, ...], under: str = "") -> Iterator[Path]:
    """Files with the given suffixes, skipping build output, caches and VCS folders."""
    base = root / under if under else root
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(suffixes):
                p = Path(dirpath) / name
                try:
                    if p.stat().st_size <= MAX_FILE_BYTES:
                        yield p
                except OSError:
                    continue


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1
