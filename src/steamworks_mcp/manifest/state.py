"""Machine-owned field metadata, ``.steam-mcp/state.json``. Only the server writes it.

Status lifecycle of a field::

    missing --set(user)--------------------------> approved --mark_applied--> applied
       |                                              ^   |                      |
       +--set(scan|generated|reference_default)--> draft  |  value edited       | value edited
                                                     |    v                      v
                                                     +-> needs_review <----------+
                                                          (approve again)

* ``approved`` means the user confirmed the value. Scanned, generated and default values are never auto-approved.
* ``applied`` means the value is confirmed present in Steamworks (read back, or the user confirmed a manual step).
* If the value in ``steamworks.yaml`` changes after approval or application (hash mismatch), the field drops back to
  ``needs_review``. A value found in the file without any state was typed by the user, so it counts as approved.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from steamworks_mcp.manifest.paths import iter_fields

Status = Literal["missing", "draft", "needs_review", "approved", "applied"]
Source = Literal["scan", "user", "generated", "reference_default"]
ExecutionMode = Literal["API", "BROWSER", "ARTIFACT", "MANUAL"]

STATE_VERSION = 1


class TransitionError(ValueError):
    pass


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):  # a unit whose parts are all unknown is still missing
        return all(is_empty(v) for v in value.values())
    return False


def value_hash(value: Any) -> str:
    """Stable hash of a JSON-like value (dict key order does not matter)."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    """Path relative to the scanned project."""
    line: int | None = None
    note: str = ""


class FieldState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    status: Status = "missing"
    source: Source | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    gate: int | None = Field(default=None, ge=0, le=3)
    execution_mode: ExecutionMode | None = None
    value_hash: str | None = None
    """Hash of the value when the status was last set; compared on every load."""
    evidence: list[Evidence] = Field(default_factory=list)
    updated_at: dt.datetime | None = None
    applied_at: dt.datetime | None = None
    notes: str = ""


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_version: Literal[1] = 1
    fields: dict[str, FieldState] = Field(default_factory=dict)

    def get(self, path: str) -> FieldState:
        return self.fields.get(path) or FieldState()

    # ------------------------------------------------------------------ transitions

    def record_value(
        self,
        path: str,
        value: Any,
        source: Source,
        *,
        confidence: float | None = None,
        evidence: list[Evidence] | None = None,
        notes: str | None = None,
    ) -> FieldState:
        """A value was written to ``path``. Users' answers are approved; everything else is a draft."""
        fs = self.fields.get(path) or FieldState()
        if is_empty(value):
            fs.status, fs.value_hash = "missing", None
        else:
            fs.status = "approved" if source == "user" else "draft"
            fs.value_hash = value_hash(value)
        fs.source = source
        fs.confidence = 1.0 if source == "user" else (confidence if confidence is not None else fs.confidence)
        if evidence is not None:
            fs.evidence = evidence
        if notes is not None:
            fs.notes = notes
        fs.updated_at = now()
        fs.applied_at = None
        self.fields[path] = fs
        return fs

    def approve(self, path: str, value: Any) -> FieldState:
        fs = self.fields.get(path) or FieldState()
        if is_empty(value):
            raise TransitionError(f"{path}: nothing to approve, the field is empty")
        if fs.status == "applied" and fs.value_hash == value_hash(value):
            return fs
        fs.status, fs.value_hash, fs.updated_at = "approved", value_hash(value), now()
        fs.source = fs.source or "user"
        self.fields[path] = fs
        return fs

    def mark_applied(self, path: str, value: Any, *, at: dt.datetime | None = None) -> FieldState:
        fs = self.fields.get(path)
        if fs is None or fs.status not in ("approved", "applied"):
            status = fs.status if fs else "missing"
            raise TransitionError(f"{path}: only approved values can be marked applied (status is {status})")
        if fs.value_hash != value_hash(value):
            raise TransitionError(f"{path}: the value changed since it was approved; approve it again first")
        fs.status, fs.applied_at, fs.updated_at = "applied", at or now(), now()
        return fs

    def confirm_checklist(self, path: str, notes: str = "") -> FieldState:
        """A manual step (``checklist.<rule id>``) the user says is done in Steamworks."""
        if not path.startswith("checklist."):
            raise TransitionError(f"{path}: not a checklist item")
        at = now()
        fs = FieldState(status="applied", source="user", confidence=1.0, updated_at=at, applied_at=at, notes=notes)
        self.fields[path] = fs
        return fs

    # ------------------------------------------------------------------ reconcile with the values file

    def reconcile(self, values: dict[str, Any]) -> list[str]:
        """Bring statuses in line with the current values file; returns the paths whose status changed.

        ``values`` is ``Manifest.model_dump(mode="json")``.
        """
        changed: list[str] = []
        current = dict(iter_fields(values))
        for path, value in current.items():
            fs = self.fields.get(path)
            before = fs.status if fs else None
            if fs is None:
                if is_empty(value):
                    continue
                fs = FieldState(status="approved", source="user", confidence=1.0, value_hash=value_hash(value))
                fs.updated_at, fs.notes = now(), "found in steamworks.yaml without state"
                self.fields[path] = fs
            elif is_empty(value):
                fs.status, fs.value_hash, fs.applied_at = "missing", None, None
            elif fs.status == "missing":
                fs.status, fs.source, fs.value_hash = "approved", "user", value_hash(value)
                fs.confidence = 1.0
            elif fs.value_hash != value_hash(value):
                fs.status, fs.value_hash = "needs_review", value_hash(value)
            if fs.status != before:
                fs.updated_at = now()
                changed.append(path)
        for path, fs in self.fields.items():
            if path.startswith(("localization.", "checklist.")) or path in current:
                continue
            if fs.status != "missing":  # the value (e.g. an achievement) was removed from the file
                fs.status, fs.value_hash, fs.updated_at = "missing", None, now()
                changed.append(path)
        return changed
