---
name: steamworks-sdk-integration
description: Add the Steamworks SDK to a Unity, Godot, Unreal or C++ game with code that uses exactly the app id and the achievement, stat and leaderboard names in steamworks.yaml - starting Steam, unlocking achievements, stats, leaderboard uploads and save locations for Steam Cloud. Use when the user wants Steam working in their game, asks how to unlock achievements in code, or moves from another SDK.
---

# Steamworks SDK in the game

Tools: `status`, `integration_code`, `check_code`, `generate`, `set_field`, `apply`

The server writes the code to `.steam-mcp/exports/code/<target>/`; it never edits the game. You copy files into the
project only when the user asks.

## 1. Check the inputs

Call `status(path)`. The code needs:

- the app id (`apps.main.appid`): without it the code uses 480, Valve's test app;
- the achievements, stats and leaderboards in `steamworks.yaml`. If they are missing, run
  `generate(path, section="code")` (finds the names the code already uses) and the `steamworks-achievements` skill.

## 2. Pick the wrapper

`integration_code` detects it; confirm it with the user:

| Engine | Target | Library to install |
|---|---|---|
| Unity | `unity-steamworks-net` | Steamworks.NET (Unity package; ships steam_api64) |
| Unity | `unity-facepunch` | Facepunch.Steamworks (C#, async, runs callbacks itself) |
| Godot 4 | `godot` | GodotSteam 4 (GDExtension from the Asset Library, or the prebuilt editor) |
| Unreal | `unreal` | The engine's Online Subsystem Steam plugin (ships the SDK) |
| Other / C++ | `cpp` | The Steamworks SDK from partner.steamgames.com (Downloads) |

## 3. Generate

`integration_code(path, features=[...])` with any of `init`, `achievements`, `stats`, `leaderboards`, `cloud` (default
all). Show its table of files and where each one goes, and explain:

- **init**: relaunches through Steam when started from the .exe, starts the API and checks the result, runs
  callbacks every frame, shuts down on exit. One copy for the whole game (Unity: a GameObject in the first scene;
  Godot: an autoload named SteamService).
- **achievements / stats**: constants for every API name plus helpers that call StoreStats, so nothing is lost.
- **leaderboards**: uploads that keep the best score. The leaderboards must exist in Steamworks first
  (`apply(section="leaderboards")` with the publisher key, or by hand).
- **cloud**: where save files go so Steam Auto-Cloud finds them, written safely (temp file, then rename).

## 4. Copy and check

With the user's OK, copy the files into the project (or show them to paste). For development, a `steam_appid.txt`
with the app id goes next to the editor or game executable; it must not be in uploaded builds. Then run
`check_code(path)` and fix what it reports.

## Testing

Steam must be running and the user's account must own the app (developers get it automatically). Achievements and
stats only work for names defined in Steamworks and published. The overlay does not show in most engine editors;
test it in a build.

Valve docs: https://partner.steamgames.com/doc/sdk/api, https://partner.steamgames.com/doc/features/achievements/ach_guide

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
