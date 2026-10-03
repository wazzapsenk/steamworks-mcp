---
name: steamworks-migration
description: Bring a game that is already on Epic, GOG, itch.io or consoles to Steam - map achievements, stats, cloud saves, friends and multiplayer to Steamworks, keep cross-platform services, reuse the store text and art, and plan keys for existing buyers. Use when the user moves or adds their game to Steam from another store or SDK.
---

# Moving a game to Steam

Tools: `status`, `init_project`, `set_field`, `generate`, `integration_code`, `check_code`, `gap_report`, `study_market`, `prepare_images`

## 1. Start tracking

`init_project(path)` scans the project (Unity today; other engines answer the interview) and `gap_report` shows the
Steam steps. Steam needs its own app (Steam Direct fee per app) and a store page that passes Valve's review.

## 2. Store page

Existing store text and art are a start, not a copy: Steam's page has its own limits (a 300-character short
description, BBCode in About, its own capsule sizes). Save the text as drafts with `set_field(source="generated")`,
run the `steamworks-store-page` skill to adapt it, and cut images with `prepare_images` (the `steamworks-store-assets`
skill). `study_market` compares with the closest games on Steam.

## 3. Map the features

| Other store / SDK | On Steam |
|---|---|
| Achievements (Epic, GOG Galaxy, consoles) | Steam achievements: API names in steamworks.yaml, 256x256 icons, hidden flags (`generate(section="code")` finds the names in code) |
| Stats / leaderboards | ISteamUserStats stats and leaderboards |
| Cloud saves | Steam Auto-Cloud on the same save folder (the `steamworks-cloud-saves` skill) |
| Friends, invites, presence | ISteamFriends, lobbies, Rich Presence (the `steamworks-social` skill) |
| Multiplayer | Keep a cross-platform backend (e.g. Epic Online Services) if players on other stores play together; use Steam auth and the Steam overlay for invites |
| Platform SDK init | `integration_code(path)` for the Steam side; keep the other SDK behind a build define |

Run `check_code` after the Steam code is in.

## 4. Builds and DRM

SteamPipe uploads (the `steamworks-build-upload` skill). Steam DRM (the executable wrapper) is optional; a DRM-free
release elsewhere and SteamAPI_RestartAppIfNecessary on Steam is common.

## 5. Existing buyers and keys

Steam keys for people who bought elsewhere (backers, early access on another store) are allowed; Valve's key rules
apply: don't give Steam customers a worse deal than elsewhere (the `steamworks-community` skill).

Valve docs: https://partner.steamgames.com/doc/gettingstarted, https://partner.steamgames.com/doc/gettingstarted/appfee,
https://partner.steamgames.com/doc/features/keys, https://partner.steamgames.com/doc/features/drm

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
