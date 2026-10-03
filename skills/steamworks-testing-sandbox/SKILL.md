---
name: steamworks-testing-sandbox
description: Test Steam features before release - Valve's test app 480 (Spacewar) for trying the SDK, then the game's own app with steam_appid.txt, test branches and keys, resetting achievements and stats, developer-only Steam Cloud, and a second account for multiplayer. Use when the user starts with the SDK, cannot get Steam to initialize, or tests achievements, cloud or multiplayer.
---

# Testing Steam features

Tools: `check_code`, `integration_code`, `status`, `set_build_live`, `steamworks_inspect`

## Before the game has an app id

App id 480 (Spacewar) is Valve's public test app: every Steam account owns it, and it has achievements, stats and
leaderboards to try the SDK with. Put `480` in `steam_appid.txt` next to the editor or the game's executable and
start Steam. Its names are Spacewar's, not yours, and nothing done there carries over. `check_code` reminds you to
replace 480 before a real build.

## With the game's own app id

- `steam_appid.txt` with the real id, next to the editor/executable, for development only.
- The developer's Steam account owns unreleased apps of its partner group, so the game shows in the Steam library
  once a build is uploaded.
- Achievements and stats must be defined in Steamworks and published before the game can set them.
- `integration_code(path)` generates code that uses exactly steamworks.yaml's names.

## Resetting progress

ISteamUserStats::ResetAllStats(true) clears stats and achievements for the current account (development only), or
ClearAchievement for one. The generated SteamProgress helpers include a ResetAchievement for development builds.

## Steam Cloud

Steamworks' Steam Cloud page can enable cloud for developers only first; test on two machines or by deleting the
local save (the `steamworks-cloud-saves` skill).

## Branches and testers

Upload builds to a password-protected beta branch and set them live there (`set_build_live`, the
`steamworks-build-upload` skill); testers pick the branch in the game's Properties > Betas. For outside testers use
keys or a Playtest (the `steamworks-playtest-demo` skill).

## Multiplayer

Two Steam accounts on two machines (or one machine and a Steam Deck); the second account needs the game too (a key).

## When Steam does not start

Steam running? The right app id in steam_appid.txt? The steam_api library next to the executable and the right
architecture? Started from the editor with Steam logged into an account that owns the app? `check_code` catches an
unchecked Init.

Valve docs: https://partner.steamgames.com/doc/sdk/api/example, https://partner.steamgames.com/doc/sdk/api,
https://partner.steamgames.com/doc/store/application/branches
