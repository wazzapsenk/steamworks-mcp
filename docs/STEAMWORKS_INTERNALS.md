# Steamworks partner site: observed behaviour

**Undocumented.** Everything here was observed on the Steamworks partner site (partner.steamgames.com) in
October 2026, on an unreleased test app, with the recorder in `scripts/live/`. Valve does not document these
pages or endpoints and can change them at any time. The tool only relies on them in the opt-in `BROWSER` mode.

The recordings behind every statement are in [`tests/fixtures/steamworks/`](../tests/fixtures/steamworks/README.md)
(sanitized; app id `1000000`, store item id `2000000`). Items marked **unverified** could not be confirmed.

## Sessions and requests

- Logged in ⇔ the `steamLoginSecure` cookie exists for `partner.steamgames.com`. Only its presence is checked; the
  value is never read.
- Anonymous visits to protected pages answer `302` to `/?goto=<path>`.
- **AJAX without a session also answers `302` to `?goto=…`.** `fetch()` follows the redirect and gets the sign-in
  page as HTML with status `200`. A JSON parse failure on an AJAX endpoint therefore means "not logged in", not
  "server error". (`errors/session_expired`)
- Every state-changing call is a `POST` with the page's CSRF token `sessionid` (the page global `g_sessionID`),
  sent from inside the logged-in page with `credentials: same-origin`.
- The pages never reach `networkidle`; use `domcontentloaded` plus a short wait.
- App list: `/apps/` (links `/apps/landing/{appid}`, rendered after load).

| Page | URL |
|---|---|
| App landing | `/apps/landing/{appid}` |
| Store page editor | `/admin/game/editbyappid/{appid}` → `302` → `/admin/game/edit/{storeItemId}` |
| Stats & Achievements | `/apps/achievements/{appid}` |
| Achievement localization | `/apps/loc/{appid}` |
| Steam Cloud | `/apps/cloud/{appid}` |
| Installation → General | `/apps/config/{appid}` |
| Publish | `/apps/publishing/{appid}` |
| Pending changes & history | `/apps/history/{appid}` |

Playtest apps have no real store page (`editbyappid` does not redirect to an item).

## Drafts, revisions and publishing

App configuration is stored in **sections** with revisions: `common`, `config` (install folder, launch options),
`ufs` (Steam Cloud), `stats` (stats and achievements), `depots`, … Every edit on the pages above creates or updates an
**uncommitted revision** of its section. Nothing reaches customers until someone publishes.

- `/apps/history/{appid}` lists the pending sections (revision, date, editor) and the published history.
- The Publish page's "View Diffs" button calls `POST /apps/diff/{appid}` with `section=technical` and returns
  `{"opened": "<html>", "diff": "<html>"}`:
  - `opened` has one line per section with uncommitted changes, for example
    `App section "ufs" has uncommitted changes. Revision 1 was last modified by … on 2 Oct @ 6:47pm`.
  - `diff` holds the KeyValues diff of each section against the published revision. A section that was never
    published shows `=== "stats" section is new ===`.
  - This call is read-only, and it is the best way to show the user what is still unpublished.
- Never called by this project: `POST /apps/prepare/{appid}` ("Prepare for Publishing"), `POST /apps/revert/{appid}`
  ("Revert Changes": drops all unpublished work, including other people's), and the publish action itself. The
  recorder blocks any non-GET request whose URL mentions `publish`, `/apps/prepare/` or `/apps/revert/`.
- The store page has its own review/publish flow on the store item. The app-section model above does not cover it.

### Can players see unpublished changes?

The test showed:

- After the writes, `/apps/diff` listed every test row as uncommitted: the Cloud rows, the launch options and the
  achievement section. After cleanup they were gone. (`visibility/before`, `visibility/after_writes`,
  `visibility/after_cleanup`)
- Public data showed nothing either way, because the test app is not public: `store.steampowered.com/api/appdetails`
  → `{"<appid>":{"success":false}}`, `GetGlobalAchievementPercentagesForApp` → `403`.

Conclusion: app-section edits (Cloud, installation, achievements) are drafts until published. This follows from
Valve's revision model and is what `/apps/diff` shows. **Unverified**: a direct check on a *public* app (anonymous
`steamcmd +app_info_print` before and after a draft edit, and the partner `GetSchemaForGame` with a publisher key)
was not recorded.

