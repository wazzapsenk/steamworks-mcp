"""Bundled data files (gates, store rules, events, capabilities, languages, asset specs)."""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Any

from ruamel.yaml import YAML


@cache
def load_yaml(name: str) -> Any:
    """Parse a bundled YAML file, e.g. ``load_yaml("languages.yaml")`` or ``load_yaml("gates/gate_1.yaml")``."""
    text = resources.files(__package__).joinpath(name).read_text(encoding="utf-8")
    return YAML(typ="safe", pure=True).load(text)


def data_files(folder: str) -> list[str]:
    """Names of the bundled files in ``folder`` (relative to the data package), sorted."""
    return sorted(f"{folder}/{p.name}" for p in resources.files(__package__).joinpath(folder).iterdir() if p.is_file())
