---
name: steamworks-build-upload
description: Upload game builds to Steam with SteamPipe - build scripts generated from steamworks.yaml, steamcmd with a restricted builder account, beta branches, setting a build live, and running it from CI (GitHub Actions, GitLab, Jenkins) without leaking credentials. Use when the user uploads a build, sets up branches, or automates releases.
---

# Uploading builds (SteamPipe)

Tools: `generate`, `apply`, `steamworks_inspect`, `set_build_live`, `check_code`, `export_package`, `server_info`

## 1. Scripts

`generate(path, section="builds")` writes `app_build_<appid>.vdf` and one `depot_build_<depotid>.vdf` per depot to
`.steam-mcp/exports/gate_2/steam/` once the depot ids are known. Each depot script maps a local folder (the engine's
build output for that OS) to the depot. `check_code(path, rules=["steamworks-build-scripts"])` checks scripts that
already live in the project: missing folders, `setlive` set to default, steam_appid.txt in a build folder.

Set `"Preview" "1"` in the app script for a dry run that only reports what would upload.

## 2. The builder account

Valve recommends a separate Steam account for uploads with only Edit App Metadata and Publish App Changes to Steam.
Log steamcmd in once by hand (`steamcmd +login <account>`, with the password and Steam Guard code typed there); later
runs reuse the session. Set `STEAMCMD_PATH` and `STEAMCMD_USERNAME` (`server_info` shows whether they are set). Never
put the password in a script: `check_code` flags it.

## 3. Upload and branches

- `apply(path, section="build")` runs `steamcmd +login <account> +run_app_build <script> +quit` (dry run first).
- `steamworks_inspect(path, what="builds")` lists recent builds and branches (publisher key).
- `set_build_live(path, build_id, branch="beta")` sets a build live on a beta branch after the user agreed. Password
  protect test branches in Steamworks. The default branch (what every player gets) is set live by hand in
  Steamworks (SteamPipe > Builds); no script may do it.

## 4. CI

The usual pattern: a job builds the game for each OS into the folders the depot scripts point to, then runs steamcmd
with the builder account. Steam Guard needs a session from a previous login: run the job on a machine where steamcmd
is already logged in, or store steamcmd's `config/config.vdf` from such a login as an encrypted CI secret and restore
it at run time. Never commit config.vdf, ssfn files or passwords; upload only to a beta branch from CI and set the
default branch live by hand.

Valve docs: https://partner.steamgames.com/doc/sdk/uploading, https://partner.steamgames.com/doc/store/application/branches,
https://partner.steamgames.com/doc/store/updates

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
