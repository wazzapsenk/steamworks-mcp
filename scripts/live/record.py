"""Record the Steamworks traffic the offline tests replay (tests/fixtures/steamworks), step by step.

    uv run python scripts/live/record.py <appid> list
    uv run python scripts/live/record.py <appid> sprint            # every step, cleanup last
    uv run python scripts/live/record.py <appid> cloud/read cloud/write cloud/readback cleanup/cloud
    uv run python scripts/live/sanitize.py <appid>                 # raw recordings -> fixtures

Use a test app or a playtest, never a released game. Raw recordings go to .steamworks-mcp/recordings/raw (ignored by
git); only sanitize.py output is committed. Every request goes through the BROWSER mode's own code and guard, so
nothing is ever published, prepared or reverted, and the cleanup steps only delete rows this script created
(names containing SWMCP_REC / -swmcp-rec). Run them even after a failure: they put the app back as it was.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from har import HarRecorder, httpx_entry

from steamworks_mcp.config import load_config
from steamworks_mcp.execute.api import PartnerApi
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.session import BrowserSession
from steamworks_mcp.execute.browser.transport import PlaywrightTransport

ROOT = Path(__file__).resolve().parents[2]
REC = ROOT / ".steamworks-mcp" / "recordings"
MARK = ("SWMCP_REC", "SWMCPRec", "-swmcp-rec")
TEST_ACH = "SWMCP_REC_A"
SHORT = "app[content][short_description]"


def marked(*values: Any) -> bool:
    return any(m.lower() in str(v).lower() for v in values for m in MARK)


@dataclass
class Ctx:
    appid: int
    t: PlaywrightTransport
    rec: HarRecorder
    key: str | None
    out: Path

    @property
    def baseline_file(self) -> Path:
        return REC / f"baseline-{self.appid}.json"

    def baseline(self) -> dict[str, Any]:
        f = self.baseline_file
        if not f.exists():
            raise RuntimeError("No baseline yet: run the */read steps first.")
        return dict(json.loads(f.read_text(encoding="utf-8")))

    def save_baseline(self, name: str, data: Any) -> None:
        f = self.baseline_file
        base = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        base.setdefault(name, data)  # the first read is the baseline; re-reads never replace it
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(base, indent=2, ensure_ascii=False), encoding="utf-8")

    def api(self) -> PartnerApi:
        if not self.key:
            raise RuntimeError("STEAMWORKS_PUBLISHER_KEY is not set")
        key = self.key

        def record(response: httpx.Response) -> None:
            response.read()
            self.rec.add_manual(httpx_entry(response, [key]))

        return PartnerApi(key, httpx.Client(timeout=30, event_hooks={"response": [record]}))


Step = Callable[[Ctx], Awaitable[str]]
STEPS: dict[str, tuple[str, Step]] = {}


def step(name: str, describe: str) -> Callable[[Step], Step]:
    def register(fn: Step) -> Step:
        STEPS[name] = (describe, fn)
        return fn

    return register


def jpg(gray: bool) -> bytes:
    img = Image.new("RGB", (256, 256), (30, 140, 90))
    buf = BytesIO()
    (img.convert("L").convert("RGB") if gray else img).save(buf, "JPEG", quality=90)
    return buf.getvalue()


async def public(c: Ctx) -> str:
    out = []
    with httpx.Client(timeout=30) as client:
        for url, params in (
            ("https://store.steampowered.com/api/appdetails", {"appids": str(c.appid), "l": "english"}),
            (
                "https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
                {"gameid": str(c.appid)},
            ),
        ):
            r = client.get(url, params=params)
            c.rec.add_manual(httpx_entry(r, []))
            out.append(f"{url.split('/')[-2] or url}: {r.status_code}")
    return ", ".join(out)


async def visibility(c: Ctx) -> str:
    await c.t.get(f"/apps/landing/{c.appid}")
    await c.t.get(f"/apps/history/{c.appid}")
    pending = await P.pending_changes(c.t, c.appid)
    return f"unpublished sections {pending['changed_sections']} | {await public(c)}"


# ---------------------------------------------------------------------------------------------------- reads


@step("visibility/before", "Public data and the unpublished changes before any write (read-only)")
async def visibility_before(c: Ctx) -> str:
    return await visibility(c)


@step("store/read", "Open the store page (redirect to the item id) and download the localization JSON")
async def store_read(c: Ctx) -> str:
    item = await P.store_item_id(c.t, c.appid)
    await c.t.get(f"/admin/game/edit/{item}")
    loc = await P.read_store_localization(c.t, item)
    c.save_baseline("store", loc)
    return f"item {item}, {len(loc['languages'])} languages"


@step("achievements/read", "Open Stats & Achievements and fetch the achievement list")
async def achievements_read(c: Ctx) -> str:
    st = await P.read_achievements(c.t, c.appid)
    c.save_baseline("achievements", st)
    return f"{len(st['achievements'])} achievements"


@step("cloud/read", "Open the Steam Cloud page")
async def cloud_read(c: Ctx) -> str:
    st = await P.read_cloud(c.t, c.appid)
    c.save_baseline("cloud", st)
    return f"quota {st['byte_quota']}/{st['file_quota']}, {len(st['roots'])} roots, {len(st['overrides'])} overrides"


@step("installation/read", "Open Installation > General")
async def installation_read(c: Ctx) -> str:
    st = await P.read_installation(c.t, c.appid)
    c.save_baseline("installation", st)
    return f"folder {st['install_folder']!r}, {len(st['launch_options'])} launch options"


@step("api/read", "Partner Web API reads with the publisher key")
async def api_read(c: Ctx) -> str:
    api = c.api()
    api.schema(c.appid)
    api.builds(c.appid, 5)
    api.betas(c.appid)
    return f"{len(api.leaderboards(c.appid))} leaderboards"


@step("api/public_unkeyed", "Public endpoints without any key (what anyone can see)")
async def api_public(c: Ctx) -> str:
    with httpx.Client(timeout=30) as client:
        r = client.get("https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/", params={"appid": c.appid})
        c.rec.add_manual(httpx_entry(r, []))
    return f"GetSchemaForGame without key: {r.status_code} | {await public(c)}"


# ---------------------------------------------------------------------------------------------------- writes


@step("cloud/write", "setufsparameters, setautocloudpath (new row), setautocloudoverride (new row)")
async def cloud_write(c: Ctx) -> str:
    base = c.baseline()["cloud"]
    await P.read_cloud(c.t, c.appid)
    await P.set_ufs(
        c.t, c.appid, {**base, "byte_quota": base["byte_quota"] + 1_048_576, "file_quota": base["file_quota"] + 1}
    )
    await P.set_root(
        c.t,
        c.appid,
        {
            "index": len(base["roots"]),
            "root": "WinAppDataLocalLow",
            "path": "SWMCPRec/Saves",
            "pattern": "*.sav",
            "os": "",
            "recursive": True,
        },
    )
    await P.set_override(
        c.t,
        c.appid,
        {
            "index": len(base["overrides"]),
            "root": "WinAppDataLocalLow",
            "os": "MacOS",
            "use_instead": "MacAppSupport",
            "add_path": "SWMCPRec",
            "replace_path": False,
        },
    )
    return "quota +1 MB / +1 file, one root, one override"


@step("cloud/readback", "Re-open the Steam Cloud page")
async def cloud_readback(c: Ctx) -> str:
    st = await P.read_cloud(c.t, c.appid)
    return f"quota {st['byte_quota']}/{st['file_quota']}, roots {[r['path'] for r in st['roots']]}"


@step("achievements/write", "newachievement + saveachievement (hidden, English and German) + both icons")
async def achievements_write(c: Ctx) -> str:
    st = await P.read_achievements(c.t, c.appid)
    existing = next((a for a in st["achievements"] if a["api_name"] == TEST_ACH), None)
    if existing:
        stat, bit = str(existing["stat_id"]), str(existing["bit_id"])
    else:
        made = await P.new_achievement(c.t, c.appid, st["max_statid"], st["max_bitid"])
        stat, bit = str(made["achievement"]["stat_id"]), str(made["achievement"]["bit_id"])
    await P.save_achievement(
        c.t,
        c.appid,
        stat,
        bit,
        TEST_ACH,
        {"english": "Recording Test A", "german": "Aufnahme-Test A"},
        {"english": "Fixture recording, safe to delete.", "german": "Fixture-Aufnahme, kann gelöscht werden."},
        True,
    )
    await P.upload_achievement_icon(c.t, c.appid, stat, bit, jpg(False), False, "test_icon.jpg")
    await P.upload_achievement_icon(c.t, c.appid, stat, bit, jpg(True), True, "test_icon_gray.jpg")
    return f"{'updated' if existing else 'created'} {TEST_ACH} ({stat}/{bit}), icons uploaded"


@step("achievements/readback", "fetchachievements again")
async def achievements_readback(c: Ctx) -> str:
    st = await P.read_achievements(c.t, c.appid)
    return f"{[a['api_name'] for a in st['achievements']]}"


@step("installation/write", "setappinstallfolder (same value) + setlaunchoption (new row)")
async def installation_write(c: Ctx) -> str:
    base = c.baseline()["installation"]
    await P.read_installation(c.t, c.appid)
    if base["install_folder"]:
        await P.set_install_folder(c.t, c.appid, base["install_folder"])
    template = base["launch_options"][0] if base["launch_options"] else {}
    index = max([o["index"] for o in base["launch_options"]], default=-1) + 1
    await P.set_launch_option(
        c.t,
        c.appid,
        {
            "index": index,
            "executable": "SWMCPRec.exe",
            "arguments": "-swmcp-rec",
            "working_dir": "",
            "type": "option1",
            "os": "windows",
            "arch": "64",
            "beta_key": "",
            "owns_dlc": "",
            "oscpu": template.get("oscpu", ""),
            "realm": template.get("realm", ""),
            "steamdeck": template.get("steamdeck", ""),
            "descriptions": {"english": "Recording test option", "german": "Aufnahme-Testoption"},
        },
    )
    return f"launch option #{index}"


@step("installation/readback", "Re-open Installation > General")
async def installation_readback(c: Ctx) -> str:
    st = await P.read_installation(c.t, c.appid)
    return f"{[(o['index'], o['executable']) for o in st['launch_options']]}"


async def upload_store(c: Ctx, english: dict[str, str], name: str) -> None:
    item = await P.store_item_id(c.t, c.appid)
    data = json.dumps({"itemid": item, "languages": {"english": english}}, indent=2, ensure_ascii=False)
    (c.out / "_uploads").mkdir(parents=True, exist_ok=True)
    (c.out / "_uploads" / name).write_text(data, encoding="utf-8")  # the browser does not show upload bytes
    await c.t.upload_store_localization(c.appid, name, data.encode("utf-8"))


@step("store/write", "Upload a localization JSON that changes only the English short description")
async def store_write(c: Ctx) -> str:
    en = c.baseline()["store"]["languages"].get("english") or {}
    await upload_store(c, {SHORT: f"{str(en.get(SHORT, ''))[:250]} (recording)"}, "store_loc_upload.json")
    return "uploaded"


@step("store/readback", "Download the localization JSON again")
async def store_readback(c: Ctx) -> str:
    loc = await P.read_store_localization(c.t, await P.store_item_id(c.t, c.appid))
    return f"english short ends with: {str(loc['languages']['english'].get(SHORT, ''))[-30:]!r}"


@step("api/leaderboard_write", "FindOrCreateLeaderboard (test board) + GetLeaderboardsForGame readback")
async def leaderboard_write(c: Ctx) -> str:
    api = c.api()
    api.find_or_create_leaderboard(
        c.appid, {"name": "SWMCP_REC_BOARD", "sort_method": "descending", "display_type": "numeric"}
    )
    return f"{[b.get('name') for b in api.leaderboards(c.appid)]}"


# ---------------------------------------------------------------------------------------------- what Steamworks accepts


@step("errors/cloud_quota_too_big", "setufsparameters above the documented limits (10 GB / 10,000 files)")
async def err_quota(c: Ctx) -> str:
    base = c.baseline()["cloud"]
    await P.set_ufs(c.t, c.appid, {**base, "byte_quota": 20_000_000_000, "file_quota": 20_000})
    st = await P.read_cloud(c.t, c.appid)
    return f"stored {st['byte_quota']}/{st['file_quota']}"


async def extra_root(c: Ctx, root: str, pattern: str) -> str:
    st = await P.read_cloud(c.t, c.appid)
    await P.set_root(
        c.t,
        c.appid,
        {
            "index": len(st["roots"]),
            "root": root,
            "path": "SWMCPRec/Bad",
            "pattern": pattern,
            "os": "",
            "recursive": False,
        },
    )
    after = await P.read_cloud(c.t, c.appid)
    return f"stored {after['roots'][-1]}"


@step("errors/cloud_invalid_root", "setautocloudpath with an unknown root name")
async def err_root(c: Ctx) -> str:
    return await extra_root(c, "NotARealRoot", "*.sav")


@step("errors/cloud_missing_pattern", "setautocloudpath with an empty pattern")
async def err_pattern(c: Ctx) -> str:
    return await extra_root(c, "WinAppDataLocalLow", "")


@step("errors/achievement_duplicate_apiname", "A second achievement saved with an API name that already exists")
async def err_duplicate(c: Ctx) -> str:
    st = await P.read_achievements(c.t, c.appid)
    made = await P.new_achievement(c.t, c.appid, st["max_statid"], st["max_bitid"])
    a = made["achievement"]
    await P.save_achievement(
        c.t,
        c.appid,
        str(a["stat_id"]),
        str(a["bit_id"]),
        TEST_ACH,
        {"english": "Duplicate"},
        {"english": "Same API name"},
        True,
    )
    after = await P.read_achievements(c.t, c.appid)
    return f"{sum(x['api_name'] == TEST_ACH for x in after['achievements'])} achievements named {TEST_ACH}"


@step("errors/launch_empty_executable", "setlaunchoption on a new index with an empty executable")
async def err_executable(c: Ctx) -> str:
    st = await P.read_installation(c.t, c.appid)
    index = max([o["index"] for o in st["launch_options"]], default=-1) + 1
    await P.set_launch_option(
        c.t,
        c.appid,
        {
            "index": index,
            "executable": "",
            "arguments": "-swmcp-rec-empty",
            "working_dir": "",
            "type": "option2",
            "os": "windows",
            "arch": "64",
            "beta_key": "",
            "owns_dlc": "",
            "descriptions": {"english": "Empty executable test"},
        },
    )
    return f"launch option #{index} with an empty executable"


@step("errors/session_expired", "A cookie-less browser: protected page redirect + AJAX GET/POST without a session")
async def err_session(c: Ctx) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome") if os.name == "nt" else await pw.chromium.launch()
        page = await (await browser.new_context()).new_page()
        c.rec.attach(page)
        await page.goto(f"https://partner.steamgames.com/apps/achievements/{c.appid}", wait_until="domcontentloaded")
        t = PlaywrightTransport(page)
        res = await t.get(f"/apps/fetchachievements/{c.appid}")
        post = await t.post(f"/apps/setufsparameters/{c.appid}", {"cb": "0"})
        await browser.close()
    return f"page -> {page.url[:60]}, AJAX GET -> {res.url[:60]}, POST -> {post.url[:60]}"


@step("visibility/after_writes", "Public data and the unpublished changes while the drafts exist (read-only)")
async def visibility_after_writes(c: Ctx) -> str:
    return await visibility(c)


# ---------------------------------------------------------------------------------------------------- cleanup


@step("cleanup/achievements", "Delete the achievements this script created")
async def cleanup_achievements(c: Ctx) -> str:
    st = await P.read_achievements(c.t, c.appid)
    gone = []
    for a in st["achievements"]:
        if marked(a["api_name"]):
            await P.delete_achievement(c.t, c.appid, str(a["stat_id"]), str(a["bit_id"]))
            gone.append(a["api_name"])
    return f"deleted {gone}"


@step("cleanup/cloud", "Remove the test rows and restore the quota settings")
async def cleanup_cloud(c: Ctx) -> str:
    base = c.baseline()["cloud"]
    st = await P.read_cloud(c.t, c.appid)
    for o in sorted(st["overrides"], key=lambda o: -o["index"]):
        if o["index"] >= len(base["overrides"]):
            await P.delete_override(c.t, c.appid, o["index"])
    for r in sorted(st["roots"], key=lambda r: -r["index"]):
        if r["index"] >= len(base["roots"]):
            await P.delete_root(c.t, c.appid, r["index"])
    await P.set_ufs(c.t, c.appid, base)  # last: saving rows turns "developers only" back on
    return "restored"


@step("cleanup/installation", "Delete the test launch options and restore the install folder")
async def cleanup_installation(c: Ctx) -> str:
    base = c.baseline()["installation"]
    st = await P.read_installation(c.t, c.appid)
    gone = []
    for o in sorted(st["launch_options"], key=lambda o: -o["index"]):
        if marked(o["executable"], o["arguments"]):
            await P.delete_launch_option(c.t, c.appid, o["index"])
            gone.append(o["index"])
    if base["install_folder"] and st["install_folder"] != base["install_folder"]:
        await P.set_install_folder(c.t, c.appid, base["install_folder"])
    return f"deleted launch options {gone}"


@step("cleanup/store", "Upload the baseline English texts back (only fields that differ)")
async def cleanup_store(c: Ctx) -> str:
    base = c.baseline()["store"]["languages"].get("english") or {}
    now = (await P.read_store_localization(c.t, await P.store_item_id(c.t, c.appid)))["languages"].get("english") or {}
    changed = {k: v for k, v in base.items() if now.get(k) != v and v}
    if changed:
        await upload_store(c, changed, "store_loc_restore.json")
    return f"restored {sorted(changed)}"


@step("api/leaderboard_delete", "DeleteLeaderboard (test board) + GetLeaderboardsForGame readback")
async def leaderboard_delete(c: Ctx) -> str:
    api = c.api()
    api.delete_leaderboard(c.appid, "SWMCP_REC_BOARD")
    return f"{[b.get('name') for b in api.leaderboards(c.appid)]}"


@step("cleanup/verify", "Read everything again and compare with the baseline")
async def cleanup_verify(c: Ctx) -> str:
    base = c.baseline()
    now = {
        "cloud": await P.read_cloud(c.t, c.appid),
        "installation": await P.read_installation(c.t, c.appid),
        "achievements": [a["api_name"] for a in (await P.read_achievements(c.t, c.appid))["achievements"]],
        "store": (await P.read_store_localization(c.t, await P.store_item_id(c.t, c.appid)))["languages"].get(
            "english"
        ),
    }
    differ = [k for k in ("cloud", "installation") if now[k] != base.get(k)]
    if now["achievements"] != [a["api_name"] for a in base.get("achievements", {}).get("achievements", [])]:
        differ.append("achievements")
    if now["store"] != base.get("store", {}).get("languages", {}).get("english"):
        differ.append("store")
    if differ:
        raise RuntimeError(f"not back to the baseline: {differ}")
    return "everything matches the baseline"


@step("visibility/after_cleanup", "Public data and the unpublished changes after cleanup (read-only)")
async def visibility_after_cleanup(c: Ctx) -> str:
    return await visibility(c)


SPRINT = [
    "visibility/before",
    "store/read",
    "achievements/read",
    "cloud/read",
    "installation/read",
    "api/read",
    "api/public_unkeyed",
    "cloud/write",
    "cloud/readback",
    "achievements/write",
    "achievements/readback",
    "installation/write",
    "installation/readback",
    "store/write",
    "store/readback",
    "api/leaderboard_write",
    "errors/cloud_quota_too_big",
    "errors/cloud_invalid_root",
    "errors/cloud_missing_pattern",
    "errors/achievement_duplicate_apiname",
    "errors/launch_empty_executable",
    "errors/session_expired",
    "visibility/after_writes",
    "cleanup/achievements",
    "cleanup/cloud",
    "cleanup/installation",
    "cleanup/store",
    "api/leaderboard_delete",
    "cleanup/verify",
    "visibility/after_cleanup",
]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("appid", type=int)
    ap.add_argument("steps", nargs="+", help="step names, 'sprint' for all, or 'list'")
    ap.add_argument("--out", type=Path, default=REC / "raw")
    args = ap.parse_args()
    if args.steps == ["list"]:
        for name in SPRINT:
            print(f"{name:40} {STEPS[name][0]}")
        return 0
    names = SPRINT if args.steps == ["sprint"] else args.steps
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        print(f"unknown steps: {unknown}; see 'list'")
        return 2
    key = load_config(dotenv=ROOT / ".env").publisher_key
    session = BrowserSession()
    await session.show_partner_site()
    for _ in range(60):
        if await session.logged_in():
            break
        print("Sign in to Steamworks in the browser window...", flush=True)
        await asyncio.sleep(10)
    else:
        print("not signed in")
        return 1
    rec = HarRecorder()
    rec.attach(session.page)
    c = Ctx(args.appid, await session.transport(), rec, key, args.out)
    failed = 0
    try:
        for name in names:
            describe, fn = STEPS[name]
            if name.startswith("api/") and name != "api/public_unkeyed" and not key:
                print(f"skip {name}: no publisher key")
                continue
            rec.begin()
            ok, summary = True, ""
            try:
                summary = await fn(c)
            except Exception as exc:  # recorded and reported; the next steps (cleanup) still run
                ok, summary, failed = False, f"{type(exc).__name__}: {exc}", failed + 1
            n = await rec.end(
                args.out / f"{name}.har", {"step": name, "describe": describe, "ok": ok, "summary": summary}
            )
            print(f"{'ok  ' if ok else 'FAIL'} {name} ({n} entries) - {summary}", flush=True)
    finally:
        await session.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
