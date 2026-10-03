---
name: steamworks-cloud-saves
description: Set up Steam Cloud saves end to end - find where the game saves, configure Steam Auto-Cloud paths and quotas in steamworks.yaml, apply them to Steamworks, write saves where Auto-Cloud finds them on Windows, macOS, Linux and Steam Deck, and test it. Use when the user wants cloud saves, asks why saves do not sync, or moves saves between platforms.
---

# Steam Cloud saves

Tools: `generate`, `approve_fields`, `apply`, `steamworks_inspect`, `integration_code`, `check_code`, `export_package`, `mark_applied`

## Two ways

- **Steam Auto-Cloud** (most games): Steam syncs files that match a root folder + subfolder + pattern, when the game
  starts and after it quits. No code needed beyond saving to the right folder.
- **ISteamRemoteStorage API**: the game reads and writes cloud files itself (FileWrite/FileRead). Only when saves need
  custom handling; it also counts against the quota.

## 1. Where the game saves

`check_code(path, rules=["steamworks-saves-cloud"])` flags saves Auto-Cloud cannot sync (PlayerPrefs in the Windows
registry, hard-coded Windows paths, BinaryFormatter). The engine's per-user folder is the right place:
Unity `Application.persistentDataPath`, Godot `user://`, Unreal `FPaths::ProjectSavedDir()` (SaveGames).
`integration_code(path, features=["cloud"])` writes a small helper that saves there safely.

## 2. Configure

`generate(path, section="cloud")` drafts `apps.main.cloud`: Auto-Cloud rows from the scanned save folder, the byte and
file quotas, and OS overrides so the same files sync between Windows, macOS and Linux (Steam Deck runs Linux or
Proton). Show the rows and approve with `approve_fields(path, ["apps.main.cloud"])` after the user agreed.

Quotas are per player: keep them above the largest save set (several slots, a settings file, backups).

## 3. Apply

- With the BROWSER mode: `apply(path, section="cloud")` (dry run first, then with the user's OK). The page then waits
  in Steamworks' Publish tab for the user.
- Without it: `export_package(path, gate=2)` writes the checklist with every value for the Steam Cloud page; confirm
  with `mark_applied` when the user entered them.

## 4. Test

Publish the Steam Cloud settings in Steamworks (or use "developers only" first). In the Steam client the game's
Properties > General shows the Steam Cloud switch. Play on one machine, quit, start on another (or delete the local
save): the save must come back. A save written while the game is still running after "quit" may miss the upload.

Valve docs: https://partner.steamgames.com/doc/features/cloud

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