Store page text: in the live validation (below), a test short description uploaded through the Localization tab
did **not** appear in the public `appdetails` of an app with a public store page while it was unpublished.

### Live validation of the Python implementation (2026-10-03)

`scripts/live/validate.py` ran the whole protocol through the MCP tools on a second app with a public store page
(read every section, mapping round trip, restore round trip, then per section: test change, write, readback,
restore). Every step passed, including achievement icon upload and the store-text upload through the page UI.
What it showed beyond the recordings:

- **Saving the same values still opens a revision.** Writing back exactly what was read gave
  `App section "ufs" has uncommitted changes` with an empty diff. The tool therefore compares the diff's
  `<del class="diff_delete">` and `<ins class="diff_insert">` blocks (ignoring whitespace and empty KeyValues
  blocks) and reports only sections with real changes as `changed_sections`.
- **Saving an Auto-Cloud path turns "developers only" (`hideInClient`) on.** The quotas and flags are therefore
  written last, after any row changed.
- **Deleting the last launch option leaves an empty `"launch" { }` block** in the `config` diff, and the install
  folder line differs only in whitespace. Neither is a real change.
- **A new section's content is not shown** (`=== "stats" section is new ===`). Creating and then deleting a test
  achievement on an app without achievements leaves such a section in the diff; the tool counts new sections as
  changed because it cannot see inside them.
- The earlier `restore round trip` step writes Steam Cloud back unchanged; it only leaves the empty revision above.

## Store page text (Localization tab)

- Export: `GET /admin/game/downloadloc/{storeItemId}?language=all&format=json` (also `format=csv`):
  `{"itemid":"…","languages":{"english":{"app[content][about]":"…","app[content][short_description]":"…"},…}}`.
  Languages without text are `[]`. The editor wraps paragraphs as `[p]…[/p]`.
- Import: the tab's upload form posts `multipart/form-data` to **`POST /admin/game/uploadloc/{storeItemId}`**. The
  body contains `sessionid`, `MAX_FILE_SIZE`, `activetab=tab_localization`, `save_redirect=edit`,
  `serialized_app_data` (the whole store form as JSON), every visible store form field, and the file(s) in
  `localization_files[]` (`.json` or `.csv`, several allowed). Answer: `302` to
  `/admin/game/edit/{storeItemId}/?activetab=tab_localization`.
- **Partial uploads work.** A file with only `languages.english["app[content][short_description]"]` changed that one
  field; About and every other language stayed as they were. (`store/write`, `store/readback`,
  `uploads/store_loc_upload.json`)
- **An empty value clears the field.** A file with `"app[content][short_description]": ""` emptied the English short
  description (tested by hand on a test app, then restored). The tool drops empty values before every upload, so
  it never clears a field.
- The export also carries fields from other tabs, such as `app[content][sysreqs][windows][min][osversion]`.
- The page loads `IStoreCatalogService/GetDevPageLinks` from `api.steampowered.com` with a short-lived
  `access_token`. Treat it like a cookie: never log it.

## Store page form (Edit Store Page)

