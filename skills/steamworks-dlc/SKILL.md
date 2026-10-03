---
name: steamworks-dlc
description: Plan and set up DLC on Steam - expansions, cosmetic packs, soundtracks and supporter packs, roadmaps and season passes, pricing next to the base game, the DLC's own app and store page, and checking ownership in the game. Use when the user plans post-launch content, a soundtrack release, or asks how DLC works on Steam.
---

# DLC

Tools: `status`, `price_brief`, `compare_games`, `store_lookup`, `integration_code`

## Plan

- **Kinds**: expansions (new content), cosmetic packs, soundtracks and art books, supporter packs. Steam shows every
  DLC on the base game's page, so a few meaningful DLC read better than many tiny ones.
- **Roadmap**: what comes when, free updates versus paid DLC. Players accept paid expansions after a well-supported
  launch more readily than day-one paid content.
- **Season pass**: say exactly what it includes and when; delays need clear communication.
- **Price**: `compare_games` and `store_lookup` show close games' DLC counts; `price_brief(path, appids=[...])` with
  DLC app ids shows what similar DLC costs. A common anchor is a fraction of the base price matched to the content.

## Set up in Steamworks (by hand; these tools do not edit DLC or packages)

1. On the base app, create a new DLC: it gets its own app id.
2. Give it a store page (description, capsules, screenshots) and pass the store review; it cannot release before the
   base game.
3. Content that downloads gets a depot; unlock-only DLC (content already in the base build) needs none.
4. Set its price and release it; it appears on the base game's page.

## In the game

Check ownership with ISteamApps::BIsDlcInstalled(dlc_appid) (or BIsSubscribedApp), and react to DlcInstalled_t when
it is bought during play. Link to the DLC's store page with ActivateGameOverlayToStore(dlc_appid, …). For many small
items, consider the Steam Inventory Service instead (the `steamworks-inventory` skill).

Valve docs: https://partner.steamgames.com/doc/store/application/dlc, https://partner.steamgames.com/doc/store/application/packages,
https://partner.steamgames.com/doc/api/ISteamApps
