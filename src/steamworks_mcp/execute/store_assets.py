"""Store and library images (Edit Store Page > Graphical Assets), uploaded from prepare_images' output.

The page posts each image as one multipart part to ``/admin/game/save/<storeItemId>?activetab=tab_graphicalassets
&json=1``; the part's name says the slot (and, for localized slots, the language), e.g.
``header_image|header|assets|header_image|image|english``. What Steam has is in the store item's
``serialized_app_data.assets``. Uploads land in the unpublished draft.

Only empty slots are filled: an image Steam already has is never replaced by this tool, and the tool has no way to
remove an uploaded image, so there is nothing to restore (the user deletes images on the Graphical Assets tab).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from steamworks_mcp.execute import sync
from steamworks_mcp.execute.browser import partner as P
from steamworks_mcp.execute.browser.transport import Transport

SECTION = "store_assets"
SLOTS = {
    "header_capsule": ("header_image|header|assets|header_image|image", "header_image"),
    "small_capsule": ("small_capsule|capsule|assets|small_capsule|image", "small_capsule"),
    "main_capsule": ("main_capsule|capsule_616x353|assets|main_capsule|image", "main_capsule"),
    "vertical_capsule": ("hero_capsule|hero_capsule|assets|hero_capsule|image", "hero_capsule"),
    "page_background": ("page_background|page_bg_raw|assets|page_background_raw", "page_background_raw"),
    "library_capsule": ("library_capsule|library_capsule|assets|library_capsule|image", "library_capsule"),
    "library_header": ("library_header|library_header|assets|library_header|image", "library_header"),
    "library_hero": ("library_hero|library_hero|assets|library_hero|image", "library_hero"),
    "library_logo": ("library_logo|logo|assets|library_logo|image", "library_logo"),
}
"""asset_specs.yaml id -> (upload part name without the language, key in serialized_app_data.assets)."""
LANGUAGE = "english"
"""Base images are uploaded for English, the language Steam falls back to."""
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def present(assets: dict[str, Any], slot: str) -> bool:
    key = SLOTS[slot][1]
    if slot == "page_background":
        return bool(assets.get(key))
    return bool(((assets.get(key) or {}).get("image") or {}).get(LANGUAGE))


def serialized_app_data(page: str) -> dict[str, Any]:
    m = re.search(r'name="serialized_app_data"[^>]*value="([^"]*)"', page) or re.search(
        r'value="([^"]*)"[^>]*name="serialized_app_data"', page
    )
    if not m:
        raise P.FormatError("The store page changed (serialized_app_data not found).")
    return dict(json.loads(html.unescape(m.group(1))))


async def read_section(t: Transport, appid: int) -> dict[str, Any]:
    item = await P.store_item_id(t, appid)
    _, page = await P.read_store_form(t, item)
    assets = serialized_app_data(page).get("assets") or {}
    return {"item_id": item, "slots": {slot: present(assets, slot) for slot in SLOTS}}


def desired(images_dir: Path) -> dict[str, Path]:
    """The prepared image of each slot (``.steam-mcp/exports/images/<slot>.jpg|png``)."""
    out = {}
    for slot in SLOTS:
        for ext in (".jpg", ".png"):
            if (images_dir / f"{slot}{ext}").is_file():
                out[slot] = images_dir / f"{slot}{ext}"
                break
    return out


def plan(item_id: str, want: dict[str, Path], current: dict[str, Any]) -> list[sync.Op]:
    ops: list[sync.Op] = []
    for slot, file in want.items():
        if current["slots"].get(slot):
            ops.append(
                sync.Op(
                    SECTION,
                    "skip",
                    slot,
                    "Steam has an image",
                    "Not replaced: this tool only fills empty slots. Replace it on the Graphical Assets tab.",
                    sync._nothing,
                )
            )
            continue

        async def upload(t: Transport, s: str = slot, f: Path = file) -> None:
            await P.upload_store_image(
                t,
                item_id,
                f"{SLOTS[s][0]}|{LANGUAGE}" if s != "page_background" else SLOTS[s][0],
                f.name,
                f.read_bytes(),
                MIME[f.suffix.lower()],
            )

        ops.append(sync.Op(SECTION, "upload", slot, None, file.name, upload))
    return ops
