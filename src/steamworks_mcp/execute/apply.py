"""apply / restore_snapshot for the BROWSER mode, with the live-test protocol built in:

1. Every apply first reads Steamworks, saves a snapshot and checks the pages still look as recorded
   (``FormatError`` stops everything before a single write).
2. ``dry_run`` (the default) only shows the differences. Writing needs ``user_confirmed=True`` after the user saw them.
3. The first write on an app must be ``restore_snapshot`` of what was just read. That first restore writes every row
   back unchanged and reads it again (a round trip proving reads and writes line up on this app); until one worked,
   ``apply`` refuses to write.
4. Only approved values are written. Rows that exist only in Steamworks stay unless ``remove_extra`` is asked for.
5. After writing, everything is read back; matching fields become ``applied``; ``audit.jsonl`` gets an entry.
6. Nothing is ever published (see :mod:`steamworks_mcp.execute.guard`).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from steamworks_mcp.execute import store_page, sync
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport
from steamworks_mcp.manifest import paths as fp
from steamworks_mcp.manifest.io import ProjectFiles, atomic_write
from steamworks_mcp.manifest.state import State, TransitionError, now
from steamworks_mcp.media.images import prepare_achievement_icons

CONSENT_VERSION = 1
CONSENT_TEXT = """\
BROWSER mode drives the Steamworks partner site in a browser window that you log into yourself.

- It relies on undocumented behaviour of the partner site. Valve can change it at any time; the tool then stops
  instead of guessing.
- It acts with the permissions of the account you log in with. Use a separate Steamworks user that has only
  "Edit App Metadata" (and "Edit App Marketing Data" for store text), not an administrator.
- It never types or stores your password or Steam Guard codes and never reads cookie values.
- It never publishes, prepares to publish or reverts anything. Its writes are unpublished drafts that you review in
  the Publish tab; "Revert Changes" there undoes them.
