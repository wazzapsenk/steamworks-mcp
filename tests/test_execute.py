"""Phase 7: writing to Steam. BROWSER mode against the recorded Steamworks traffic, the Web API against a mock, and
the hard rules (never publish, dry run first, restore first, approved values only, readback, audit)."""

from __future__ import annotations

import html
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from mcp import Client

from steamworks_mcp import project as proj
from steamworks_mcp.config import Config, load_config
from steamworks_mcp.execute import guard, importer, store_assets, store_page, sync
from steamworks_mcp.execute.api import DISPLAY_TYPES, PartnerApi, SteamApiError, plan_leaderboards, run_steamcmd
from steamworks_mcp.execute.apply import ApplyRefused, Consent, apply_section, restore_snapshot, save_snapshot
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.html import parse
from steamworks_mcp.execute.browser.transport import NotLoggedInError, PlaywrightTransport, ReplayTransport, Response
from steamworks_mcp.execute.service import Executor
from steamworks_mcp.fields import set_fields
from steamworks_mcp.localization import store as loc_store
from steamworks_mcp.localization.store import set_translations
from steamworks_mcp.project import Project
from steamworks_mcp.server import create_server

FIX = Path(__file__).parent / "fixtures" / "steamworks"
APP = 1000000
KEY = "ABCDEF0123456789ABCDEF0123456789"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def replay(*steps: str) -> ReplayTransport:
    return ReplayTransport([FIX / f"{s}.har" for s in steps])


def recorded_posts(step: str) -> list[tuple[str, dict[str, str]]]:
    """The form posts of a recorded step, without the CSRF session id."""
    out = []
    for e in json.loads((FIX / f"{step}.json").read_text(encoding="utf-8")):
        if e["method"] == "POST" and "multipart" not in e["request"]:
            out.append((urlsplit(e["url"]).path, {k: v for k, v in e["request"].items() if k != "sessionid"}))
    return out


def sent_posts(t: ReplayTransport) -> list[tuple[str, dict[str, str]]]:
    return [(s.path, s.form) for s in t.sent if s.method == "POST"]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "game"
    root.mkdir()
    proj.init_project(root, "ExampleGame", APP)
    return Project.open(root)


@pytest.fixture
def consent(tmp_path: Path) -> Consent:
    c = Consent(tmp_path / "home" / "consent.json")
    c.accept()
    return c


def setv(project: Project, values: dict[str, Any], source: str = "user") -> Project:
    out = set_fields(project, values, source)  # type: ignore[arg-type]
    assert not out.get("not_saved"), out
    project.save()
    return Project.open(project.files.root)


def translate(project: Project, lang: str, texts: dict[str, str]) -> Project:
    out = set_translations(project.values(), project.files.root, project.state, lang, texts, source="user")
    assert out["saved"] and not out["rejected"], out
    project.save()
    return Project.open(project.files.root)


async def run_apply(project: Project, consent: Consent, t: ReplayTransport, section: str, **kw: Any) -> dict[str, Any]:
    out = await apply_section(t, project.values(), project.state, project.files, consent, section, **kw)
    project.save()
    return out


def audit_entries(project: Project) -> list[dict[str, Any]]:
    log = project.files.audit_log
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


# ------------------------------------------------------------------------------------------------ never publish


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/apps/publishing/1000000"),
        ("POST", "/apps/publishing/1000000"),
        ("POST", "/apps/prepare/1000000"),
        ("POST", "/apps/revert/1000000"),
        ("POST", "/apps/publish/1000000"),
        ("POST", "/admin/game/submit/2000000"),
        ("POST", "/admin/game/release/2000000"),
        ("GET", "/admin/game/publish/2000000"),
        ("POST", "/admin/game/revert/2000000"),
        ("POST", "/admin/game/prepare/2000000"),
        ("POST", "/admin/store/packagerevert/3000000"),
        ("POST", "/admin/store/packageprepare/3000000"),
        ("POST", "/apps/retireapp/1000000"),
        ("POST", "/tagdata/forcetagranking"),  # store tags go live at once
        ("POST", "/store/ajaxpackagesave/3000000"),  # package names and contents too
    ],
)
def test_guard_blocks_publishing(method: str, path: str) -> None:
    with pytest.raises(guard.GuardError, match="never publishes"):
        guard.check(method, "https://partner.steamgames.com" + path)
    assert not guard.browser_allows(method, "https://partner.steamgames.com" + path, "document")


def test_guard_allows_only_known_writes_on_steam() -> None:
    ok = [
        "/apps/setufsparameters/1000000",
        "/apps/setautocloudpath/1000000",
        "/apps/saveachievement/1000000",
        "/apps/deleteachievement/1000000/3/0",
        "/images/uploadachievement",
        "/admin/game/uploadloc/2000000",
        "/admin/game/save/2000000",
        "/apps/diff/1000000",
    ]
    for path in ok:
        guard.check("POST", "https://partner.steamgames.com" + path)
    for url in (
        "https://partner.steamgames.com/apps/setsomethingelse/1000000",
        "https://partner.steamgames.com/admin/game/savesomething/2000000",
        "https://store.steampowered.com/apps/setufsparameters/1000000",
    ):
        with pytest.raises(guard.GuardError, match="not an endpoint"):
            guard.check("POST", url)
    for url in (
        "http://partner.steamgames.com/apps/cloud/1",
        "https://partner.steamgames.com.evil.example/apps/cloud/1",
    ):
        with pytest.raises(guard.GuardError, match="Steam's domains"):
            guard.check("GET", url)


def test_browser_window_filter() -> None:
    allows = guard.browser_allows
    assert allows("GET", "https://partner.steamgames.com/apps/cloud/1000000", "document")
    assert allows("GET", "https://community.akamai.steamstatic.com/public/javascript/publishing.js", "script")
    assert not allows("GET", "https://partner.steamgames.com/apps/publishing/1000000", "xhr")
    assert not allows("POST", "https://partner.steamgames.com/apps/prepare/1000000", "fetch")
    assert not allows("POST", "https://partner.steamgames.com/apps/savesomething/1000000", "xhr")
    assert allows("POST", "https://partner.steamgames.com/login/settoken", "fetch")  # the user signing in
    assert allows("POST", "https://login.steampowered.com/jwt/finalizelogin", "fetch")
    assert allows("GET", "data:image/png;base64,AAAA", "image")