- **One form for every tab.** `GET /admin/game/edit/{storeItemId}` holds `#gameform`. It posts multipart to
  `POST /admin/game/save/{storeItemId}`, which answers with a redirect to the edit page carrying "Changes saved".
  - The fields include `sessionid`, `serialized_app_data` (the whole store item as JSON; the best place to read
    current values) and about 1,900 inputs.
  - Field groups:
    - `app[content][links][website|forums|online_manual|privacy_policy|…]` and `app[content][support_info][url|email|phone]`
    - `app[content][legal][<lang>]`
    - `app[content][sysreqs][windows|mac|linux|android][min|rec][…]`: per-language text, `memory`/`diskspace`
      `[amount|units]`, `directx` (a select), `broadband`
    - `app[platforms][win|mac|linux|android]`
    - `app[content][supported_languages][<lang>][supported|full_audio|subtitles]`
    - `rgGenres[<id>]` and `app[classification][primary_genre]`
    - `app[classification][category][category_<id>]`
    - `app[game][3pdrm|3pacc][…]`
  - Ticked "fancy checkbox" inputs hold `true` and empty ones `""` (some start as `1`). Genre names come from the
    page's `OnGenreSelect(this, '<id>', '<name>')`.
- **Saving the form unchanged changes nothing.** Recorded live on a test app:
  - The tool posts the page's own form back (the browser's `FormData` of `#gameform`) with only the changed inputs
    replaced.
  - An unchanged post left `serialized_app_data`, the localization export and the diff page exactly as they were.
  - Inputs the static form does not carry (About, the social links) kept their values.
  - Changing two inputs and then writing them back also returned to the exact same state.
  - In the form, unlike the localization import, an empty value is how a field is cleared.
- **What saving does not touch.** Saves go into the unpublished store draft (`GET /admin/game/diff/{storeItemId}`
  shows it). The "View Diffs" button saves first, so the tool only reads the diff URL.
- **Areas the tool leaves alone:**
  - Developer and publisher names are autocomplete widgets.
  - The Controller and Accessibility wizards set categories and also record that the wizard was finished.
  - The release date has its own endpoint (`/apprelease/ajaxupdatereleaserequest/{appid}`).
- **Graphical assets.** The Graphical Assets tab uploads each image as one multipart part to
  `POST /admin/game/save/{storeItemId}?activetab=tab_graphicalassets&json=1`, together with `sessionid`.
  - The part's name says the slot. For localized slots the language follows:
    - `header_image|header|assets|header_image|image|english`
    - `small_capsule|capsule|…`, `main_capsule|capsule_616x353|…`, `hero_capsule|hero_capsule|…` (the vertical
      capsule), `library_capsule|…`, `library_header|…`, `library_hero|…`, `library_logo|logo|…`
    - The page background is not localized: `page_background|page_bg_raw|assets|page_background_raw`.
  - The image rules dialog is only in the page; the server never sees it.
  - The images then show up in `serialized_app_data.assets` (`<slot>.image.<lang>`, `page_background_raw`).
  - Recorded live: nine empty slots of a game filled this way, into the store draft.
- **Library logo position.** The position tool posts the fields below to the same save URL as the image uploads:
  - `json=1`
  - `app[assets][library_logo][logo_position][pinned_position]`: one of `BottomLeft`, `UpperLeft`, `UpperCenter`,
    `CenterCenter`, `BottomCenter`
  - `app[assets][library_logo][logo_position][width_pct]` and `[height_pct]`

  After the library images were uploaded, the Library Assets checklist item stayed incomplete. It was completed
  once the logo position was set. Recorded live.
- **Developer and publisher names.** The form only carries one empty "add another" row:
  - Developer: `app[game][developers][<n>][name]` (hidden) plus a `…_compl` text box.
  - Publisher: `app[game][publishers][<n>]` plus its `…_compl` text box.
  - The names already set are only in `serialized_app_data.game` (`developers: [{name}]`, `publishers: [name]`).
  - Filling the empty row with a plain name (no link to a developer homepage) added it and completed the
    "Developer and Publisher Names" checklist item. Recorded live.
  - Removing a name needs the page's own remove control; the tool only adds.
