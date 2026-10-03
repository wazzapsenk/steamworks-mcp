"""Files of a game project: ``steamworks.yaml`` (comment-preserving), ``.steam-mcp/state.json``, drafts.

Layout::

    <game>/
    ├── steamworks.yaml              values; edited by the user and by the server
    ├── localization/<lang>.yaml     translations (flat "path: text")
    └── .steam-mcp/
        ├── .gitignore               written by init_project
        ├── state.json               per-field metadata (committed)
        ├── drafts/<field>/<id>.json text alternatives (committed)
        ├── scan/  exports/  snapshots/  cache/  audit.jsonl   (not committed)
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.drafts import Draft
from steamworks_mcp.manifest.models import Manifest
from steamworks_mcp.manifest.state import State

MANIFEST_NAME = "steamworks.yaml"
STATE_DIR = ".steam-mcp"

STATE_GITIGNORE = """\
# Written by steamworks-mcp. state.json and drafts/ are meant to be committed.
scan/
exports/
snapshots/
cache/
audit.jsonl
"""

ROOT_GITIGNORE_SUGGESTIONS = (".env",)
"""Lines ``init_project`` suggests for the project's own .gitignore (it never edits that file itself)."""


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectFiles:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def localization_dir(self) -> Path:
        return self.root / "localization"

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def state_gitignore(self) -> Path:
        return self.state_dir / ".gitignore"

    @property
    def drafts_dir(self) -> Path:
        return self.state_dir / "drafts"

    @property
    def scan_dir(self) -> Path:
        return self.state_dir / "scan"

    @property
    def snapshots_dir(self) -> Path:
        return self.state_dir / "snapshots"

    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "cache"

    @property
    def audit_log(self) -> Path:
        return self.state_dir / "audit.jsonl"

    def export_dir(self, gate: int) -> Path:
        return self.state_dir / "exports" / f"gate_{gate}"

    def draft_dir(self, field: str) -> Path:
        fp.split(field)
        return self.drafts_dir / field


def _yaml() -> YAML:
    y = YAML()  # round-trip: keeps comments, key order and quoting
    y.preserve_quotes = True
    y.default_flow_style = False
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def format_errors(err: ValidationError, raw: Any) -> list[str]:
    """Pydantic errors as ``field.path: message``, with keyed-list indexes replaced by their keys."""
    out = []
    for e in err.errors():
        segs: list[str] = []
        node: fp.Node | None = fp.ROOT
        cur = raw
        for loc in e["loc"]:
            seg = str(loc)
            if node is not None and node.kind == "keyed_list" and isinstance(loc, int) and isinstance(cur, list):
                item = cur[loc] if loc < len(cur) else None
                if isinstance(item, dict) and node.key in item:
                    seg = str(item[node.key])
            segs.append(seg)
            node, cur = _descend(node, cur, loc)
        where = ".".join(segs) or "(root)"
        out.append(f"{where}: {e['msg']}")
    return out


def _descend(node: fp.Node | None, cur: Any, loc: Any) -> tuple[fp.Node | None, Any]:
    if node is None:
        return None, None
    nxt: Any = None
    if isinstance(cur, dict):
        nxt = cur.get(loc)
    elif isinstance(cur, list) and isinstance(loc, int) and loc < len(cur):
        nxt = cur[loc]
    if node.kind in ("model", "unit") and node.model is not None:
        return fp.children(node.model).get(str(loc)), nxt
    if node.kind in ("keyed_list", "index_list", "dict"):
        return node.item, nxt
    return None, nxt