async def test_transports_refuse_before_sending() -> None:
    t = replay("cloud/read")
    with pytest.raises(guard.GuardError):
        await t.get("/apps/publishing/1000000")
    with pytest.raises(guard.GuardError):
        await t.post("/apps/prepare/1000000", {})

    class Page:
        url = "https://partner.steamgames.com/"
        calls = 0

        async def evaluate(self, *_: Any) -> Any:
            self.calls += 1
            return {"status": 200, "url": self.url, "text": "{}"}

    page = Page()
    pw = PlaywrightTransport(page)
    for call in (pw.get("/apps/publishing/1000000"), pw.post("/apps/revert/1000000", {})):
        with pytest.raises(guard.GuardError):
            await call
    assert page.calls == 0


# ------------------------------------------------------------------------------------------------ reading pages


def test_html_parser() -> None:
    page = parse(
        '<script>g_sessionID = "abc";</script><form name="F"><input id="q" name="quota" value="10">'
        '<input type="checkbox" name="on" checked><select name="s"><option value="a">A<option value="b" selected>B'
        '</select></form><input name="Launch_0_description_loc[english]" value="Play">'
    )
    assert page.session_id == "abc"
    assert page.forms["F"]["quota"] == "10" and page.forms["F"]["on"] is True and page.forms["F"]["s"] == "b"
    assert page.by_id["q"]["value"] == "10"
    assert page.named["Launch_0_description_loc[english]"] == "Play"


async def test_reads_match_the_recordings() -> None:
    cloud = await P.read_cloud(replay("cloud/read"), APP)
    assert cloud["byte_quota"] == 104857600 and cloud["file_quota"] == 100 and cloud["developers_only"] is True
    assert [r["pattern"] for r in cloud["roots"]] == ["*.sav"] and cloud["overrides"][0]["os"] == "MacOS"
    inst = await P.read_installation(replay("installation/read"), APP)
    assert inst["install_folder"] == "ExampleGame"
    assert inst["launch_options"][0]["executable"] == "ExampleGame.exe"
    assert inst["launch_options"][0]["descriptions"]["english"] == "Play ExampleGame"
    ach = await P.read_achievements(replay("achievements/readback"), APP)
    assert [a["api_name"] for a in ach["achievements"]] == ["SWMCP_REC_A"]
    t = replay("store/read")
    item = await P.store_item_id(t, APP)
    store = await P.read_store_localization(t, item)
    assert item == "2000000" and "app[content][short_description]" in store["languages"]["english"]


async def test_pending_changes_per_section() -> None:
    t = replay("visibility/after_writes")
    pending = await P.pending_changes(t, APP)
    assert "<" not in pending["text"] and "SWMCPRec.exe" in pending["text"]
    assert [(s.method, s.path) for s in t.sent] == [("POST", "/apps/diff/1000000")]
    sections = pending["sections"]
    assert sections["stats"]["new"] and sections["stats"]["changed"]  # a new section's content is not shown
    assert sections["config"]["changed"] and "SWMCPRec.exe" in "".join(sections["config"]["added"])
    assert not sections["common"]["changed"]  # only tabs differ
    assert pending["changed_sections"] == ["config", "stats", "ufs"]


def test_an_empty_block_left_by_a_deleted_row_is_not_a_change() -> None:
    diff = (
        '<br>=== Changes to "config" section (Revision 1 vs 2 )===<br><span>&quot;5&quot;\n{\n</span>'
        '<del class="diff_delete">\t&quot;installdir&quot;\t\t&quot;Game&quot;\n</del>'
        '<ins class="diff_insert">\t&quot;installdir&quot;\t&quot;Game&quot;\n\t&quot;launch&quot;\n\t{\n\t}\n</ins>'
    )
    assert P.parse_diff(diff)["config"]["changed"] is False


def test_a_saved_but_unchanged_section_is_not_a_change() -> None:
    diff = '<br>=== Changes to "ufs" section (Revision 1 vs 2 )===<br>'
    assert P.parse_diff(diff) == {"ufs": {"new": False, "removed": [], "added": [], "changed": False}}


async def test_release_checklists_are_read() -> None:
    items = await P.read_checklists(replay("visibility/before"), APP)
    by_name = {(i["checklist"], i["item"]): i for i in items}
    assert by_name[("Your Store Presence", "Descriptions")]["status"] == "complete"
    support = by_name[("Your Store Presence", "Support Info")]
    assert support["status"] == "incomplete" and "support contact" in support["explanation"]
    assert by_name[("Your Store Presence", "Cloud Saves")]["status"] == "suggested"
    assert ("Your Game Build", "Trailer Uploaded") in by_name and len(items) == 30


def test_every_checklist_item_has_a_gate_rule() -> None:
    from steamworks_mcp.gates.engine import gate_files

    items = P.parse_checklists(
        next(
            e["response"]["content"]["text"]
            for e in json.loads((FIX / "visibility/before.har").read_text("utf-8"))["log"]["entries"]
            if "/apps/landing/" in e["request"]["url"]
        )
    )
    linked = {r.steamworks_checklist for g in gate_files() for r in g.rules}
    assert {f"{i['checklist']} / {i['item']}" for i in items} <= linked
    # items Steamworks adds once a feature is on (seen live on an app with Steam Cloud and achievements)
    assert {
        "Your Store Presence / File Quota",
        "Your Store Presence / File Limit",
        "Your Game Build / Achievement Configured",
    } <= linked


async def test_expired_session_is_reported() -> None:
    with pytest.raises(NotLoggedInError, match="steamworks_open"):
        await P.read_achievements(replay("errors/session_expired"), APP)


async def test_changed_page_stops_before_writing(tmp_path: Path, project: Project, consent: Consent) -> None:
    har = tmp_path / "changed.har"
    entry = {
        "request": {"method": "GET", "url": f"https://partner.steamgames.com/apps/cloud/{APP}"},
        "response": {"status": 200, "content": {"text": "<html><body>A redesigned page</body></html>"}},
    }
    har.write_text(json.dumps({"log": {"entries": [entry]}}), encoding="utf-8")
    t = ReplayTransport([har])
    project = setv(project, {"apps.main.cloud.byte_quota": 1000})
    with pytest.raises(P.FormatError):
        await run_apply(project, consent, t, "cloud", dry_run=False, user_confirmed=True)
    assert sent_posts(t) == []


# ------------------------------------------------------------------------------------------------ planners


async def test_plans_leave_unmanaged_rows_and_languages_alone() -> None:
    current = await P.read_installation(replay("installation/read"), APP)
    values = {
        "apps": {
            "main": {
                "installation": {
                    "launch_options": [{"executable": "ExampleGame.exe", "description": "Play ExampleGame"}]
                }
            }
        }
    }
    desired = sync.desired_installation(values, "main", current, {})
    assert desired["launch_options"][0]["descriptions"]["turkish"]  # kept although this project does not manage it
    assert sync.plan_installation(APP, desired, current, remove_extra=False) == []
    assert len(sync.plan_installation(APP, desired, current, remove_extra=False, force=True)) == 2


