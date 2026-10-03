"""Live validation of the BROWSER mode against a real Steamworks app, through the MCP tools themselves.

    uv run python scripts/live/validate.py <appid> --accept-risks [--sections cloud,achievements,...] [--read-only]

Use a demo, a playtest or an app whose unpublished changes you can afford to see. What it does:

1. Opens the BROWSER-mode window (sign in there if asked) and keeps the Publish page's list of unpublished changes.
2. Reads every section (a changed page stops everything here, before any write).
3. Checks that steamworks.yaml values built from what Steamworks has produce no changes (the mapping round trip).
4. Runs the first-write protocol: a restore_snapshot round trip on Steam Cloud.
5. Per section, least visible first: a small test change -> dry run -> write -> readback -> restore_snapshot ->
   readback. Test rows are named ``steamworks-mcp-live-test`` / ``SWMCP_LIVE_TEST``.
6. Compares the unpublished changes with step 1, and checks that the public store page never showed the test text.

It never publishes. Everything it changes is restored; if a restore fails it stops and says what is left.
Output (the report and the throwaway project) goes to .steamworks-mcp/live/<appid>/, which git ignores.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from PIL import Image

from steamworks_mcp.config import Config
from steamworks_mcp.execute.service import Executor
from steamworks_mcp.execute.sync import CLOUD_OS, LAUNCH_OS
from steamworks_mcp.project import init_project
from steamworks_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[2]
SECTIONS = ("cloud", "achievements", "installation", "store_text")  # least visible first
MARK = "steamworks-mcp-live-test"
ACH = "SWMCP_LIVE_TEST"


class Failed(RuntimeError):
    pass


class Run:
    def __init__(self, client: Client, appid: int, out: Path) -> None:
        self.client, self.appid, self.out = client, appid, out
        self.report: dict[str, Any] = {"appid": appid, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}

    def log(self, step: str, ok: bool, **info: Any) -> None:
        self.report["steps"].append({"step": step, "ok": ok, **info})
        print(f"[{'ok' if ok else 'FAIL'}] {step}" + (f" - {info.get('note')}" if info.get("note") else ""), flush=True)
        (self.out / "report.json").write_text(
            json.dumps(self.report, indent=2, ensure_ascii=False, default=str), "utf-8"
        )

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        res = await self.client.call_tool(tool, args)
        if res.is_error:
            text = getattr(res.content[0], "text", str(res.content)) if res.content else "error"
            raise Failed(f"{tool}: {text}")
        return dict(res.structured_content or {})

    async def set(self, values: dict[str, Any]) -> None:
        out = await self.call("set_field", path="project", values=values)
        if out.get("not_saved"):
            raise Failed(f"set_field refused: {out['not_saved']}")


# ---------------------------------------------------------------------------------------------------- mapping


def cloud_values(c: dict[str, Any]) -> dict[str, Any]:
    os_back = {v: k for k, v in CLOUD_OS.items()}
    return {
        "apps.main.cloud.byte_quota": c["byte_quota"],
        "apps.main.cloud.file_quota": c["file_quota"],
        "apps.main.cloud.developers_only": c["developers_only"],
        "apps.main.cloud.sync_on_suspend": c["sync_on_suspend"],
        "apps.main.cloud.auto_cloud": [
            {
                "root": r["root"],
                "subdirectory": r["path"],
                "pattern": r["pattern"],
                "os": os_back.get(r["os"], "all"),
                "recursive": r["recursive"],
            }
            for r in c["roots"]
        ],
        "apps.main.cloud.overrides": [
            {
                "root": o["root"],
                "os": os_back[o["os"]],
                "use_instead": o["use_instead"],
                "add_path": o["add_path"],
                "replace_path": o["replace_path"],
            }
            for o in c["overrides"]
        ],
    }


def installation_values(inst: dict[str, Any]) -> dict[str, Any]:
    os_back = {v: k for k, v in LAUNCH_OS.items()}
    options = []
    for o in inst["launch_options"]:
        options.append(
            {
                "executable": o["executable"],
                "arguments": o["arguments"],
                "working_dir": o["working_dir"],
                "description": o["descriptions"].get("english") or None,
                "type": o["type"] or "default",
                "os": os_back.get(o["os"], "all"),
                "arch": o["arch"] or "all",
                "beta_key": o["beta_key"],
                "owns_dlc": o["owns_dlc"],
            }
        )
    return {
        "apps.main.installation.install_folder": inst["install_folder"] or None,
        "apps.main.installation.launch_options": options,
    }


def summary(section: str, data: dict[str, Any]) -> dict[str, Any]:
    if section == "cloud":
        return {"byte_quota": data["byte_quota"], "roots": len(data["roots"]), "overrides": len(data["overrides"])}
    if section == "achievements":
        return {
            "achievements": len(data["achievements"]),
            "with_progress": sum(bool(a.get("progress")) for a in data["achievements"]),
        }
    if section == "installation":
        return {"launch_options": len(data["launch_options"])}
    return {
        "languages_with_text": sorted(
            k for k, v in data["languages"].items() if isinstance(v, dict) and any(v.values())
        )
    }


# ---------------------------------------------------------------------------------------------------- steps


async def section_test(run: Run, section: str, current: dict[str, Any]) -> None:
    """Test change -> dry run -> write -> readback -> restore -> readback."""
    if section == "cloud":
        base = cloud_values(current)
        await run.set(base)
        await mapping_check(run, section)
        test = dict(base)
        test["apps.main.cloud.file_quota"] = (current["file_quota"] or 0) + 1
        test["apps.main.cloud.auto_cloud"] = [
            *base["apps.main.cloud.auto_cloud"],
            {"root": "gameinstall", "subdirectory": MARK, "pattern": "*.swmcp"},
        ]
        await run.set(test)
    elif section == "installation":
        base = installation_values(current)
        await run.set(base)
        await mapping_check(run, section)
        options = [
            *base["apps.main.installation.launch_options"],
            {
                "executable": f"{MARK}.exe",
                "arguments": "-swmcp-live",
                "type": "option3",
                "description": "steamworks-mcp live test (safe to delete)",
            },
        ]
        await run.set({"apps.main.installation.launch_options": options})
    elif section == "achievements":
        icon = run.out / "project" / "icons" / "live_test.png"
        icon.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (512, 512), (40, 120, 200)).save(icon)
        await run.set(
            {
                "achievements": [
                    {
                        "id": ACH,
                        "name": "steamworks-mcp live test",
                        "hidden": True,
                        "description": "Created by a live test; safe to delete.",
                        "icon": "icons/live_test.png",
                    }
                ]
            }
        )
    elif section == "store_text":
        short = current["languages"]["english"].get("app[content][short_description]", "")
        text = (short[: 300 - len(MARK) - 3].rstrip() + f" [{MARK}]") if short else f"[{MARK}]"
        await run.set({"store.short_description": text})
        run.report["store_test_text"] = MARK

    upload_icons = section == "achievements"
    dry = await run.call("apply", path="project", section=section, upload_icons=upload_icons)
    if dry.get("refused") or not dry.get("changes"):
        raise Failed(f"{section}: dry run did not plan the test change: {dry}")
    run.log(f"{section}: dry run", True, changes=dry["changes"])
    wrote = await run.call(
        "apply", path="project", section=section, dry_run=False, user_confirmed=True, upload_icons=upload_icons
    )
    sid = wrote["snapshot"]
    ok = not wrote.get("error") and wrote["still_different"] == []
    run.log(
        f"{section}: write + readback",
        ok,
        done=len(wrote["done"]),
        applied=wrote["applied_fields"],
        error=wrote.get("error"),
        still_different=wrote["still_different"],
    )
    if section == "store_text":
        await public_check(run)
    if section != "store_text":  # the store page is not part of the Publish page's technical diff
        needle = ACH if section == "achievements" else MARK
        pending = await run.call("steamworks_inspect", path="project", what="pending")
        added = "".join(a for v in pending["sections"].values() for a in v["added"])
        hidden = [k for k, v in pending["sections"].items() if v["new"]]
        run.log(
            f"{section}: listed as an unpublished change",
            needle in added or bool(hidden),
            changed_sections=pending["changed_sections"],
            note=None if needle in added else f"new section(s) {hidden}: Steamworks does not show their content",
        )
    restored = await run.call("restore_snapshot", path="project", snapshot=sid, dry_run=False, user_confirmed=True)
    run.log(
        f"{section}: restore",
        bool(restored["restore_verified"]),
        done=len(restored["done"]),
        still_different=restored["still_different"],
        error=restored.get("error"),
    )
    if not restored["restore_verified"]:
        raise Failed(f"{section}: restore did not verify; snapshot {sid} still differs. Stop and check Steamworks.")


def same_changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str | None]:
    """Real unpublished changes are the same as before (saved-but-unchanged revisions do not count)."""

    def content(p: dict[str, Any], name: str) -> Any:
        v = p["sections"].get(name) or {}
        return ["".join(v.get("removed", [])).split(), "".join(v.get("added", [])).split()]

    extra = [n for n in after["changed_sections"] if n not in before["changed_sections"]]
    differ = [n for n in before["changed_sections"] if content(before, n) != content(after, n)]
    hidden = [n for n in extra if after["sections"][n]["new"]]
    real = [n for n in extra if n not in hidden] + differ
    if real:
        return False, f"changed: {real}"
    if hidden:
        return True, f"new section(s) {hidden} remain unpublished; Steamworks does not show their content"
    return True, None


async def mapping_check(run: Run, section: str) -> None:
    dry = await run.call("apply", path="project", section=section)
    run.log(
        f"{section}: steamworks.yaml built from Steamworks plans no changes",
        dry.get("changes") == [],
        changes=dry.get("changes"),
        refused=dry.get("refused"),
    )


async def public_check(run: Run) -> None:
    try:
        r = httpx.get(
            "https://store.steampowered.com/api/appdetails", params={"appids": run.appid, "l": "english"}, timeout=30
        )
        data = r.json().get(str(run.appid), {})
    except (httpx.HTTPError, ValueError) as exc:
        run.log("public store page: unchanged", False, note=f"could not read appdetails: {exc}")
        return
    if not data.get("success"):
        run.log("public store page: unchanged", True, note="the app has no public store data (not released)")
        return
    short = data["data"].get("short_description", "")
    run.log(
        "public store page: unchanged",
        MARK not in short,
        note="the unpublished test text is not public" if MARK not in short else "TEST TEXT IS PUBLIC",
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("appid", type=int)
    ap.add_argument("--accept-risks", action="store_true", help="accept the BROWSER-mode terms (shown on first run)")
    ap.add_argument("--sections", default=",".join(SECTIONS))
    ap.add_argument("--read-only", action="store_true")
    args = ap.parse_args()
    sections = [s for s in SECTIONS if s in args.sections.split(",")]

    out = ROOT / ".steamworks-mcp" / "live" / str(args.appid)
    shutil.rmtree(out / "project", ignore_errors=True)
    (out / "project").mkdir(parents=True)
    init_project(out / "project", "Live test", args.appid)
    config = Config(workspace_root=out, browser_enabled=True)

    execu = Executor(config)
    async with Client(create_server(config, execu)) as client:
        run = Run(client, args.appid, out)
        try:
            opened = await run.call("steamworks_open")
            if opened.get("consent_required"):
                print(opened["text"])
                if not args.accept_risks:
                    print("Run again with --accept-risks once you agree.")
                    return 2
                opened = await run.call("steamworks_open", accept_risks=True)
            for _ in range(60):
                if opened.get("logged_in"):
                    break
                print("Sign in to Steamworks in the browser window...", flush=True)
                await asyncio.sleep(10)
                opened = await run.call("steamworks_open")
            else:
                raise Failed("not signed in")
            run.log("signed in", True)

            baseline = await run.call("steamworks_inspect", path="project", what="pending")
            run.report["pending_before"] = baseline
            current: dict[str, dict[str, Any]] = {}
            for section in SECTIONS:
                current[section] = (await run.call("steamworks_inspect", path="project", what=section))["steamworks"]
                run.log(f"{section}: read (page format as recorded)", True, summary=summary(section, current[section]))
            if args.read_only:
                return 0

            # first write on the app: the restore round trip, on the least visible section
            snap = (await run.call("apply", path="project", section="cloud"))["snapshot"]
            dry = await run.call("restore_snapshot", path="project", snapshot=snap)
            rt = await run.call("restore_snapshot", path="project", snapshot=snap, dry_run=False, user_confirmed=True)
            run.log(
                "restore round trip (first write)",
                bool(rt["restore_verified"]),
                planned=len(dry["changes"]),
                done=len(rt["done"]),
                still_different=rt["still_different"],
                error=rt.get("error"),
            )
            if not rt["restore_verified"]:
                raise Failed("the round trip did not verify; no further writes")
            ok, note = same_changes(baseline, await run.call("steamworks_inspect", path="project", what="pending"))
            run.log("round trip changed nothing in Steamworks", ok, note=note)
            if not ok:
                raise Failed("writing back what was read changed something; no further writes")

            for section in sections:
                await section_test(run, section, current[section])

            after = await run.call("steamworks_inspect", path="project", what="pending")
            run.report["pending_after"] = after
            ok, note = same_changes(baseline, after)
            run.log("unpublished changes are as before the tests", ok, note=note)
        except Failed as exc:
            run.log("stopped", False, note=str(exc))
            return 1
        finally:
            run.report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
            (out / "report.json").write_text(json.dumps(run.report, indent=2, ensure_ascii=False, default=str), "utf-8")
            print(f"report: {out / 'report.json'}")
            await execu.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