class ManifestFile:
    """``steamworks.yaml`` with its comments. Every change is validated before it is kept."""

    def __init__(self, path: Path, raw: CommentedMap, manifest: Manifest) -> None:
        self.path = path
        self.raw = raw
        self.manifest = manifest

    @classmethod
    def parse(cls, path: Path, text: str) -> ManifestFile:
        try:
            raw = _yaml().load(text) or CommentedMap()
        except Exception as exc:
            raise ManifestError(f"{path.name} is not valid YAML: {exc}") from exc
        if not isinstance(raw, CommentedMap):
            raise ManifestError(f"{path.name} must contain a mapping at the top level")
        try:
            manifest = Manifest.model_validate(_plain(raw))
        except ValidationError as exc:
            lines = "\n".join(f"  - {m}" for m in format_errors(exc, _plain(raw)))
            raise ManifestError(f"{path.name} is invalid:\n{lines}") from exc
        return cls(path, raw, manifest)

    @classmethod
    def load(cls, path: Path) -> ManifestFile:
        if not path.exists():
            raise ManifestError(f"No {path.name} in {path.parent}. Run init_project first.")
        return cls.parse(path, path.read_text(encoding="utf-8"))

    def values(self) -> dict[str, Any]:
        """JSON-like values (what field paths, hashes and state reconciliation work on)."""
        return self.manifest.model_dump(mode="json")

    def get(self, field: str) -> Any:
        return fp.get(self.values(), field)

    def set_many(self, changes: Iterable[tuple[str, Any]]) -> None:
        """Apply several changes atomically: either all validate, or nothing changes."""
        candidate = copy.deepcopy(self.raw)
        for field, value in changes:
            fp.set_in(candidate, field, _to_yaml(value), new_map=CommentedMap)
        try:
            manifest = Manifest.model_validate(_plain(candidate))
        except ValidationError as exc:
            raise ManifestError("; ".join(format_errors(exc, _plain(candidate)))) from exc
        self.raw, self.manifest = candidate, manifest

    def set(self, field: str, value: Any) -> None:
        self.set_many([(field, value)])

    def dumps(self) -> str:
        buf = StringIO()
        _yaml().dump(self.raw, buf)
        return buf.getvalue()

    def save(self) -> None:
        atomic_write(self.path, self.dumps())