async def test_cloud_plan_deletes_extra_rows_only_when_asked() -> None:
    before = await P.read_cloud(replay("cloud/read"), APP)
    after = await P.read_cloud(replay("cloud/readback"), APP)
    assert {op.action for op in sync.plan_cloud(APP, before, after, remove_extra=False)} == {"set"}
    ops = sync.plan_cloud(APP, before, after, remove_extra=True)
    assert [(op.action, op.target) for op in ops] == [
        ("delete", "root #1"),
        ("delete", "override #1"),
        ("set", "quotas and flags"),  # last: saving rows turns "developers only" back on
    ]


def test_achievements_with_a_progress_bar_are_left_alone() -> None:
    current = {
        "achievements": [
            {
                "api_name": "A",
                "stat_id": 1,
                "bit_id": 0,
                "display_name": "Old",
                "description": "d",
                "hidden": "0",
                "progress": {"min_val": 0},
            }
        ],
        "max_statid": "1",
        "max_bitid": "0",
    }
    ops = sync.plan_achievements(
        APP, [{"api_name": "A", "names": {"english": "New"}, "descriptions": {}, "hidden": False}], current, False
    )
    assert [(op.action, op.target) for op in ops] == [("skip", "A")]


async def test_saving_keeps_the_unlock_permission() -> None:
    current = {
        "achievements": [
            {
                "api_name": "A",
                "stat_id": 1,
                "bit_id": 0,
                "display_name": "Old",
                "description": "d",
                "hidden": "0",
                "permission": 2,
                "progress": False,
            }
        ],
        "max_statid": "1",
        "max_bitid": "0",
    }
    desired = [{"api_name": "A", "names": {"english": "New"}, "descriptions": {}, "hidden": False}]
    (op,) = sync.plan_achievements(APP, desired, current, False)

    sent: list[dict[str, str]] = []

    class Recorder:
        async def post(self, path: str, form: dict[str, str]) -> Any:
            sent.append(form)
            return Response(200, "https://partner.steamgames.com" + path, '{"success": 1, "saved": true}')

    await op.run(Recorder())  # type: ignore[arg-type]
    assert sent[0]["permission"] == "2" and sent[0]["displayname"] == '"New"'


def test_store_text_comparison_ignores_paragraph_tags() -> None:
    assert sync.normalize_store_text("[p]One[/p][p]Two[/p]") == sync.normalize_store_text("One\n\nTwo")
    # a translation without the editor's [p] tags still has the source's tags
    assert loc_store.tag_signature("[p]One[/p][h2]Two[/h2]") == loc_store.tag_signature("Eins\n[h2]Zwei[/h2]")


STORE_FORM = """<script>var g_sessionID = "abc";</script>
<form id="gameform" method="post" enctype="multipart/form-data">
<input type="hidden" name="serialized_app_data" value="SAD">
<input type="text" name="app[game][developers][1][name]_compl" value="">
<input type="hidden" name="app[game][developers][1][name]" value="">
<input type="text" name="app[game][publishers][0]_compl" value="">
<input type="hidden" name="app[game][publishers][0]" value="">
<input type="text" name="app[content][links][website]" value="https://old.example.com">
<input type="text" name="app[content][support_info][email]" value="">
<input type="hidden" name="app[content][legal][english]" value="">
<input type="hidden" name="app[content][sysreqs][windows][min][osversion][english]" value="">
<input name="app[content][sysreqs][windows][min][memory][amount]" value="">
<select name="app[content][sysreqs][windows][min][memory][units]"><option value="MB">MB</option>
<option value="GB" selected>GB</option></select>
<select name="app[content][sysreqs][windows][min][directx]"><option value="N/A">N/A</option>
<option value="11">11</option></select>
<input type="hidden" name="app[content][sysreqs][windows][min][broadband]" value="">
<input type="hidden" name="app[platforms][win]" value="1"><input type="hidden" name="app[platforms][mac]" value="1">
<input type="hidden" name="app[content][supported_languages][english][supported]" value="">
<input type="hidden" name="app[content][supported_languages][english][full_audio]" value="">
<input type="hidden" name="app[content][supported_languages][english][subtitles]" value="">
<input type="hidden" name="rgGenres[1]" value="" onchange="OnGenreSelect( this, '1', 'Action');">
<input type="hidden" name="rgGenres[23]" value="1" onchange="OnGenreSelect( this, '23', 'Indie');">
<select name="app[classification][primary_genre]"><option value="0">-</option><option value="1">Action</option></select>
<input type="hidden" name="app[classification][category][category_2]" value="true">
<input type="hidden" name="app[classification][category][category_38]" value="">
<input type="text" name="app[content][reviews][0][site]" value="untouched">
</form>""".replace("SAD", html.escape(json.dumps({"game": {"developers": [{"name": "Example Studio"}]}})))


def test_store_page_inputs_from_steamworks_yaml() -> None:
    current = store_page.read(parse(STORE_FORM).forms["gameform"], STORE_FORM)
    assert "app[content][reviews][0][site]" not in current["form"]  # only the inputs this section manages
    assert current["genres"] == {"Action": "1", "Indie": "23"}
    values = {
        "source_language": "english",
        "store": {
            "links": {"website": "https://studio.example.com"},
            "support": {"email": "", "url": None},  # empty: never sent, never clears Steam's value
            "legal": {"legal_line": "(c) 2026 Example Studio"},
            "system_requirements": {
                "windows": {"minimum": {"os": "Windows 10 64-bit", "memory": "8 GB", "directx": "Version 11"}}
            },
            "platforms": ["windows"],
            "supported_languages": {"english": {"interface": True, "full_audio": False, "subtitles": True}},
            "primary_genre": "Action",
            "genres": ["Action", "Indie", "Roguelike"],
            "categories": ["Single-player", "Online Co-op", "Full controller support"],
        },
    }
    want = store_page.desired(values, current, remove_extra=False)
    diff = store_page.changes(want["inputs"], current)
    assert diff == {
        "app[content][links][website]": "https://studio.example.com",
        "app[content][legal][english]": "(c) 2026 Example Studio",
        "app[content][sysreqs][windows][min][osversion][english]": "Windows 10 64-bit",
        "app[content][sysreqs][windows][min][memory][amount]": "8",
        "app[content][sysreqs][windows][min][directx]": "11",
        "app[content][supported_languages][english][supported]": "true",
        "app[content][supported_languages][english][subtitles]": "true",
        "rgGenres[1]": "true",
        "app[classification][primary_genre]": "1",
        "app[classification][category][category_38]": "true",
    }  # GB, platforms, Indie and Single-player already match; mac stays ticked without remove_extra
    assert any("Roguelike" in p for p in want["problems"])
    assert any("Controller Support wizard" in p for p in want["problems"])
    extra = store_page.changes(store_page.desired(values, current, remove_extra=True)["inputs"], current)
    assert extra["app[platforms][mac]"] == ""
    # names already on Steam stay; new ones go into the form's empty "add another" row
    assert current["developers"] == ["Example Studio"]
    people = {"store": {"developers": ["Example Studio", "Second Studio"], "publishers": ["Example Publishing"]}}
    added = store_page.changes(store_page.desired(people, current, False)["inputs"], current)
    assert added == {
        "app[game][developers][1][name]": "Second Studio",
        "app[game][developers][1][name]_compl": "Second Studio",
        "app[game][publishers][0]": "Example Publishing",
        "app[game][publishers][0]_compl": "Example Publishing",
    }
    assert store_page.size("1.5 GB") == ("1536", "MB") and store_page.size("500 MB") == ("500", "MB")
    assert store_page.size("lots") is None


