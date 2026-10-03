# Capabilities

<!-- Generated from src/steamworks_mcp/data/capabilities.yaml by scripts/gen_docs.py. Do not edit. -->

What the tool can do in Steamworks, per area. **API** is official, documented tooling (partner Web API with a
publisher key, steamcmd / SteamPipe). **BROWSER** is the opt-in mode that drives the partner site in your own
logged-in browser window, using undocumented behaviour that Valve can change at any time
(see [STEAMWORKS_INTERNALS.md](STEAMWORKS_INTERNALS.md)). **ARTIFACT** means the tool produces the file you
upload; **MANUAL** means a step-by-step checklist that you confirm with `mark_applied`.

Last reviewed: 2026-10-03.

| Area | Official API | BROWSER | Mode (browser off → on) | Notes |
|---|---|---|---|---|
| Build upload (depots, app_build VDF) | ❔ unverified: steamcmd / SteamPipe with the builder account | — | API | Without steamcmd configured, the tool writes the app_build / depot_build VDFs and the command to run. |
| Set a build live on a branch | ❔ unverified: ISteamApps/SetAppBuildLive (beta branches) | — | MANUAL | Valve's docs say build scripts cannot set the default branch live; it is done by hand in App Admin. On a released app the account also has to confirm on its phone / Steam Mobile app. |
| List builds and branches | ❔ unverified: ISteamApps/GetAppBuilds, GetAppBetas | — | API |  |
| Leaderboards | ❔ unverified: ISteamLeaderboards/FindOrCreateLeaderboard, GetLeaderboardsForGame, DeleteLeaderboard | — | API |  |
| Read the achievement and stat schema | ❔ unverified: ISteamUserStats/GetSchemaForGame (publisher key) | ✅ verified: apps/fetchachievements | API |  |
| Achievement definitions, localized names, icons | — (no Web API writes achievement definitions) | ✅ verified: apps/newachievement, saveachievement, images/uploadachievement, deleteachievement | ARTIFACT → BROWSER | Steamworks accepts duplicate API names; the tool validates before writing. |
| Achievement localization file (KeyValues) | — | ⚠️ partial: apps/uploadachievementloc | ARTIFACT | Upload is accepted but the token names that apply to page-created achievements are unknown, and the export is empty before publishing. Localized names are written through saveachievement instead. |
| Stat definitions | — | ❔ unverified | MANUAL |  |
| Steam Cloud quota, flags, Auto-Cloud paths and root overrides | — | ✅ verified: apps/setufsparameters, setautocloudpath, setautocloudoverride | MANUAL → BROWSER | Steamworks silently clamps quotas and replaces unknown roots with gameinstall; the tool validates first. |
| Store page short description and About This Game, all languages | — | ✅ verified: Localization tab import (admin/game/uploadloc), partial uploads | ARTIFACT → BROWSER | The ARTIFACT is the same JSON file the Localization tab imports. |
| Other store page fields (system requirements, links, legal line, languages table, genres, categories, tags) | — | ❔ unverified | MANUAL |  |
| Capsules, screenshots, trailers, library assets | — | ❔ unverified | ARTIFACT | Images are cropped from the user's own art at the exact sizes; artwork is never generated. |
| App icon and shortcut icon | — | ❔ unverified | ARTIFACT |  |
| Install folder and launch options (with localized descriptions) | — | ✅ verified: apps/setappinstallfolder, setlaunchoption | MANUAL → BROWSER |  |
| Depots, OS support, packages | — | ❔ unverified | MANUAL |  |
| Pricing and discounts | — | — | MANUAL |  |
| Content survey, mature content, AI disclosure, ratings | — | — | MANUAL | Never automated; the tool prepares the answers. |
| What is still unpublished | — | ✅ verified: apps/diff (the Publish page's read-only "View Diffs") | MANUAL → BROWSER |  |
| Publishing Steamworks changes, posting Coming Soon, releasing | — | — | MANUAL | Never automated. Publish, Prepare for Publishing, Revert and Release are blocked in code. |

## Steamworks permissions for automation

Give automation its own Steamworks user. The tool never publishes, so BROWSER mode does not need "Publish App Changes To Steam". steamcmd uploads do (Valve's builder-account recommendation). Setting a build live on the default branch of a released app also needs the account's phone number or Steam Mobile authenticator.

- **BROWSER mode user:** Edit App Metadata, Edit App Marketing Data
- **steamcmd builder account:** Edit App Metadata, Publish App Changes To Steam
- **Never needed by this tool:** Manage Pricing & Discounts, View Financial Info, Manage Signing / Actual Authority permissions

Publishing, Prepare for Publishing, Revert Changes and Release are never automated.
