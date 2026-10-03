---
name: steamworks-store-assets
description: Every Steam store and library image - capsules, library hero, logo and capsule, page background, app and achievement icons - cut at exact sizes from one key art and one logo, checked against Valve's graphical asset rules, plus screenshots and trailers. Use when the user prepares store images, asks for capsule sizes, or gets assets rejected.
---

# Store and library images

Tools: `prepare_images`, `set_field`, `get_spec_info`, `apply`, `steamworks_inspect`, `export_package`, `gap_report`

## 1. Inputs

- `assets.key_art`: one large, clean artwork without text (at least as big as the largest image Steam wants).
- `assets.logo`: the game's logo on a transparent background.
- `assets.key_art_focus`: where the important part of the art is, so crops keep it.
- `assets.overrides.<id>`: a hand-made image for any slot (designers often redo the small capsule).

Save the paths with `set_field`. `get_spec_info("asset_specs")` lists every slot with its size, format and rules.

## 2. Make them

`prepare_images(path)` cuts every image at the exact size, crops around the focus, never stretches, reports any
upscaling, and makes achievement icons (256x256 JPG, with greyscale locked versions) from `achievements.*.icon`.
Show the user `.steam-mcp/exports/images/preview.html`. It never generates artwork: missing art is reported.

## 3. Valve's rules (the store review checks them)

- Capsules show the game's art and its name or logo; no review scores, awards, prices, discount or "new" text, and
  no quotes. Capsules must be readable at small sizes.
- The library hero has no text or logo (the logo is a separate image placed over it).
- Screenshots show the game itself, not concept art or menus only; at least five, 16:9, high resolution.
- Trailers: the first one starts on the page; show gameplay early. Check Valve's trailer guidance.

## 4. Upload

With the BROWSER mode, `apply(path, section="store_assets")` uploads images into slots Steamworks has no image for yet
(it never replaces one) and sets the library logo position; `steamworks_inspect(path, what="store_assets")` shows
which slots are filled. Otherwise `export_package(path, gate=1)` names each file after its Steamworks slot with a
checklist. `gap_report(path, gate=1)` tracks what is still missing.

Valve docs: https://partner.steamgames.com/doc/store/assets, https://partner.steamgames.com/doc/store/assets/rules,
https://partner.steamgames.com/doc/store/assets/libraryassets, https://partner.steamgames.com/doc/store/trailer

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
