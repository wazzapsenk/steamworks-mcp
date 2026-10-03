---
name: steamworks-playtest-demo
description: Steam Playtest and demos - when to use each, setting up the separate app, the store page button, access waves, builds, Steam Next Fest demos, and keeping saves from a demo for the full game. Use when the user wants testers, an open playtest, a demo, or to join Steam Next Fest.
---

# Playtests and demos

Tools: `status`, `init_project`, `set_field`, `gap_report`, `generate`, `apply`, `export_package`, `get_spec_info`

Both are separate apps with their own app id, attached to the main game. In steamworks.yaml they are `apps.playtest`
and `apps.demo`; most tools take `app="playtest"` or `app="demo"`.

## Playtest

For testing before release. Players press "Request access" on the main game's store page; the developer lets them in
in waves (Steamworks > Playtest), or opens it to everyone. Playtests are free, have no reviews and no store page of
their own, and can be closed at any time. Good for stress-testing servers or gathering feedback with a survey link.

Set it up: create the playtest from the main app in Steamworks, put its app id in `apps.playtest.appid`
(`set_field`), and give it depots and launch options like the main game (`apps.playtest.builds.depots`,
`apps.playtest.installation`; `generate(path, section="builds")` drafts depots for the main game only, so
copy and adjust them). The `steamworks-app-config` skill walks through it.

## Demo

A free slice of the game for anyone, with a "Download demo" button on the main page (and optionally its own store
page). It needs its own builds and passes Valve's review. A demo is required for Steam Next Fest; plan it around the
registration deadline from `get_spec_info("events")` (the `steamworks-sales-calendar` skill).

- Keep demo progress for the full game: share Steam Cloud saves between the apps (`apps.main.cloud.shared_appid`) or
  read the demo's save folder.
- Link to the full game from the demo's last screen (ActivateGameOverlayToStore with the main app id).
- Its depots and launch options go under `apps.demo` the same way as for the playtest; `apply(..., app="demo")`
  and `export_package` work on it.

## Keys

For press or a closed group, request keys in Steamworks; Valve's key rules apply (the `steamworks-community` skill).

Valve docs: https://partner.steamgames.com/doc/features/playtest, https://partner.steamgames.com/doc/store/application/demos,
https://partner.steamgames.com/doc/marketing/upcoming_events/nextfest

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