async def test_store_page_save_posts_the_page_form_with_changes() -> None:
    sent: list[tuple[str, str, list[tuple[str, str | None]]]] = []

    class Recorder:
        async def submit_form(
            self, page: str, selector: str, action: str, changes: list[tuple[str, str | None]]
        ) -> Response:
            sent.append((page, action, changes))
            return Response(200, "https://partner.steamgames.com/admin/game/edit/2000000?msg=Changes+saved", "")

    await P.save_store_page(Recorder(), "2000000", {"app[content][links][website]": "https://x.example"})  # type: ignore[arg-type]
    assert sent == [
        (
            "/admin/game/edit/2000000",
            "/admin/game/save/2000000",
            [("app[content][links][website]", "https://x.example"), ("activetab", "tab_basic")],
        )
    ]

    class Unchanged(Recorder):  # an identical post: back on the edit page, without "Changes saved"
        async def submit_form(self, *a: Any) -> Response:
            return Response(200, "https://partner.steamgames.com/admin/game/edit/2000000?activetab=tab_basic", "")

    await P.save_store_page(Unchanged(), "2000000", {"x": "y"})  # type: ignore[arg-type]

    class Elsewhere(Recorder):
        async def submit_form(self, *a: Any) -> Response:
            return Response(200, "https://partner.steamgames.com/dashboard/", "")

    with pytest.raises(RuntimeError, match="unexpected page"):
        await P.save_store_page(Elsewhere(), "2000000", {"x": "y"})  # type: ignore[arg-type]


async def test_store_assets_fill_only_empty_slots(tmp_path: Path) -> None:
    for slot in ("header_capsule", "library_hero"):
        (tmp_path / f"{slot}.jpg").write_bytes(b"\xff\xd8 jpg")
    (tmp_path / "page_background.png").write_bytes(b"\x89PNG")
    want = store_assets.desired(tmp_path, {})
    assert set(want["images"]) == {"header_capsule", "library_hero", "page_background"}
    steam = {
        "header_image": {"image": {"english": "abc/header.jpg"}},
        "library_hero": {"image": {}},
        "page_background_raw": "",
    }
    current = {"item_id": "2000000", "slots": {s: store_assets.present(steam, s) for s in store_assets.SLOTS}}
    ops = store_assets.plan("2000000", want, current)
    assert [(op.action, op.target) for op in ops] == [
        ("skip", "header_capsule"),  # Steam has an image there: never replaced (all such slots in one line)
        ("upload", "page_background"),
        ("upload", "library_hero"),
    ]
    sent: list[tuple[str, dict[str, tuple[str, bytes, str]]]] = []

    class Recorder:
        async def post_multipart(
            self, path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
        ) -> Response:
            sent.append((path, files))
            return Response(200, "https://partner.steamgames.com" + path, '{"success": 1}')

    for op in ops:
        await op.run(Recorder())  # type: ignore[arg-type]
    assert [p for p, _ in sent] == ["/admin/game/save/2000000?activetab=tab_graphicalassets&json=1"] * 2
    assert list(sent[0][1]) == ["page_background|page_bg_raw|assets|page_background_raw"]  # not localized
    assert sent[0][1]["page_background|page_bg_raw|assets|page_background_raw"][2] == "image/png"
    assert list(sent[1][1]) == ["library_hero|library_hero|assets|library_hero|image|english"]

    position = {"pinned_position": "BottomLeft", "width_pct": 52.4, "height_pct": 70.5}
    placed = store_assets.plan("2000000", {"images": {}, "logo_position": position}, {**current, "logo_position": None})
    assert [(op.action, op.target) for op in placed] == [("set", "library logo position")]
    fields: list[dict[str, str]] = []

    class Fields(Recorder):
        async def post_multipart(
            self, path: str, f: dict[str, str], files: dict[str, tuple[str, bytes, str]]
        ) -> Response:
            fields.append(f)
            return Response(200, "https://partner.steamgames.com" + path, "")

    await placed[0].run(Fields())  # type: ignore[arg-type]
    assert fields[0]["app[assets][library_logo][logo_position][pinned_position]"] == "BottomLeft"
    assert fields[0]["app[assets][library_logo][logo_position][width_pct]"] == "52.4"
    same = {"pinned_position": "BottomLeft", "width_pct": "52.40001", "height_pct": "70.5"}
    assert (
        store_assets.plan("2000000", {"images": {}, "logo_position": position}, {**current, "logo_position": same})
        == []
    )


async def test_store_text_never_sends_an_empty_value() -> None:
    """Recorded on a test app: an empty value in the import clears the field on Steam."""
    short, about = "app[content][short_description]", "app[content][about]"
    current = {"languages": {"english": {short: "Old text", about: "Old about"}}}
    assert sync.plan_store(APP, "2000000", {"english": {short: "", about: "  "}}, current, force=True) == []
    sent: list[bytes] = []

    class Recorder:
        async def upload_store_localization(self, appid: int, name: str, data: bytes) -> Response:
            sent.append(data)
            return Response(200, "", '{"success": 1}')

    await P.upload_store_localization(
        Recorder(),  # type: ignore[arg-type]
        APP,
        {"itemid": "2000000", "languages": {"english": {short: "New", about: ""}, "german": {short: ""}}},
    )
    assert [json.loads(d)["languages"] for d in sent] == [{"english": {short: "New"}}]


# ------------------------------------------------------------------------------------------------ apply: the protocol

