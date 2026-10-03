"""Store tags applied by the developer (Steamworks' Tag Wizard, ``/taxonomy/tagwizard/<appid>``).

Unlike everything else this tool writes, tags are not a draft: the wizard's Publish posts them to
``/tagdata/forcetagranking`` and Steam applies them to the store at once ("Your changes have been successfully
published to Steam"). So apply(section="store_tags") needs ``goes_live_now=true`` on top of ``user_confirmed=true``.
Community tags are never removed (the wizard's ``negatedtagids`` stays empty).
"""

from __future__ import annotations

import json
from typing import Any

from steamworks_mcp.execute import sync
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport

SECTION = "store_tags"
MAX_TAGS = 20
GOES_LIVE = (
    "Store tags go live on the store at once: Steam publishes them when they are saved. Call again with "
    "goes_live_now=true only if the user agreed to that."
)


def _ids(tags: list[Any]) -> list[int]:
    return [int(t["tagid"] if isinstance(t, dict) else t) for t in tags]


async def read_section(t: Transport, appid: int) -> dict[str, Any]:
    res = P._checked(await t.get(f"/taxonomy/tagwizard/{appid}"), "Tag Wizard")
    start = res.text.find("initSurvey(")
    brace = res.text.find("{", start)
    if start < 0 or brace < 0:
        raise P.FormatError("The Tag Wizard changed (its data was not found).")
    data, _ = json.JSONDecoder().raw_decode(res.text, brace)
    names = {int(k): v for k, v in (data.get("tagNames") or {}).items()}
    return {
        "applied": _ids(data.get("devTags") or []),
        "community": _ids(data.get("communityTags") or []),
        "names": names,
    }


def desired(values: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    ids = {name.lower(): tid for tid, name in current["names"].items()}
    wanted, problems = [], []
    for name in (values.get("store") or {}).get("tags") or []:
        if name.lower() in ids:
            wanted.append(ids[name.lower()])
        else:
            problems.append(f"tag {name!r}: not a Steam tag")
    if len(wanted) > MAX_TAGS:
        problems.append(f"{len(wanted)} tags: the Tag Wizard takes {MAX_TAGS} at most; the first {MAX_TAGS} are used")
    return {"tags": list(dict.fromkeys(wanted))[:MAX_TAGS], "problems": problems}


def plan(appid: int, want: dict[str, Any], current: dict[str, Any], force: bool = False) -> list[sync.Op]:
    names = current["names"]
    ops = [sync.Op(SECTION, "skip", p, None, "not written", sync._nothing) for p in want["problems"]]
    if want["tags"] and (force or want["tags"] != current["applied"]):

        async def publish(t: Transport, tags: list[int] = want["tags"]) -> None:
            await P.apply_store_tags(t, appid, tags)

        ops.append(
            sync.Op(
                SECTION,
                "publish now",
                "store tags (live at once)",
                [names.get(i, i) for i in current["applied"]],
                [names.get(i, i) for i in want["tags"]],
                publish,
            )
        )
    return ops
