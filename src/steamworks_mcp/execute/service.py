"""What the apply / inspect / build tools do, between the MCP layer and the API and BROWSER code.

Every write follows the same steps: read Steam's current state and keep it as a snapshot, show the differences
(``dry_run``, the default), write only after ``user_confirmed``, read back, mark what Steam now has as ``applied``,
and add an ``audit.jsonl`` entry.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio

from steamworks_mcp.config import Config
from steamworks_mcp.execute import apply as A
from steamworks_mcp.execute import sync
from steamworks_mcp.execute.api import PartnerApi, SteamApiError, plan_leaderboards, run_steamcmd
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import NotLoggedInError, Transport
from steamworks_mcp.export import vdf
from steamworks_mcp.manifest.io import atomic_write
from steamworks_mcp.project import Project

APPS = ("main", "demo", "playtest")
API_SECTIONS = ("leaderboards", "build")
BROWSER_SECTIONS = sync.SECTIONS
SECTIONS = BROWSER_SECTIONS + API_SECTIONS
INSPECT = ("builds", "leaderboards", "achievement_schema", "snapshots", "pending", *BROWSER_SECTIONS)

TransportFactory = Callable[[], Awaitable[Transport]]

NO_BROWSER = (
    "{what} needs the BROWSER mode, which is off (STEAM_MCP_BROWSER=1 turns it on; read the README first). "
    "Without it, export_package(gate) writes these values as files with a checklist for entering them by hand."
)


def appid_of(values: dict[str, Any], app: str) -> int:
    if app not in APPS:
        raise ValueError(f"app must be one of {', '.join(APPS)}")
    appid = ((values.get("apps") or {}).get(app) or {}).get("appid")
    if not appid:
        raise A.ApplyRefused(f"apps.{app}.appid is not set in steamworks.yaml.")
    return int(appid)


class Executor:
    def __init__(
        self, config: Config, transport_factory: TransportFactory | None = None, api: PartnerApi | None = None
    ) -> None:
        self.config = config
        self.consent = A.Consent(config.consent_path)
        self._transport_factory = transport_factory
        self._api = api
        self._session: Any = None

    # ------------------------------------------------------------------------------------------------ BROWSER

    def _browser(self) -> Any:
        if self._session is None:
            from steamworks_mcp.execute.browser.session import BrowserSession

            self._session = BrowserSession(self.config.browser_profile_dir)
        return self._session

    async def open(self, accept_risks: bool) -> dict[str, Any]:
        if not self.consent.accepted():
            if not accept_risks:
                return {
                    "consent_required": True,
                    "text": A.CONSENT_TEXT,
                    "next": "Show this text to the user as it is. If they agree, call again with accept_risks=true.",
                }
            self.consent.accept()
        if self._transport_factory is not None:
            return {"logged_in": True}
        session = self._browser()
        await session.show_partner_site()
        logged_in = await session.logged_in()
        return {
            "logged_in": logged_in,
            "next": "Steamworks is ready. Look first: steamworks_inspect, then apply(..., dry_run=true)."
            if logged_in
            else "Ask the user to sign in to Steamworks in the browser window that opened (they type the password and "
            "Steam Guard code themselves), then call steamworks_open again.",
        }

    async def transport(self, what: str) -> Transport:
        if not self.config.browser_enabled:
            raise A.ApplyRefused(NO_BROWSER.format(what=what))
        if not self.consent.accepted():
            raise A.ApplyRefused("Call steamworks_open first: the user has to accept the BROWSER-mode terms once.")
        if self._transport_factory is not None:
            return await self._transport_factory()
        session = self._browser()
        if session.page is None or not await session.logged_in():
            raise NotLoggedInError()
        transport: Transport = await session.transport()
        return transport

    async def restore(
        self, project: Project, snapshot: str | None, dry_run: bool, user_confirmed: bool
    ) -> dict[str, Any]:
        if not snapshot:
            return {
                "snapshots": list_snapshots(project),
                "next": "Pick one and call restore_snapshot(path, snapshot=<id>).",
            }
        t = await self.transport("restore_snapshot")
        return await A.restore_snapshot(
            t, project.files, self.consent, snapshot, dry_run=dry_run, user_confirmed=user_confirmed
        )

    # ------------------------------------------------------------------------------------------------ API

    def api(self) -> PartnerApi:
        if self._api is None:
            if not self.config.publisher_key:
                raise SteamApiError(
                    "STEAMWORKS_PUBLISHER_KEY is not set. Create one under Users & Permissions > Manage Groups > "
                    "Web API key and put it in .env."
                )
            self._api = PartnerApi(self.config.publisher_key)
        return self._api

    # ------------------------------------------------------------------------------------------------ apply

    async def apply(
        self,
        project: Project,
        section: str,
        app: str,
        *,
        dry_run: bool,
        user_confirmed: bool,
        remove_extra: bool,
        upload_icons: bool,
    ) -> dict[str, Any]:
        if section in BROWSER_SECTIONS:
            t = await self.transport(f"apply({section})")
            out = await A.apply_section(
                t,
                project.values(),
                project.state,
                project.files,
                self.consent,
                section,
                app=app,
                dry_run=dry_run,
                user_confirmed=user_confirmed,
                remove_extra=remove_extra,
                upload_icons=upload_icons,
            )
        elif section == "leaderboards":  # blocking HTTP calls and steamcmd run in a worker thread
            out = await anyio.to_thread.run_sync(
                functools.partial(
                    self.apply_leaderboards,
                    project,
                    app,
                    dry_run=dry_run,
                    user_confirmed=user_confirmed,
                    remove_extra=remove_extra,
                )
            )
        elif section == "build":
            out = await anyio.to_thread.run_sync(
                functools.partial(self.upload_build, project, app, dry_run=dry_run, user_confirmed=user_confirmed)
            )
        else:
            raise ValueError(f"section must be one of {', '.join(SECTIONS)}")
        project.save()
        return out

    def apply_leaderboards(
        self, project: Project, app: str, *, dry_run: bool, user_confirmed: bool, remove_extra: bool
    ) -> dict[str, Any]:
        if app != "main":
            raise A.ApplyRefused("Leaderboards are defined for the main game only.")
        values = project.values()
        appid = appid_of(values, app)
        api = self.api()
        current = api.leaderboards(appid)
        sid = A.save_snapshot(project.files, app, appid, "leaderboards", {"leaderboards": current})
        pending = sync.not_approved(values, project.state, "leaderboards", app)
        if pending:
            return {
                "snapshot": sid,
                "refused": A.NOT_APPROVED,
                "not_approved": pending,
            }
        desired = list(values.get("leaderboards") or [])
        plan = plan_leaderboards(desired, current, remove_extra)
        note = "Settings of existing leaderboards are not changed (only possible by deleting them with their scores)."
        if dry_run:
            changes = bool(plan["create"] or plan["delete"])
            return {
                "snapshot": sid,
                "dry_run": True,
                **plan,
                "note": note,
                "next": A.CONFIRM_NEXT if changes else "Nothing to create or delete.",
            }
        if not user_confirmed:
            raise A.ApplyRefused("Writing needs user_confirmed=true, after the user saw the dry-run changes.")
        done: list[dict[str, Any]] = []
        error = None
        try:
            for board in plan["create"]:
                api.find_or_create_leaderboard(appid, board)
                done.append({"action": "create", "name": board["name"]})
            for name in plan["delete"]:
                api.delete_leaderboard(appid, name)
                done.append({"action": "delete", "name": name})
        except SteamApiError as exc:
            error = str(exc)
        after = plan_leaderboards(desired, api.leaderboards(appid), remove_extra)
        not_there = {b["name"] for b in after["create"]} | {b["name"] for b in after["settings_differ"]}
        ok = {f"leaderboards.{d['name']}." for d in desired if d["name"] not in not_there}
        applied = A.mark_applied_fields(values, project.state, lambda p: any(p.startswith(x) for x in ok))
        A.audit(
            project.files,
            {
                "action": "apply",
                "app": app,
                "appid": appid,
                "section": "leaderboards",
                "snapshot": sid,
                "done": done,
                "error": error,
            },
        )
        out = {"snapshot": sid, "done": done, "still_different": after, "applied_fields": applied, "note": note}
        if error:
            out["error"] = error
        return out

    def upload_build(self, project: Project, app: str, *, dry_run: bool, user_confirmed: bool) -> dict[str, Any]:
        values = project.values()
        appid = appid_of(values, app)
        root = project.files.root
        script_dir = project.files.export_dir(2) / "steam"
        scripts = vdf.build_scripts(values, root, script_dir, app)
        if isinstance(scripts, str):
            raise A.ApplyRefused(f"No SteamPipe scripts yet: {scripts}. generate(section='builds') helps.")
        for name, text in scripts.items():
            atomic_write(script_dir / name, text)
        profile = (values.get("apps") or {}).get(app) or {}
        builds = profile.get("builds") or {}
        problems = []
        for d in builds.get("depots") or []:
            if not d.get("depot_id"):
                continue
            folder = root / str(d.get("content_root") or "Builds")
            if not folder.is_dir() or not any(p.is_file() for p in folder.rglob("*")):
                problems.append(
                    f"Depot {d['depot_id']} ({d.get('name')}): {folder} is missing or empty (build the game first)"
                )
        setup = []
        if not self.config.steamcmd_path:
            setup.append("STEAMCMD_PATH is not set (full path to steamcmd).")
        if not self.config.steamcmd_username:
            setup.append(
                "STEAMCMD_USERNAME is not set (the restricted builder account; log in once yourself with "
                "`steamcmd +login <name>` so it keeps the session)."
            )
        live = builds.get("set_live_on")
        script = script_dir / f"app_build_{appid}.vdf"
        info: dict[str, Any] = {
            "scripts": [(script_dir / n).relative_to(root).as_posix() for n in scripts],
            "command": vdf.steamcmd_command(appid, self.config.steamcmd_path or "steamcmd"),
            "set_live": f"The build goes live on the beta branch '{live}' after uploading."
            if live and live != "default"
            else "The build is uploaded only; nothing is set live (set_build_live does that).",
            "problems": problems,
            "setup_missing": setup,
        }
        pending = sync.not_approved(values, project.state, "build", app)
        if pending:
            return {
                **info,
                "refused": A.NOT_APPROVED,
                "not_approved": pending,
            }
        if dry_run:
            return {
                **info,
                "dry_run": True,
                "next": A.CONFIRM_NEXT,
            }
        if not user_confirmed:
            raise A.ApplyRefused("Uploading needs user_confirmed=true, after the user saw the dry run.")
        if problems or setup:
            raise A.ApplyRefused(" ".join(problems + setup))
        assert self.config.steamcmd_path and self.config.steamcmd_username
        result = run_steamcmd(self.config.steamcmd_path, self.config.steamcmd_username, script)
        A.audit(
            project.files,
            {
                "action": "upload_build",
                "app": app,
                "appid": appid,
                "success": result["success"],
                "build_id": result["build_id"],
                "exit_code": result["exit_code"],
            },
        )
        applied = (
            A.mark_applied_fields(values, project.state, lambda p: sync.written("build", app, p))
            if result["success"]
            else []
        )
        if result["needs_interactive_login"]:
            result["next"] = (
                "steamcmd needs a login: ask the user to run `steamcmd +login <builder account>` once in a "
                "terminal (they type the password and Steam Guard code), then try again."
            )
        return {**result, "applied_fields": applied}

    def set_build_live(
        self,
        project: Project,
        app: str,
        build_id: int,
        branch: str,
        *,
        description: str,
        dry_run: bool,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        """Beta branches only. The default branch (what every player gets) is set live by the user in App Admin."""
        appid = appid_of(project.values(), app)
        if branch.strip().lower() in ("default", "public", ""):
            raise A.ApplyRefused(
                "The default branch is set live by hand: Steamworks Settings > SteamPipe > Builds (on a released app "
                "Steam also asks for a Steam Mobile confirmation). This tool only sets beta branches live."
            )
        api = self.api()
        warning = f"Only players who opt into the beta branch '{branch}' get this build."
        if dry_run:
            return {
                "dry_run": True,
                "appid": appid,
                "build_id": build_id,
                "branch": branch,
                "warning": warning,
                "recent_builds": api.builds(appid, 5),
                "branches": api.betas(appid),
                "next": A.CONFIRM_NEXT,
            }
        if not user_confirmed:
            raise A.ApplyRefused("Setting a build live needs user_confirmed=true, after the user saw the dry run.")
        result = api.set_build_live(appid, build_id, branch, description)
        A.audit(
            project.files,
            {
                "action": "set_build_live",
                "app": app,
                "appid": appid,
                "build_id": build_id,
                "branch": branch,
                "needs_mobile_confirmation": result["needs_mobile_confirmation"],
            },
        )
        if result["needs_mobile_confirmation"]:
            result["next"] = (
                "Ask the user to confirm the build change in the Steam Mobile app; it is not live until then."
            )
        return {**result, "warning": warning}

    # ------------------------------------------------------------------------------------------------ inspect

    async def inspect(self, project: Project, what: str, app: str) -> dict[str, Any]:
        if what == "snapshots":
            return {"snapshots": list_snapshots(project)}
        if what not in INSPECT:
            raise ValueError(f"what must be one of {', '.join(INSPECT)}")
        appid = appid_of(project.values(), app)
        if what == "builds":
            api = self.api()
            return {"appid": appid, "builds": api.builds(appid), "branches": api.betas(appid)}
        if what == "leaderboards":
            return {"appid": appid, "leaderboards": self.api().leaderboards(appid)}
        if what == "achievement_schema":
            stats = self.api().schema(appid).get("availableGameStats") or {}
            return {"appid": appid, "achievements": stats.get("achievements") or [], "stats": stats.get("stats") or []}
        t = await self.transport(f"steamworks_inspect({what})")
        if what == "pending":
            return {
                "appid": appid,
                "unpublished_changes": await P.pending_changes(t, appid),
                "note": "Read-only. Publishing is always done by the user in Steamworks.",
            }
        return {"appid": appid, "steamworks": await A.read_section(t, what, appid)}


def list_snapshots(project: Project) -> list[str]:
    folder: Path = project.files.snapshots_dir
    return sorted(p.stem for p in folder.glob("*.json"))[-30:] if folder.is_dir() else []