CLOUD = {
    "apps.main.cloud.byte_quota": 105906176,
    "apps.main.cloud.file_quota": 101,
    "apps.main.cloud.developers_only": True,
    "apps.main.cloud.sync_on_suspend": False,
    "apps.main.cloud.auto_cloud": [
        {
            "root": "WinAppDataLocalLow",
            "subdirectory": "RedactedGames/ExampleGame",
            "pattern": "*.sav",
            "recursive": True,
        },
        {"root": "WinAppDataLocalLow", "subdirectory": "SWMCPRec/Saves", "pattern": "*.sav", "recursive": True},
    ],
    "apps.main.cloud.overrides": [
        {
            "root": "WinAppDataLocalLow",
            "os": "macos",
            "use_instead": "MacAppSupport",
            "add_path": "unity.RedactedGames.ExampleGame",
            "replace_path": True,
        },
        {"root": "WinAppDataLocalLow", "os": "macos", "use_instead": "MacAppSupport", "add_path": "SWMCPRec"},
    ],
}


async def test_apply_dry_run_writes_nothing(project: Project, consent: Consent) -> None:
    project = setv(project, CLOUD)
    t = replay("cloud/read")
    out = await run_apply(project, consent, t, "cloud")
    assert out["dry_run"] is True and len(out["changes"]) == 3
    assert sent_posts(t) == []
    assert (project.files.snapshots_dir / f"{out['snapshot']}.json").exists()


async def test_apply_needs_confirmation_and_a_restore_first(project: Project, consent: Consent) -> None:
    project = setv(project, CLOUD)
    with pytest.raises(ApplyRefused, match="user_confirmed"):
        await run_apply(project, consent, replay("cloud/read"), "cloud", dry_run=False)
    t = replay("cloud/read")
    with pytest.raises(ApplyRefused, match="restore_snapshot"):
        await run_apply(project, consent, t, "cloud", dry_run=False, user_confirmed=True)
    assert sent_posts(t) == []


async def test_only_approved_values_are_written(project: Project, consent: Consent) -> None:
    project = setv(project, {"apps.main.cloud.byte_quota": 2000}, source="generated")
    consent.mark_restore_verified(APP)
    t = replay("cloud/read")
    out = await run_apply(project, consent, t, "cloud", dry_run=False, user_confirmed=True)
    assert out["not_approved"] == ["apps.main.cloud.byte_quota"]
    assert sent_posts(t) == []


async def test_apply_cloud_sends_what_the_page_sends(project: Project, consent: Consent) -> None:
    project = setv(project, CLOUD)
    consent.mark_restore_verified(APP)
    t = replay("cloud/read", "cloud/readback", "cloud/write")
    out = await run_apply(project, consent, t, "cloud", dry_run=False, user_confirmed=True)
    assert sorted(sent_posts(t)) == sorted(recorded_posts("cloud/write"))
    assert out["still_different"] == [] and "apps.main.cloud.byte_quota" in out["applied_fields"]
    assert Project.open(project.files.root).state.fields["apps.main.cloud.byte_quota"].status == "applied"
    entry = audit_entries(project)[-1]
    assert entry["action"] == "apply" and entry["section"] == "cloud" and len(entry["done"]) == 3
    assert not any(guard.FORBIDDEN.search(s.path) for s in t.sent)


async def test_apply_installation_keeps_other_languages(project: Project, consent: Consent) -> None:
    project = setv(
        project,
        {
            "target_languages": ["german"],
            "apps.main.installation.install_folder": "ExampleGame",
            "apps.main.installation.launch_options": [
                {"executable": "ExampleGame.exe", "description": "Play ExampleGame"},
                {
                    "executable": "SWMCPRec.exe",
                    "arguments": "-swmcp-rec",
                    "type": "option1",
                    "description": "Recording test option",
                },
            ],
        },
    )
    project = translate(
        project, "german", {"apps.main.installation.launch_options.1.description": "Aufnahme-Testoption"}
    )
    consent.mark_restore_verified(APP)
    t = replay("installation/read", "installation/readback", "installation/write")
    out = await run_apply(project, consent, t, "installation", dry_run=False, user_confirmed=True)
    launch = [p for p in recorded_posts("installation/write") if p[0].startswith("/apps/setlaunchoption")]
    assert sent_posts(t) == launch  # the install folder already matched; option 0 is untouched
    assert out["still_different"] == []
    assert "localization.german.apps.main.installation.launch_options.1.description" in out["applied_fields"]


async def test_apply_achievements_with_translations(project: Project, consent: Consent) -> None:
    project = setv(
        project,
        {
            "target_languages": ["german"],
            "achievements": [
                {
                    "id": "SWMCP_REC_A",
                    "name": "Recording Test A",
                    "description": "Fixture recording, safe to delete.",
                    "hidden": True,
                }
            ],
        },
    )
    project = translate(
        project,
        "german",
        {
            "achievements.SWMCP_REC_A.name": "Aufnahme-Test A",
            "achievements.SWMCP_REC_A.description": "Fixture-Aufnahme, kann gelöscht werden.",
        },
    )
    consent.mark_restore_verified(APP)
    t = replay("achievements/write", "achievements/readback")
    # The write step re-read the list before creating; drop that second read so the readback gets the recorded one.
    t.queue = [e for e in t.queue if not e["request"]["url"].endswith("/fetchachievements/1000000")]
    out = await run_apply(project, consent, t, "achievements", dry_run=False, user_confirmed=True)
    assert sent_posts(t) == recorded_posts("achievements/write")
    assert out["still_different"] == []
    assert set(out["applied_fields"]) >= {"achievements.SWMCP_REC_A.name", "achievements.SWMCP_REC_A.hidden"}
    assert "achievements.SWMCP_REC_A.icon" not in out["applied_fields"]


async def test_apply_store_text_uploads_only_what_changed(project: Project, consent: Consent) -> None:
    upload = json.loads((FIX / "uploads" / "store_loc_upload.json").read_text(encoding="utf-8"))
    short = upload["languages"]["english"]["app[content][short_description]"]
    project = setv(
        project, {"store.short_description": short, "store.about": "steamworks-mcp test: About This Game placeholder."}
    )
    consent.mark_restore_verified(APP)
    t = replay("store/read", "store/readback", "store/write", "cleanup/store")
    out = await run_apply(project, consent, t, "store_text", dry_run=False, user_confirmed=True)
    uploads = [json.loads(s.form["json"]) for s in t.sent if s.method == "UPLOAD"]
    assert uploads == [upload]
    assert out["still_different"] == [] and set(out["applied_fields"]) == {"store.short_description", "store.about"}


async def test_a_failed_write_is_reported_and_audited(project: Project, consent: Consent) -> None:
    project = setv(project, CLOUD)
    consent.mark_restore_verified(APP)
    t = replay("cloud/read", "cloud/readback")  # no recorded answers for the writes: the first one fails
    out = await run_apply(project, consent, t, "cloud", dry_run=False, user_confirmed=True)
    assert "Stopped after 0 of 3" in out["error"] and out["applied_fields"] == []
    assert audit_entries(project)[-1]["error"]


