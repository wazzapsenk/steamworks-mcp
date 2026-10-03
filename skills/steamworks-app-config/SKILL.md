---
name: steamworks-app-config
description: The technical Steamworks setup of an app - depots per OS, launch options, install folder, redistributables, the demo and playtest apps, and system requirements - drafted from the project, checked, and applied or exported as a checklist. Use when the user sets up depots, launch options or a demo/playtest app, or asks what Steamworks' Installation and Depots pages need.
---

# App configuration: depots, launch options, installation

Tools: `status`, `generate`, `start_interview`, `set_field`, `approve_fields`, `validate`, `apply`, `steamworks_inspect`, `export_package`, `gap_report`

Every app has its own settings: the main game, its demo and its playtest each have an app id
(`apps.main`, `apps.demo`, `apps.playtest`). Most tools take `app="demo"` / `app="playtest"`.

## 1. Depots

A depot is one set of files Steam downloads (usually one per OS, sometimes per language or for optional content).
`generate(path, section="builds")` drafts a depot per OS from the platforms in steamworks.yaml. Depots are created by
hand in Steamworks (SteamPipe > Depots); enter their ids with `set_field` (`apps.main.builds.depots.<n>.depot_id`).
With the BROWSER mode, `apply(path, section="depots")` sets each existing depot's OS, architecture and language.

## 2. Installation and launch options

`apps.<app>.installation`: the install folder name and the launch options (executable, arguments, OS, a description
when there is more than one). Steam starts the first option that matches the player's OS. `start_interview` asks
what the scan could not find. `validate` rejects empty executables and options the build does not contain.
Apply with `apply(path, section="installation")` (BROWSER mode) or follow `export_package(path, gate=2)`.

Redistributables (Visual C++ runtime, DirectX, .NET) are ticked on the Installation page so Steam installs them on
first launch.

## 3. System requirements

`generate(path, section="requirements")` drafts minimum requirements from the engine and target platforms; always
review them with the user (real test hardware beats a guess).

## 4. Check and apply

`gap_report(path, gate=2)` lists what the build review still needs; `steamworks_inspect(path, what="depots")` and
`what="installation"` show what Steamworks has. Writes start as dry runs and wait in Steamworks' Publish tab.

DLC and packages are not edited by these tools (packages change what customers own at once): the `steamworks-dlc`
skill explains the setup.

Valve docs: https://partner.steamgames.com/doc/sdk/uploading, https://partner.steamgames.com/doc/store/application,
https://partner.steamgames.com/doc/sdk/installscripts

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