- **Live at once although the name does not say so:**
  - **Package names and contents** (`/store/ajaxpackagesave/{packageId}`; renaming publishes right away) are on the
    guard's forbidden list.
  - **Store tags.** The Tag Wizard (`/taxonomy/tagwizard/{appid}`) carries its data in `initSurvey({...})`:
    - `tagNames` (id → name of every tag), `devTags` (the developer's tags) and `communityTags`.
    - Publish posts `POST /tagdata/forcetagranking` with `appid`, `sessionid`, `rankedtagids[]` (most important
      first, 20 at most) and `negatedtagids[]` (community tags to remove). The answer is
      `{"success": 1, "finalTags": [...]}`.
    - The page says the changes "have been successfully published to Steam".
    - The tool reads this live, and writes it only with `goes_live_now=true`, never sending `negatedtagids`. The
      write itself is not recorded: it would publish.

## Depots

- **How the page holds the depots.** `GET /apps/depots/{appid}` holds them twice:
  - as `var depots = {"<id>": {"name", "config": {...}}}` in the page's script;
  - as rows: `li > span.appdepot`, each with `.depotid`, an Edit button (`.editme`) and, in a hidden edit panel,
    the selects `language`, `oslist` (`""` = all, `windows`, `macos`, `linux`, `windows,linux`, …), `osarch`, …
- **Save** (`#submitter`) builds the whole list from those rows and posts it as
  `POST /depots/upload/{appid}` with `depots=<JSON list>`. A depot left out of the list would be removed, so the tool
  never builds the list itself: it changes the selects of the rows it needs and clicks the page's own Save.
- **Recorded live on a test app:**
  - Setting a depot to Windows/64-bit showed up in the depot's `config` and as the only pending change of the
    `depots` app section.
  - Writing the empty values back removed the change again (no pending changes left).

## Achievements

- `GET /apps/fetchachievements/{appid}` → `{"achievements":[…],"languages":{"english":true,…}}`. `languages` lists the
  languages enabled for achievement text on `/apps/loc/{appid}`.
- `POST /apps/newachievement/{appid}` `{maxstatid, maxbitid}` (from `#max_statid_used` / `#max_bitid_used`, `0`/`-1`
  when empty) → `{"success":1,"maxstatid","maxbitid","achievement":{…}}`. New rows are
  `api_name: NEW_ACHIEVEMENT_{stat}_{bit}`, `display_name: NEW_ACHIEVEMENT_NAME_{stat}_{bit}`.
- `POST /apps/saveachievement/{appid}` `{statid, bitid, apiname, displayname, description, permission, hidden,
  progressStat, progressMin, progressMax}`. `displayname`/`description` are JSON: a plain string (English only) or
  `{"english":…,"german":…}` without empty languages. Answer `{"success":1,"saved":true,"achievement":{…}}`.
  Languages that are not enabled for the app are still stored.
- **API names are not checked for uniqueness.** A second achievement saved with an existing API name was accepted
  (`saved: true`). The tool must validate this itself. (`errors/achievement_duplicate_apiname`)
- `POST /images/uploadachievement` multipart `{sessionid, MAX_FILE_SIZE, appID, statID, bit,
  requestType: achievement|achievement_gray, image}` →
  `{"success":true,"message":"Your achievement image was successfully uploaded."}`. 256×256 JPG accepted.
- Delete: the page's `DeleteAchievementClosure` calls `POST /apps/deleteachievement/{appid}/{stat}/{bit}` with only
  `sessionid` → `{"deleted":true}`. There is also `POST /apps/deletestat/{appid}/{stat}` (not used).

### Achievement localization file (unverified)

- Download: `GET /apps/downloadachievementloc/{appid}?download_language=english|all`. For an app whose achievements
  were never published, it returned an empty file (`"lang"` with an empty block) even though the achievements had
  English and German names. **Unverified:** the export probably reflects published data only.
- Upload: the page form posts `multipart/form-data` to `POST /apps/uploadachievementloc` with
  `{sessionid, MAX_FILE_SIZE, appID, kv}` through a hidden iframe. Responses:
  - Single-language layout `"lang" { "Language" "<lang>" "Tokens" { … } }` is parsed. A language that is not
    enabled for the app is rejected: `{"success":false,"message":"App doesn't support language 'german'."}`.
  - Accepted uploads answer `{"success":true,"message":["N achievements processed.","Localized names: 0", …]}`.
  - Tokens `NEW_ACHIEVEMENT_{stat}_{bit}_NAME` and `NEW_ACHIEVEMENT_NAME_{stat}_{bit}` both gave "Localized
    names: 0" for achievements created on the page. The token names that apply are **unknown**.
- Until this is verified, localized achievement text is written through `saveachievement` (verified above), and the
  KeyValues file is only offered as an `ARTIFACT`.

## Steam Cloud

- `POST /apps/setufsparameters/{appid}` `{cb, cfiles, appidRedirect, hideInClient, syncOnSuspend}` →
  `{"success":true,"message":"Steam Cloud enabled for this game."}`. Enabling Cloud for the first time also turned
  on "developers only" (`hideInClient`).
- **Quotas are clamped silently:** `cb=10000000001` was stored as `10000000000`, `cfiles=10001` as `10000`, and
  both calls answered `success: true`. (`errors/cloud_quota_too_big`)
- `POST /apps/setautocloudpath/{appid}` `{index, root, path, pattern, oslist, recursive}` →
  `{"success":true,"message":"Application auto-cloud path set successfully."}`. All fields empty deletes row `index`;
  delete from the highest index down.
  - **An unknown `root` is stored as `gameinstall`** (App Install Directory) with `success: true`.
    (`errors/cloud_invalid_root`)
  - **An empty `pattern` is accepted** and stored empty. (`errors/cloud_missing_pattern`)
- `POST /apps/setautocloudoverride/{appid}` `{index, root, os, useinstead, addpath, replacepath}` →
  `{"success":true,"message":"Application auto-cloud override set successfully."}`. Empty fields delete.
- Root values: `gameinstall`, `SteamCloudDocuments`, `WinMyDocuments`, `WinAppDataLocal`, `WinAppDataLocalLow`,
  `WinAppDataRoaming`, `WinSavedGames`, `WindowsHome`, `MacHome`, `MacAppSupport`, `MacDocuments`, `LinuxHome`,
  `LinuxXdgDataHome`, `LinuxXdgConfigHome`, `AndroidExternalData`, `AndroidInternalData`.
  OS values: `""` (all), `Windows`, `MacOS`, `Linux`, `Android`.
- On the page, existing rows are forms named `AutoCloudPathForm{i}`. Override forms have a `useinstead` field. The
  quota inputs are `#ufsQuota` / `#ufsFiles`, and the checkboxes are `#ufsHideInClient` / `#ufsAllowSyncOnSuspend`.

## Installation → General

- `POST /apps/setappinstallfolder/{appid}` `{installfolder}`.
- `POST /apps/setlaunchoption/{appid}` `{index, executable, arguments, workingdir, type, description, osversion,
  osarch, oscpu, betakey, ownsdlc, realm, steamdeck, description_{lang}…}` →
  `{"success":true,"message":"Application launch option set successfully."}`.
  - **Delete = every field empty except `index`.** This is what the page's `DeleteLaunchOption` sends.
  - **An empty `executable` with other fields set is accepted** and creates a row without an executable.
    (`errors/launch_empty_executable`)
  - `type`: `""`, `default`, `config`, `vr`, `openvroverlay`, `openxr`, `othervr`, `server`, `editor`, `manual`,
    `benchmark`, `safemode`, `option1..3`. `osversion`: `""`, `windows`, `macos`, `linux`, `android`. `osarch`:
    `""`, `32`, `64`.
  - Existing rows are forms `LaunchForm{i}`. Localized descriptions are hidden inputs
    `Launch_{i}_description_loc[{lang}]`. The arguments input is named `argumentsx`.

## What the server does *not* validate

Steamworks accepted every invalid value we sent: duplicate achievement API names, unknown Cloud roots (silently
replaced), empty Auto-Cloud patterns, quotas above the documented limits (silently clamped), and launch options
without an executable. Validation has to happen in this tool, before anything is written.

## Partner Web API (publisher key)

Recorded on an unreleased test app with a key from a group that has only the **General** permission (`api/read`,
`api/leaderboard_write`, `api/leaderboard_display_types`, `api/leaderboard_delete`). All calls go to
`partner.steam-api.com`. Without a key, `api.steampowered.com` answers `GetSchemaForGame` with
`400 Required parameter 'key' is missing`. (`api/public_unkeyed`)

Leaderboards (`ISteamLeaderboards`):

- `FindOrCreateLeaderboard/v2` (POST) takes `appid`, `name`, `sortmethod`, `displaytype`, `createifnotfound`,
  `onlytrustedwrites` and `onlyfriendsreads`. The answer is HTTP 200 with
  `{"result": {"result": 1, "leaderboard": {leaderboardName, leaderBoardID, leaderBoardEntries,
  leaderBoardSortMethod, leaderBoardDisplayType, onlytrustedwrites, onlyfriendsreads, ...}}}`.
- The `displaytype` names are `Numeric`, `Seconds` and `MilliSeconds`. The SDK's names (`TimeSeconds`,
  `TimeMilliSeconds`) and unknown strings are accepted, but the board is stored with an empty display type
  (`api/leaderboard_display_types`). The tool only sends the three valid names. It reports a board with an empty
  display type as `unset`.
