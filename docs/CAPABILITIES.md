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
| Build upload (depots, app_build VDF) | ✅ verified: steamcmd / SteamPipe with the builder account | — | API | Uploaded a test build with the generated VDFs and a cached steamcmd login (nothing set live; the steamcmd output itself is not kept as a fixture). Without steamcmd configured, the tool writes the app_build / depot_build VDFs and the command to run. |
| Set a build live on a branch | ❔ unverified: ISteamApps/SetAppBuildLive (beta branches) | — | MANUAL | set_build_live sets beta branches live through the Web API after the user confirms. The default branch is always set live by hand in App Admin (Valve: build scripts cannot target it); on a released app the account also has to confirm on its phone / Steam Mobile app. |
| List builds and branches | ⚠️ partial: ISteamApps/GetAppBuilds, GetAppBetas | — | API | GetAppBuilds is recorded with and without builds. GetAppBetas answered HTTP 500 on unreleased apps, so the branch list is not verified. |
| Leaderboards | ✅ verified: ISteamLeaderboards/FindOrCreateLeaderboard, GetLeaderboardsForGame, DeleteLeaderboard | — | API | GetLeaderboardsForGame is cached for about a minute, so the tool reads the boards it changes one by one (FindOrCreateLeaderboard without creating). Settings of an existing board are only reported, never changed. |
| Read the achievement and stat schema | ⚠️ partial: ISteamUserStats/GetSchemaForGame (publisher key) | ✅ verified: apps/fetchachievements | API | The API returns the published schema only; recorded on an app with nothing published yet (empty answer). |
| Achievement definitions, localized names, icons | — (no Web API writes achievement definitions) | ✅ verified: apps/newachievement, saveachievement, images/uploadachievement, deleteachievement | ARTIFACT → BROWSER | Steamworks accepts duplicate API names; the tool validates before writing. |
| Achievement localization file (KeyValues) | — | ⚠️ partial: apps/uploadachievementloc | ARTIFACT | Upload is accepted but the token names that apply to page-created achievements are unknown, and the export is empty before publishing. Localized names are written through saveachievement instead. |
| Stat definitions | — | ❔ unverified | MANUAL |  |
| Steam Cloud quota, flags, Auto-Cloud paths and root overrides | — | ✅ verified: apps/setufsparameters, setautocloudpath, setautocloudoverride | MANUAL → BROWSER | Steamworks silently clamps quotas and replaces unknown roots with gameinstall; the tool validates first. Saving an Auto-Cloud path turns "developers only" back on, so quotas and flags are written last. |
| Store page short description and About This Game, all languages | — | ✅ verified: Localization tab import (admin/game/uploadloc), partial uploads | ARTIFACT → BROWSER | The ARTIFACT is the same JSON file the Localization tab imports. |
| Other store page fields (system requirements, links, legal line, languages table, genres, categories, tags) | — | ⚠️ partial: Store page form (admin/game/save), apply(section='store_page') | MANUAL | Links, support info, legal line and system requirements were written, read back and restored live; the language table, genres, categories, platforms and DRM fields go through the same form. Developer/publisher names, the Controller and Accessibility wizards and the release date stay manual. Tags are never written: Steam publishes them at once. |
| Capsules, screenshots, trailers, library assets | — | ⚠️ partial: Graphical Assets upload (admin/game/save ... tab_graphicalassets), apply(section='store_assets') | ARTIFACT | Images are cropped from the user's own art at the exact sizes; artwork is never generated. Capsules, page background and library images were uploaded live into empty slots; existing images are never replaced. Screenshots, trailers and the library logo position are still uploaded by hand. |
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
