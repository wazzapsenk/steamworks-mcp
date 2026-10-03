---
name: steamworks-bug-reports
description: Collect useful bug reports from Steam players - a pinned discussion template, what to log in the game (build id, branch, OS, Steam Deck, Proton), how players get Steam's system information, crash dumps, and a known-issues post. Use when the user sets up bug reporting, gets vague reports, or prepares for launch support.
---

# Bug reports from players

Tools: `launch_watch`, `study_reviews`, `save_review_study`

## In the game (so every report has the facts)

Show and log these on a debug screen or in the log file:

- game version and Steam build id (ISteamApps::GetAppBuildId) and branch (ISteamApps::GetCurrentBetaName);
- OS, GPU, driver, resolution; ISteamUtils::IsSteamRunningOnSteamDeck; under Proton, the Windows build runs on
  Linux (check for Proton environment variables if it matters);
- where the log and save files are (the engine's per-user folder).

A "Copy debug info" button that puts this on the clipboard saves many round trips.

## Discussion template (pin it in the Community Hub)

Draft it for the user; a good one asks for:

1. What happened, and what you expected.
2. Steps to reproduce (from the main menu).
3. Game version / build id and branch (shown in the game's settings).
4. PC, Steam Deck, or Linux; for PC: Steam > Help > System Information, copied into a text file and attached.
5. The log file from <path>, and a save file if progress is involved.
6. Screenshot or clip.

## Crashes

Engines and services report crashes automatically (Unity Cloud Diagnostics, Sentry, Backtrace, Unreal's crash
reporter with your own endpoint). Ask before sending anything: say what is collected in the privacy policy.

## After launch

`study_reviews(path, appids=[<own app id>])` and `save_review_study` group complaints (bugs and stability,
performance, controls…); `launch_watch` shows whether reviews improve after a patch. Keep a pinned known-issues
post up to date and link it from patch notes (the `steamworks-community` skill).