# ------------------------------------------------------------------------------------------------ restore


async def snapshot_of(project: Project, step: str) -> str:
    data = await P.read_cloud(replay(step), APP)
    return save_snapshot(project.files, "main", APP, "cloud", data)


async def test_first_restore_is_a_round_trip(project: Project, tmp_path: Path) -> None:
    consent = Consent(tmp_path / "c.json")
    sid = await snapshot_of(project, "cloud/read")
    dry = await restore_snapshot(replay("cloud/read"), project.files, consent, sid)
    assert dry["dry_run"] and len(dry["changes"]) == 3 and "First restore" in dry["note"]
    t = replay("cloud/read", "cloud/read", "cloud/write")
    out = await restore_snapshot(t, project.files, consent, sid, dry_run=False, user_confirmed=True)
    assert out["restore_verified"] is True and consent.restore_verified(APP)
    ufs = next(form for path, form in sent_posts(t) if path.startswith("/apps/setufsparameters"))
    assert ufs["cb"] == "104857600" and ufs["cfiles"] == "100"  # written back unchanged


async def test_restore_undoes_an_apply(project: Project, consent: Consent) -> None:
    consent.mark_restore_verified(APP)
    sid = await snapshot_of(project, "cloud/read")
    t = replay("cleanup/cloud", "cloud/read")
    with pytest.raises(ApplyRefused, match="user_confirmed"):
        await restore_snapshot(replay("cleanup/cloud"), project.files, consent, sid, dry_run=False)
    out = await restore_snapshot(t, project.files, consent, sid, dry_run=False, user_confirmed=True)
    assert out["restore_verified"] is True and out["still_different"] == []
    sent = sent_posts(t)
    assert (
        "/apps/setautocloudpath/1000000",
        {"index": "1", "root": "", "path": "", "pattern": "", "oslist": ""},
    ) in sent
    assert audit_entries(project)[-1]["action"] == "restore"


# ------------------------------------------------------------------------------------------------ Web API


def mock_api(handler: Callable[[httpx.Request], httpx.Response]) -> PartnerApi:
    return PartnerApi(KEY, httpx.Client(transport=httpx.MockTransport(handler)))


def params(request: httpx.Request) -> dict[str, str]:
    if request.method == "GET":
        return dict(request.url.params)
    return dict(parse_qsl(request.content.decode()))


def test_api_sends_the_documented_parameters() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, params(request)))
        if request.url.path.endswith("GetLeaderboardsForGame/v2/"):
            return httpx.Response(
                200, json={"response": {"leaderboards": [{"name": "BEST", "sortmethod": "Descending"}]}}
            )
        if request.url.path.endswith("SetAppBuildLive/v2/"):
            return httpx.Response(201, json={"response": {"result": 1}})
        board = {"leaderBoardID": 7, "leaderboardName": "TIME", "leaderBoardDisplayType": "Seconds"}
        return httpx.Response(200, json={"result": {"result": 1, "leaderboard": board}})

    api = mock_api(handler)
    assert api.leaderboards(APP)[0]["name"] == "BEST"
    made = api.find_or_create_leaderboard(APP, {"name": "TIME", "sort_method": "ascending", "display_type": "seconds"})
    assert made["id"] == 7 and made["displaytype"] == "Seconds"
    live = api.set_build_live(APP, 42, "beta")
    assert live["needs_mobile_confirmation"] is True
    with pytest.raises(SteamApiError, match="never sets the default branch"):
        api.set_build_live(APP, 42, "public")
    create = dict(seen)["/ISteamLeaderboards/FindOrCreateLeaderboard/v2/"]
    assert create["sortmethod"] == "Ascending" and create["displaytype"] == "Seconds"
    assert create["createifnotfound"] == "true" and create["onlytrustedwrites"] == "false"
    setlive = dict(seen)["/ISteamApps/SetAppBuildLive/v2/"]
    assert setlive["betakey"] == "beta" and setlive["buildid"] == "42"
    assert all(p["key"] == KEY for _, p in seen)
    assert KEY not in repr(api)


def test_api_errors_never_show_the_key() -> None:
    api = mock_api(lambda r: httpx.Response(500, text=f"boom {r.url}"))
    with pytest.raises(SteamApiError) as exc:
        api.builds(APP)
    assert KEY not in str(exc.value) and "<key>" in str(exc.value)
    with pytest.raises(SteamApiError, match="publisher key was rejected"):
        mock_api(lambda r: httpx.Response(403)).betas(APP)


