"""Smoke test: the recorded Steamworks fixtures load, replay, and still show the behaviour they were recorded for."""

from __future__ import annotations

import pytest
from steamworks_har import APP_ID, SESSION_ID, STORE_ITEM_ID, Replay, load, steps


@pytest.mark.parametrize("step", steps())
def test_every_step_loads(step: str) -> None:
    for x in load(step):
        assert x.method in {"GET", "POST"}
        assert x.url.startswith("https://")


def test_store_text_download_and_partial_upload() -> None:
    r = Replay("store/read", "store/write", "store/readback")
    loc = r.take("GET", f"/admin/game/downloadloc/{STORE_ITEM_ID}").json()
    assert loc["itemid"] == str(STORE_ITEM_ID)
    english = loc["languages"]["english"]
    assert set(english) == {"app[content][about]", "app[content][short_description]"}

    upload = r.take("POST", f"/admin/game/uploadloc/{STORE_ITEM_ID}")
    assert upload.status == 302
    assert upload.redirect_url.endswith("activetab=tab_localization")
    assert 'name="localization_files[]"' in upload.request_body

    after = r.take("GET", f"/admin/game/downloadloc/{STORE_ITEM_ID}").json()["languages"]["english"]
    assert after["app[content][short_description]"].endswith("(recording)")
    assert after["app[content][about]"] == english["app[content][about]"]  # untouched field kept


def test_achievement_create_save_icon_delete() -> None:
    r = Replay("achievements/write", "cleanup/achievements")
    created = r.take("POST", f"/apps/newachievement/{APP_ID}").json()
    assert created["success"] == 1
    ach = created["achievement"]
    assert ach["api_name"] == f"NEW_ACHIEVEMENT_{ach['stat_id']}_{ach['bit_id']}"

    save = r.take("POST", f"/apps/saveachievement/{APP_ID}")
    assert save.form["sessionid"] == SESSION_ID
    assert save.json()["saved"] is True

    icon = r.take("POST", "/images/uploadachievement")
    assert icon.json()["success"] is True

    deleted = r.take("POST", f"/apps/deleteachievement/{APP_ID}/{ach['stat_id']}/{ach['bit_id']}")
    assert deleted.json() == {"deleted": True}


def test_cloud_endpoints_and_server_side_clamping() -> None:
    r = Replay("cloud/write")
    assert r.take("POST", f"/apps/setufsparameters/{APP_ID}").json()["success"] is True
    path = r.take("POST", f"/apps/setautocloudpath/{APP_ID}")
    assert {"index", "root", "path", "pattern", "oslist", "recursive"} <= set(path.form)
    assert r.take("POST", f"/apps/setautocloudoverride/{APP_ID}").json()["success"] is True

    quota = Replay("errors/cloud_quota_too_big")
    too_big = quota.take("POST", f"/apps/setufsparameters/{APP_ID}", cb="10000000001")
    assert too_big.json()["success"] is True  # accepted; the page then shows 10,000,000,000


def test_launch_option_delete_is_an_all_empty_row() -> None:
    r = Replay("cleanup/installation")
    x = r.take("POST", f"/apps/setlaunchoption/{APP_ID}")
    assert x.form["index"]
    assert all(v == "" for k, v in x.form.items() if k not in {"index", "sessionid"})


def test_publish_page_diff_is_read_only_and_lists_drafts() -> None:
    before = Replay("visibility/before").take("POST", f"/apps/diff/{APP_ID}", section="technical").json()
    during = Replay("visibility/after_writes").take("POST", f"/apps/diff/{APP_ID}", section="technical").json()
    assert "uncommitted changes" in before["opened"]
    assert "SWMCPRec.exe" in during["diff"] and "SWMCPRec.exe" not in before["diff"]


def test_expired_session_redirects_ajax_to_the_sign_in_page() -> None:
    xs = load("errors/session_expired")
    landing = [x for x in xs if x.resource_type == "document"]
    assert any(x.status == 302 and "goto=" in x.redirect_url for x in landing)
    # AJAX without a session: 302 to "?goto=…", and fetch() follows it to an HTML page with status 200.
    ajax = next(x for x in xs if x.path == f"/apps/fetchachievements/{APP_ID}")
    assert ajax.status == 302 and "goto=" in ajax.redirect_url
    followed = [x for x in xs if x.resource_type == "fetch" and x.status == 200 and x.path in {"", "/"}]
    assert followed and (followed[0].response_body or "").lstrip().lower().startswith("<!doctype html")
