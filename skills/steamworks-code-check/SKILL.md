---
name: steamworks-code-check
description: Check a game's own code, engine settings and SteamPipe build scripts against 24 Steamworks rules - test app id 480, unchecked SteamAPI_Init, missing callbacks or StoreStats, achievement names Steamworks does not know, Web API keys or steamcmd passwords in the project, setlive default, Steam Deck problems, saves Steam Cloud cannot sync, the old P2P API. Use before uploading a build, after adding Steam code, or when the user asks to review their Steam integration.
---

# Steamworks code check

Tools: `check_code`, `get_spec_info`, `integration_code`

## 1. Run it

`check_code(path)` (add `source_dir` when the engine project is not the folder with steamworks.yaml). Only the game's
own code counts: plugins, the SDK wrappers, editor tools and demo scenes are skipped. It never changes a file, and a
key or password it finds is never shown.

Show the `display` table, then go through the findings, errors first, in plain words: what was found, why it matters
on Steam, the fix. One finding per rule is usually enough to explain; mention how many more there are.

## 2. The rules (get_spec_info("code_rules") has the full text)

- **App id**: 480 (Spacewar, Valve's test app) left in; an id that differs from the game's, demo's or playtest's.
- **SDK start**: Init result not checked; no RestartAppIfNecessary; callbacks never run; no Shutdown.
- **Stats and achievements**: set without StoreStats; names not in steamworks.yaml; achievements nothing unlocks.
- **Secrets**: Web API keys, steamcmd passwords in scripts, ssfn / config.vdf login files.
- **Build scripts**: `setlive` default (Valve allows that only by hand), content roots, depot scripts or files that do
  not exist, steam_appid.txt in a build folder.
- **Steam Deck**: forced resolution, no gamepad input, anti-cheat that needs Proton support.
- **Saves**: progress in PlayerPrefs (registry), hard-coded Windows paths, BinaryFormatter.
- **Networking**: the old ISteamNetworking P2P API; auth tickets nothing verifies.

`check_code(path, rules=[...])` runs only some rules or groups (`steamworks-secrets`, `steamworks-steam-deck`, …).

## 3. Fix

Offer fixes only when the user asks; then edit the code yourself or show the change. For the SDK start, stats and
saves, `integration_code` writes code that already follows these rules. A leaked Web API key must be revoked and
replaced in Steamworks (Users & Permissions > Manage Groups), not only deleted from the code. Run `check_code` again
afterwards.

In Cursor, the same rules are also installed as rules/*.mdc and show while the matching files are edited.

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