def recorded_api(step: str) -> tuple[PartnerApi, list[dict[str, Any]]]:
    """A PartnerApi that answers with the recorded responses of ``step``, in order. Each request must send what the
    recording sent (the key aside); the list left over shows which recorded calls were not made."""
    pairs: list[dict[str, Any]] = json.loads((FIX / f"{step}.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        want = pairs.pop(0)
        assert (request.method, request.url.path) == (want["method"], urlsplit(want["url"]).path)
        recorded = want.get("request") if want["method"] == "POST" else want.get("query")
        sent = {k: v for k, v in params(request).items() if k != "key"}
        assert sent == {k: v for k, v in (recorded or {}).items() if k != "key"}
        return httpx.Response(want["status"], json=want["response"])

    return mock_api(handler), pairs


def test_api_reads_as_recorded_on_an_app_without_builds() -> None:
    api, left = recorded_api("api/read")
    assert api.schema(APP) == {}  # nothing published yet
    with pytest.raises(SteamApiError, match=r"HTTP 500.*without any build yet") as exc:
        api.builds(APP, 5)
    assert exc.value.status == 500
    with pytest.raises(SteamApiError, match=r"HTTP 500.*SteamPipe > Builds"):
        api.betas(APP)
    assert api.leaderboards(APP) == [] and left == []


def test_api_lists_builds_as_recorded() -> None:
    api, left = recorded_api("api/builds")
    build = api.builds(APP, 5)["builds"]["4000001"]
    assert build["Description"] == "BuildTest (main)" and list(build["depots"]) == ["3000001"]
    with pytest.raises(SteamApiError, match="with and without an uploaded build"):
        api.betas(APP)  # still HTTP 500 on an unreleased app that has a build
    assert left == []


def test_leaderboard_calls_as_recorded() -> None:
    api, left = recorded_api("api/leaderboard_write")
    board = {"name": "SWMCP_REC_BOARD", "sort_method": "descending", "display_type": "numeric"}
    made = api.find_or_create_leaderboard(APP, board)
    assert made["id"] > 0 and made["displaytype"] == "Numeric" and made["sortmethod"] == "Descending"
    assert api.find_leaderboard(APP, "SWMCP_REC_BOARD") == made
    assert api.leaderboards(APP) == []  # the cached list does not show the new board yet
    assert left == []

    api, left = recorded_api("api/leaderboard_delete")
    assert api.delete_leaderboard(APP, "SWMCP_REC_BOARD") is True
    assert api.delete_leaderboard(APP, "SWMCP_REC_BOARD") is False  # result 2: no such board
    assert api.find_leaderboard(APP, "SWMCP_REC_BOARD") is None  # leaderBoardID 0
    assert api.leaderboards(APP) == [] and left == []


def test_display_type_names_as_recorded() -> None:
    """Steam stores the Web API names; the SDK's (TimeSeconds...) are accepted but leave the display type empty."""
    stored = {}
    for e in json.loads((FIX / "api/leaderboard_display_types.json").read_text(encoding="utf-8")):
        if e["request"].get("createifnotfound") == "true":
            stored[e["request"]["displaytype"]] = e["response"]["result"]["leaderboard"]["leaderBoardDisplayType"]
    assert stored == {"Seconds": "Seconds", "MilliSeconds": "MilliSeconds", "TimeSeconds": "", "TimeMilliSeconds": ""}
    assert set(DISPLAY_TYPES.values()) == {"Numeric", "Seconds", "MilliSeconds"}
    empty = {"name": "T", "sortmethod": "Ascending", "displaytype": ""}
    plan = plan_leaderboards([{"name": "T", "sort_method": "ascending", "display_type": "seconds"}], [empty], False)
    assert plan["settings_differ"][0]["steam"]["display_type"] == "unset"


def test_leaderboard_apply_reads_back_past_the_cached_list(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    proj.init_project(root, "ExampleGame", APP)
    project = setv(
        Project.open(root), {"leaderboards": [{"name": "FAST", "sort_method": "ascending", "display_type": "seconds"}]}
    )
    boards: dict[str, dict[str, Any]] = {"OLD": {"leaderBoardID": 3, "leaderboardName": "OLD"}}
    cached = [{"id": 3, "name": "OLD", "sortmethod": "Descending", "displaytype": "Numeric"}]

    def handler(request: httpx.Request) -> httpx.Response:
        p, path = params(request), request.url.path
        if path.endswith("GetLeaderboardsForGame/v2/"):
            return httpx.Response(200, json={"response": {"result": 1, "leaderboards": cached}})
        if path.endswith("DeleteLeaderboard/v1/"):
            return httpx.Response(200, json={"result": {"result": 1 if boards.pop(p["name"], None) else 2}})
        if p["createifnotfound"] == "true":
            boards[p["name"]] = {
                "leaderBoardID": 9,
                "leaderboardName": p["name"],
                "leaderBoardSortMethod": p["sortmethod"],
                "leaderBoardDisplayType": p["displaytype"],
                "onlytrustedwrites": False,
                "onlyfriendsreads": False,
            }
        found = boards.get(p["name"], {"leaderBoardID": 0, "leaderboardName": p["name"]})
        return httpx.Response(200, json={"result": {"result": 1, "leaderboard": found}})

    config = Config(workspace_root=tmp_path, publisher_key=KEY, home_dir=tmp_path / "home")
    ex = Executor(config, api=mock_api(handler))
    out = ex.apply_leaderboards(project, "main", dry_run=False, user_confirmed=True, remove_extra=True)
    assert out["done"] == [{"action": "create", "name": "FAST"}, {"action": "delete", "name": "OLD"}]
    assert out["still_different"] == {"create": [], "delete": [], "settings_differ": []}
    assert out["applied_fields"] and "error" not in out
    # a minute later the cached list still shows OLD and not FAST: FAST is not created twice, OLD is already gone
    again = ex.apply_leaderboards(project, "main", dry_run=False, user_confirmed=True, remove_extra=True)
    assert again["done"] == [{"action": "delete", "name": "OLD", "already_gone": True}]
    assert again["still_different"] == {"create": [], "delete": [], "settings_differ": []}


async def test_import_fills_only_empty_fields(project: Project, consent: Consent, tmp_path: Path) -> None:
    project = setv(project, {"store.about": "My own about text."})
    root = project.files.root

    async def transport() -> ReplayTransport:
        return replay("store/read", "achievements/read", "cloud/read", "installation/read", "visibility/before")

    ex = Executor(
        Config(workspace_root=tmp_path, browser_enabled=True, home_dir=tmp_path / "home"), transport_factory=transport
    )
    sections = ["store_text", "achievements", "cloud", "installation", "checklist"]
    dry = await ex.import_from_steamworks(project, "main", sections, dry_run=True)
    assert [c["field"] for c in dry["conflicts"]] == ["store.about"]
    assert {"store.short_description", "apps.main.cloud.byte_quota", "apps.main.installation.launch_options"} <= set(
        dry["fill"]
    )
    assert Project.open(root).values()["store"]["short_description"] is None  # a dry run saves nothing

    out = await ex.import_from_steamworks(Project.open(root), "main", sections, dry_run=False)
    p = Project.open(root)
    assert p.values()["store"]["about"] == "My own about text."  # never overwritten
    short = p.state.get("store.short_description")
    assert (short.status, short.source) == ("applied", "steamworks") and short.applied_at is not None
    assert p.state.get("checklist.depots_configured").status == "applied"
    assert p.values()["target_languages"] == ["german", "turkish"]
    assert p.state.get("localization.german.store.short_description").status == "applied"
    assert "localization.german.store.about" not in p.state.fields  # the file's About differs: no translation of it
    assert out["confirm_with_user"]["values"]["prerequisites.partner_account"] is True
    assert p.values()["prerequisites"]["partner_account"] is None  # gate 0 is the user's to confirm
    assert audit_entries(p)[-1]["action"] == "import_from_steamworks"


def test_import_turns_editor_paragraph_tags_into_blank_lines() -> None:
    loc = {
        "languages": {
            "english": {"app[content][about]": "[p]One[/p][p]Two[/p]"},
            "german": {"app[content][about]": "Eins"},
        }
    }
    found, translations = importer.store_text(loc, "english")
    assert found == {"store.about": "One\n\nTwo"} and translations == {"german": {"store.about": "Eins"}}


def test_import_leaderboards_skips_boards_without_display_type() -> None:
    boards = [
        {"name": "FAST", "sortmethod": "Ascending", "displaytype": "Seconds"},
        {"name": "BROKEN", "sortmethod": "Ascending", "displaytype": ""},
    ]
    found, notes = importer.leaderboards(boards)
    assert found == {
        "leaderboards.FAST": {
            "sort_method": "ascending",
            "display_type": "seconds",
            "only_trusted_writes": False,
            "only_friends_reads": False,
        }
    }
    assert "BROKEN" in notes[0]


def test_leaderboard_plan() -> None:
    desired = [
        {"name": "BEST", "sort_method": "descending", "display_type": "numeric"},
        {"name": "FAST", "sort_method": "ascending", "display_type": "milliseconds"},
    ]
    current = [
        {"name": "BEST", "sortmethod": "Ascending", "displaytype": "Numeric"},
        {"name": "OLD", "sortmethod": "Descending", "displaytype": "Numeric"},
    ]
    plan = plan_leaderboards(desired, current, remove_extra=False)
    assert [b["name"] for b in plan["create"]] == ["FAST"] and plan["delete"] == []
    assert plan["settings_differ"][0]["name"] == "BEST"
    assert plan_leaderboards(desired, current, remove_extra=True)["delete"] == ["OLD"]


def test_steamcmd_never_gets_a_password_and_hides_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe, script = tmp_path / "steamcmd.exe", tmp_path / "app_build_1000000.vdf"
    exe.write_text("", encoding="utf-8")
    script.write_text('"AppBuild" {}', encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        log = "Logging in user 'builder_acct' [U:1:123456] to Steam Public...OK\n"
        log += "Cached credentials found.\nBuildID 4242\n"
        log += "Successfully finished AppID 1000000 build\n"
        return subprocess.CompletedProcess(cmd, 0, log + "Using password token xyz\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = run_steamcmd(str(exe), "builder_acct", script)
    assert calls == [[str(exe), "+login", "builder_acct", "+run_app_build", str(script), "+quit"]]
    assert out["success"] is True and out["build_id"] == 4242
    assert "builder_acct" not in out["log_tail"] and "token xyz" not in out["log_tail"]
    assert "123456" not in out["log_tail"] and "<steam id>" in out["log_tail"]
    with pytest.raises(SteamApiError, match="STEAMCMD_USERNAME"):
        run_steamcmd(str(exe), "bad name; rm", script)
    with pytest.raises(SteamApiError, match="not found"):
        run_steamcmd(str(tmp_path / "missing.exe"), "builder_acct", script)


# ------------------------------------------------------------------------------------------------ MCP tools


def test_config_reads_the_execution_settings(tmp_path: Path) -> None:
    env = {"STEAMCMD_USERNAME": "builder_acct", "STEAMWORKS_MCP_HOME": str(tmp_path / "h"), "STEAM_MCP_BROWSER": "1"}
    config = load_config(env, dotenv=tmp_path / "none.env")
    assert config.steamcmd_username == "builder_acct" and config.browser_enabled
    assert config.consent_path == tmp_path / "h" / "consent.json"


async def test_browser_tools_exist_only_when_enabled(tmp_path: Path) -> None:
    for enabled in (False, True):
        config = Config(workspace_root=tmp_path, browser_enabled=enabled, home_dir=tmp_path / "home")
        async with Client(create_server(config)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
        assert {"apply", "steamworks_inspect", "set_build_live"} <= names
        assert ("steamworks_open" in names) is enabled and ("restore_snapshot" in names) is enabled


async def test_browser_flow_through_the_server(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    proj.init_project(root, "ExampleGame", APP)
    project = setv(Project.open(root), CLOUD)
    config = Config(workspace_root=tmp_path, browser_enabled=True, home_dir=tmp_path / "home")

    async def transport() -> ReplayTransport:
        return replay("cloud/read")

    async with Client(create_server(config, Executor(config, transport_factory=transport))) as client:

        async def call(tool: str, **args: Any) -> Any:
            return await client.call_tool(tool, args)

        refused = await call("apply", path="game", section="cloud")
        assert refused.is_error and "steamworks_open" in refused.content[0].text
        terms = (await call("steamworks_open")).structured_content
        assert terms["consent_required"] and "never publishes" in terms["text"]
        assert (await call("steamworks_open", accept_risks=True)).structured_content == {"logged_in": True}
        dry = (await call("apply", path="game", section="cloud")).structured_content
        assert dry["dry_run"] and len(dry["changes"]) == 3
        snaps = (await call("steamworks_inspect", path="game", what="snapshots")).structured_content
        assert snaps["snapshots"] == [dry["snapshot"]]
        blocked = await call("apply", path="game", section="cloud", dry_run=False, user_confirmed=True)
        assert blocked.is_error and "restore_snapshot" in blocked.content[0].text
    assert json.loads(config.consent_path.read_text(encoding="utf-8"))["version"] == 1
    assert project.files.snapshots_dir.exists()


async def test_browser_sections_without_browser_mode_point_to_exports(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    proj.init_project(root, "ExampleGame", APP)
    config = Config(workspace_root=tmp_path, home_dir=tmp_path / "home")
    async with Client(create_server(config)) as client:
        out = await client.call_tool("apply", {"path": "game", "section": "cloud"})
    assert out.is_error and "export_package" in out.content[0].text  # type: ignore[union-attr]


def test_build_upload_dry_run_and_set_live(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "Builds" / "win").mkdir(parents=True)
    (root / "Builds" / "win" / "Game.exe").write_bytes(b"MZ")
    proj.init_project(root, "ExampleGame", APP)
    project = setv(
        Project.open(root),
        {"apps.main.builds.depots": [{"name": "windows", "depot_id": 1000001, "content_root": "Builds/win"}]},
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"response": {"builds": {}, "betas": {}}})

    config = Config(workspace_root=tmp_path, publisher_key=KEY, home_dir=tmp_path / "home")
    ex = Executor(config, api=mock_api(handler))
    dry = ex.upload_build(project, "main", dry_run=True, user_confirmed=False)
    assert dry["problems"] == [] and len(dry["setup_missing"]) == 2 and "nothing is set live" in dry["set_live"]
    assert (root / dry["scripts"][0]).read_text(encoding="utf-8").startswith('"AppBuild"')
    with pytest.raises(ApplyRefused, match="STEAMCMD_PATH"):
        ex.upload_build(project, "main", dry_run=False, user_confirmed=True)
    for branch in ("default", "public"):
        with pytest.raises(ApplyRefused, match="set live by hand"):
            ex.set_build_live(project, "main", 7, branch, description="", dry_run=False, user_confirmed=True)
    live = ex.set_build_live(project, "main", 7, "beta", description="", dry_run=True, user_confirmed=False)
    assert "beta branch 'beta'" in live["warning"] and not any("SetAppBuildLive" in p for p in seen)
    with pytest.raises(ApplyRefused, match="user_confirmed"):
        ex.set_build_live(project, "main", 7, "beta", description="", dry_run=False, user_confirmed=False)