def _plain(data: Any) -> Any:
    """ruamel containers -> plain dict/list (pydantic validates those)."""
    if isinstance(data, dict):
        return {str(k): _plain(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_plain(v) for v in data]
    return data


def _to_yaml(value: Any) -> Any:
    if isinstance(value, dict):
        m = CommentedMap()
        for k, v in value.items():
            m[k] = _to_yaml(v)
        return m
    if isinstance(value, list):
        return [_to_yaml(v) for v in value]
    return value


def new_manifest_text(name: str | None = None, appid: int | None = None) -> str:
    """Starting content of ``steamworks.yaml`` for ``init_project``."""
    header = (
        "# steamworks.yaml: what your game needs on Steam, as plain values. Edit freely.\n"
        "# Field status (draft / approved / applied) is tracked in .steam-mcp/state.json.\n"
        "# Schema: docs/SCHEMA.md in the steamworks-mcp repository.\n"
    )
    raw = CommentedMap()
    raw["schema_version"] = 1
    game = CommentedMap()
    if name:
        game["name"] = name
    raw["game"] = game
    raw["source_language"] = "english"
    raw["target_languages"] = []
    main = CommentedMap()
    if appid:
        main["appid"] = appid
    apps = CommentedMap()
    apps["main"] = main
    raw["apps"] = apps
    Manifest.model_validate(_plain(raw))
    buf = StringIO()
    _yaml().dump(raw, buf)
    return header + buf.getvalue()


# ----------------------------------------------------------------------------------------------- state & drafts


def load_state(files: ProjectFiles) -> State:
    if not files.state_file.exists():
        return State()
    return State.model_validate_json(files.state_file.read_text(encoding="utf-8"))


def save_state(files: ProjectFiles, state: State) -> None:
    data = state.model_dump(mode="json", exclude_defaults=False)
    data["fields"] = dict(sorted(data["fields"].items()))
    atomic_write(files.state_file, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def save_draft(files: ProjectFiles, draft: Draft) -> Path:
    path = files.draft_dir(draft.field) / f"{draft.id}.json"
    atomic_write(path, draft.model_dump_json(indent=2) + "\n")
    return path


def load_drafts(files: ProjectFiles, field: str) -> list[Draft]:
    folder = files.draft_dir(field)
    if not folder.is_dir():
        return []
    drafts = [Draft.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    return sorted(drafts, key=lambda d: d.created_at)


# ----------------------------------------------------------------------------------------------- v0.1 import

_V01_REQ = {"soundCard": "sound_card", "vrSupport": "vr_support", "additionalNotes": "additional_notes"}


def from_v01(old: dict[str, Any]) -> dict[str, Any]:
    """Convert a v0.1 (TypeScript) ``steamworks.yaml`` into this schema."""
    store = old.get("store") or {}
    art = store.get("art") or {}
    new: dict[str, Any] = {
        "schema_version": 1,
        "game": {"name": old.get("name")},
        "source_language": old.get("sourceLanguage", "english"),
        "target_languages": list(old.get("targetLanguages") or []),
        "store": {
            "short_description": store.get("shortDescription") or None,
            "about": store.get("about") or None,
            "tags": list(store.get("tags") or []),
            "supported_languages": {
                lang: {
                    "interface": s.get("interface", True),
                    "full_audio": s.get("fullAudio", False),
                    "subtitles": s.get("subtitles", False),
                }
                for lang, s in (store.get("supportedLanguages") or {}).items()
            },
            "system_requirements": {
                os_name: {
                    tier: {_V01_REQ.get(k, k): v for k, v in (reqs or {}).items()}
                    for tier, reqs in (plat or {}).items()
                }
                for os_name, plat in (store.get("systemRequirements") or {}).items()
            },
        },
        "assets": {
            "key_art": art.get("keyArt"),
            "logo": art.get("logo"),
            "screenshots_dir": store.get("screenshotsDir", "store/screenshots"),
            "overrides": dict(art.get("overrides") or {}),
        },
        "achievements": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "description": a.get("description") or None,
                "hidden": a.get("hidden", False),
                "icon": a.get("icon"),
                "icon_locked": a.get("iconLocked"),
                **({"progress": a["progress"]} if a.get("progress") else {}),
            }
            for a in old.get("achievements") or []
        ],
        "apps": {"main": {"appid": old.get("appId")}},
    }
    main = new["apps"]["main"]
    cloud = old.get("cloud")
    if cloud:
        auto = cloud.get("autoCloud") or {}
        main["cloud"] = {
            "byte_quota": cloud.get("byteQuota"),
            "file_quota": cloud.get("fileQuota"),
            "shared_appid": cloud.get("sharedAppId"),
            "developers_only": cloud.get("developersOnly"),
            "sync_on_suspend": cloud.get("syncOnSuspend"),
            "auto_cloud": [
                {
                    "root": r["root"],
                    "subdirectory": r.get("subdirectory", ""),
                    "pattern": r.get("pattern", "*"),
                    "os": r.get("os", "all"),
                    "recursive": r.get("recursive", False),
                }
                for r in auto.get("roots") or []
            ],
            "overrides": [
                {
                    "root": o["originalRoot"],
                    "os": o["os"],
                    "use_instead": o["newRoot"],
                    "add_path": o.get("addOrReplacePath", ""),
                    "replace_path": o.get("replace", False),
                }
                for o in auto.get("rootOverrides") or []
            ],
        }
    app = old.get("app")
    if app:
        main["installation"] = {
            "install_folder": app.get("installFolder"),
            "launch_options": [
                {
                    "executable": lo["executable"],
                    "arguments": lo.get("arguments", ""),
                    "working_dir": lo.get("workingDir", ""),
                    "description": lo.get("description") or None,
                    "type": lo.get("type", "default"),
                    "os": lo.get("os", "windows"),
                    "arch": lo.get("arch", "all"),
                    "beta_key": lo.get("betaKey", ""),
                    "owns_dlc": lo.get("ownsDlc", ""),
                }
                for lo in app.get("launchOptions") or []
            ],
        }
    Manifest.model_validate(new)
    return new