- With `createifnotfound=false` the same call is a lookup that never creates anything. A board that does not exist
  comes back with `result 1` and `leaderBoardID 0`.
- `DeleteLeaderboard/v1` (POST, `appid`, `name`) answers `{"result": {"result": 1}}`. For a board that does not
  exist it answers `result 2`, still with HTTP 200.
- `GetLeaderboardsForGame/v2` answers `{"response": {"result": 1, "leaderboards": [{id, name, entries, sortmethod,
  displaytype, onlytrustedwrites, onlyfriendsreads, ...}]}}`. The list is cached:
  - A new board was missing from it, and a deleted board was still listed, for up to about a minute.
  - So the tool plans and reads back each board it is about to change with the lookup, not with the list.
- Settings of an existing leaderboard cannot be changed through the Web API without deleting it (and its scores);
  the tool only reports such differences.

Builds and the schema:

- `GetAppBuilds/v1` (`appid`, `count`) answers
  `{"response": {"builds": {"<BuildID>": {BuildID, CreationTime, Description, AccountIDCreator, depots:
  {"<DepotID>": {DepotID, DepotVersionGID, TotalOriginalBytes, TotalCompressedBytes}}}}, "result": 1}}`
  (`api/builds`, after one test build was uploaded with steamcmd and set live nowhere). On an app without any build
  it answers HTTP 500 `{"response": {"result": 2, "message": "Failed to query app builds"}}` (`api/read`).