- Valve does not document or endorse this kind of automation; using it is your decision and at your own risk.
"""


@dataclass
class Consent:
    path: Path

    def _read(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def accepted(self) -> bool:
        return self._read().get("version") == CONSENT_VERSION

    def accept(self) -> None:
        data = self._read()
        data.update({"version": CONSENT_VERSION, "accepted_at": now().isoformat()})
        atomic_write(self.path, json.dumps(data, indent=2) + "\n")

    def restore_verified(self, appid: int) -> bool:
        return str(appid) in self._read().get("restore_verified", {})

    def mark_restore_verified(self, appid: int) -> None:
        data = self._read()
        data.setdefault("restore_verified", {})[str(appid)] = now().isoformat()
        atomic_write(self.path, json.dumps(data, indent=2) + "\n")


class ApplyRefused(RuntimeError):
    pass


CONFIRM_NEXT = "Show these changes to the user. If they agree, call again with dry_run=false, user_confirmed=true."
NOT_APPROVED = "These values are not approved yet; show them to the user and approve_fields first."


async def read_section(t: Transport, section: str, appid: int) -> dict[str, Any]:
    if section == "cloud":
        return await P.read_cloud(t, appid)
    if section == "installation":
        return await P.read_installation(t, appid)
    if section == "achievements":
        return await P.read_achievements(t, appid)
    if section == "store_text":
        item = await P.store_item_id(t, appid)
        return {"item_id": item, **await P.read_store_localization(t, item)}
    if section == "store_page":
        return await store_page.read_section(t, appid)
    raise ValueError(f"section must be one of {', '.join(sync.SECTIONS)}")


def save_snapshot(files: ProjectFiles, app: str, appid: int, section: str, data: dict[str, Any]) -> str:
    taken = dt.datetime.now(dt.UTC)
    sid = f"{taken.strftime('%Y%m%dT%H%M%S%fZ')}-{app}-{section}"
    atomic_write(
        files.snapshots_dir / f"{sid}.json",
        json.dumps(
            {"id": sid, "taken_at": taken.isoformat(), "app": app, "appid": appid, "section": section, "data": data},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    return sid


def load_snapshot(files: ProjectFiles, sid: str) -> dict[str, Any]:
    p = files.snapshots_dir / f"{sid}.json"
    if not p.exists():
        known = sorted(x.stem for x in files.snapshots_dir.glob("*.json")) if files.snapshots_dir.exists() else []
        raise ValueError(f"No snapshot {sid}. Snapshots: {', '.join(known[-10:]) or 'none'}.")
    return dict(json.loads(p.read_text(encoding="utf-8")))


def audit(files: ProjectFiles, entry: dict[str, Any]) -> None:
    files.audit_log.parent.mkdir(parents=True, exist_ok=True)
    with files.audit_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now().isoformat(), **entry}, ensure_ascii=False, default=str) + "\n")


def _plan(
    section: str,
    appid: int,
    desired: Any,
    current: dict[str, Any],
    remove_extra: bool,
    icons: dict[str, tuple[bytes, bytes]] | None = None,
    force: bool = False,
) -> list[sync.Op]:
    if section == "cloud":
        return sync.plan_cloud(appid, desired, current, remove_extra, force)
    if section == "installation":
        return sync.plan_installation(appid, desired, current, remove_extra, force)
    if section == "achievements":
        return sync.plan_achievements(appid, desired, current, remove_extra, icons, force)
    if section == "store_page":
        return store_page.plan(current["item_id"], desired, current, force)
    return sync.plan_store(appid, current["item_id"], desired, current, force)


def desired_from_values(
    section: str,
    values: dict[str, Any],
    state: State,
    root: Path,
    app: str,
    current: dict[str, Any],
    remove_extra: bool = False,
) -> Any:
    if section == "store_page":
        return store_page.desired(values, current, remove_extra)
    if section == "cloud":
        return sync.desired_cloud(values, app, current)
    if section == "installation":
        return sync.desired_installation(
            values, app, current, sync.approved_translations(values, state, root, f"apps.{app}.installation.")
        )
    if section == "achievements":
        return sync.desired_achievements(values, sync.approved_translations(values, state, root, "achievements."))
    return sync.desired_store(values, sync.approved_translations(values, state, root, "store."))


def _icons(values: dict[str, Any], root: Path, out_dir: Path) -> dict[str, tuple[bytes, bytes]]:
    icons = {}
    for r in prepare_achievement_icons(values, root, out_dir):
        if r.file:
            icons[r.id] = ((out_dir / f"{r.id}.jpg").read_bytes(), (out_dir / f"{r.id}_locked.jpg").read_bytes())
    return icons


async def apply_section(
    t: Transport,
    values: dict[str, Any],
    state: State,
    files: ProjectFiles,
    consent: Consent,
    section: str,
    *,
    app: str = "main",
    dry_run: bool = True,
    user_confirmed: bool = False,
    remove_extra: bool = False,
    upload_icons: bool = False,
) -> dict[str, Any]:
    appid = fp.get(values, f"apps.{app}.appid")
    if not appid:
        raise ApplyRefused(f"apps.{app}.appid is not set.")
    if section in ("achievements", "store_page") and app != "main":
        raise ApplyRefused("Achievements and the store page are defined for the main game only.")
    current = await read_section(t, section, int(appid))
    sid = save_snapshot(files, app, int(appid), section, current)
    pending = sync.not_approved(values, state, section, app, icons=upload_icons)
    if pending:
        return {
            "snapshot": sid,
            "refused": NOT_APPROVED,
            "not_approved": pending,
        }
    desired = desired_from_values(section, values, state, files.root, app, current, remove_extra)
    icons = (
        _icons(values, files.root, files.snapshots_dir / f"{sid}-icons")
        if section == "achievements" and upload_icons
        else None
    )
    ops = _plan(section, int(appid), desired, current, remove_extra, icons)
    changes = [op.describe() for op in ops]
    if dry_run:
        return {
            "snapshot": sid,
            "dry_run": True,
            "changes": changes,
            "next": CONFIRM_NEXT if changes else "Steamworks already matches.",
        }
    if not user_confirmed:
        raise ApplyRefused("Writing needs user_confirmed=true, after the user saw the dry-run changes.")
    if not consent.restore_verified(int(appid)):
        raise ApplyRefused(
            f"Live-test protocol: the first write on app {appid} must be restore_snapshot('{sid}') (it writes "
            "back exactly what was just read). Once a restore worked, apply can write."
        )
    done, error = await _run(t, ops)
    after = await _read_back(
        t, files, section, int(appid), {"action": "apply", "app": app, "snapshot": sid, "done": done, "error": error}
    )
    remaining = _plan(
        section,
        int(appid),
        desired_from_values(section, values, state, files.root, app, after, remove_extra),
        after,
        remove_extra,
    )
    applied = (
        mark_applied_fields(values, state, lambda p: sync.written(section, app, p, upload_icons))
        if not remaining and error is None
        else []
    )
    if applied:
        applied += _mark_translations_applied(values, state, files.root, section, app)
    audit(
        files,
        {
            "action": "apply",
            "app": app,
            "appid": appid,
            "section": section,
            "snapshot": sid,
            "done": done,
            "error": error,
            "still_different": len(remaining),
        },
    )
    out = {
        "snapshot": sid,
        "done": done,
        "still_different": [op.describe() for op in remaining],
        "applied_fields": applied,
        "publish": "Nothing was published. Review and publish in Steamworks yourself (Publish tab).",
    }
    if error:
        out["error"] = (
            f"Stopped after {len(done)} of {len(ops)} change(s): {error}. restore_snapshot('{sid}') undoes them."
        )
    return out


async def _read_back(
    t: Transport, files: ProjectFiles, section: str, appid: int, entry: dict[str, Any]
) -> dict[str, Any]:
    try:
        return await read_section(t, section, appid)
    except Exception as exc:  # what was written still goes to the audit log
        audit(files, {**entry, "appid": appid, "section": section, "readback_error": str(exc)})
        raise


async def _run(t: Transport, ops: list[sync.Op]) -> tuple[list[dict[str, Any]], str | None]:
    """Run ops in order; stop at the first failure and report it (what was written before it is in ``done``)."""
    done: list[dict[str, Any]] = []
    for op in ops:
        try:
            await op.run(t)
        except Exception as exc:  # reported with the progress so far; the caller reads back and audits
            return done, f"{op.area} {op.action} {op.target}: {exc}"
        done.append(op.describe())
    return done, None


def mark_applied_fields(values: dict[str, Any], state: State, match: Callable[[str], bool]) -> list[str]:
    """Approved fields that ``match`` -> applied (after the readback showed Steam has them)."""
    out = []
    for path, _value in fp.iter_fields(values):
        fs = state.fields.get(path)
        if match(path) and fs is not None and fs.status == "approved":
            try:
                state.mark_applied(path, fp.get(values, path))
                out.append(path)
            except TransitionError:
                pass
    return out


TRANSLATED = {"installation": "apps.{app}.installation.", "achievements": "achievements.", "store_text": "store."}


def _mark_translations_applied(values: dict[str, Any], state: State, root: Path, section: str, app: str) -> list[str]:
    prefix = TRANSLATED.get(section, "").format(app=app)
    out = []
    for lang, texts in sync.approved_translations(values, state, root, prefix).items() if prefix else []:
        for key, text in texts.items():
            if section == "store_text" and key not in sync.STORE_FIELDS:
                continue  # only the two descriptions go through the store localization upload
            path = f"localization.{lang}.{key}"
            fs = state.fields.get(path)
            if fs is not None and fs.status == "approved":
                try:
                    state.mark_applied(path, text)
                    out.append(path)
                except TransitionError:
                    pass
    return out


async def restore_snapshot(
    t: Transport,
    files: ProjectFiles,
    consent: Consent,
    sid: str,
    *,
    dry_run: bool = True,
    user_confirmed: bool = False,
) -> dict[str, Any]:
    snap = load_snapshot(files, sid)
    section, appid = snap["section"], int(snap["appid"])
    current = await read_section(t, section, appid)
    target = snap["data"]
    if section == "achievements":
        desired: Any = [
            {
                "api_name": a["api_name"],
                "names": sync._as_map(a["display_name"]),
                "descriptions": sync._as_map(a["description"]),
                "hidden": sync._hidden(a.get("hidden")),
            }
            for a in target["achievements"]
        ]
    elif section == "store_text":
        desired = {lang: f for lang, f in target["languages"].items() if isinstance(f, dict) and f}
    elif section == "store_page":
        desired = {"inputs": target["form"], "problems": []}
    else:
        desired = target
    round_trip = not consent.restore_verified(appid)
    ops = _plan(section, appid, desired, current, True, force=round_trip)
    note = (
        "First restore on this app: every row is written back as it is in the snapshot and read again, to prove the "
        "tool reads and writes this app correctly before apply may change anything."
        if round_trip
        else "Only rows that differ from the snapshot are written."
    )
    if dry_run:
        return {
            "snapshot": sid,
            "dry_run": True,
            "changes": [op.describe() for op in ops],
            "note": note,
            "next": "Show these to the user; restore with dry_run=false, user_confirmed=true.",
        }
    if not user_confirmed:
        raise ApplyRefused("Restoring needs user_confirmed=true, after the user saw the changes.")
    done, error = await _run(t, ops)
    after = await _read_back(
        t, files, section, appid, {"action": "restore", "snapshot": sid, "done": done, "error": error}
    )
    remaining = _plan(section, appid, desired, after, True)
    verified = not remaining and error is None
    if verified:
        consent.mark_restore_verified(appid)
    audit(
        files,
        {
            "action": "restore",
            "appid": appid,
            "section": section,
            "snapshot": sid,
            "done": done,
            "error": error,
            "still_different": len(remaining),
        },
    )
    out = {
        "snapshot": sid,
        "done": done,
        "still_different": [op.describe() for op in remaining],
        "restore_verified": verified,
        "publish": "Nothing was published.",
    }
    if error:
        out["error"] = f"Stopped after {len(done)} of {len(ops)} change(s): {error}."
    return out