- `GetAppBetas/v1` answered HTTP 500 `{"response": {"result": 42, "message": "Couldn't get app info for app …"}}` on
  two unreleased apps, with and without a build, so its success shape is not recorded.
- `GetAppDepotVersions/v1` and `GetPartnerAppListForWebAPIKey/v2` (the apps the key reaches) answered normally; the
  tool does not use them.
- steamcmd prints the builder account's id (`Logging in user '…' [U:1:…]`); the tool hides it with the name.
- `GetSchemaForGame/v2` returns the published schema only: `{"game": {}}` while nothing is published.

Builds going live (from Valve's documentation, not recorded):

- `SetAppBuildLive/v2` (POST): `betakey` is required, `public` meaning the default branch; a released app then
  also needs `steamid` and answers `201 Created` while the change waits for a Steam Mobile confirmation. The tool
  only sets beta branches live; the default branch stays a manual step (gate rule `build_set_live_default_branch`).

## Environment notes

- Playwright's bundled Chromium failed to start on Windows 11 ("side-by-side configuration"). Installed Chrome or
  Edge through Playwright's `channel` works.
- Antivirus products can inject scripts and CSP entries into pages. Recordings must strip them.
- Under tsx/esbuild, functions passed to `page.evaluate` need a `__name` shim (TypeScript only).
