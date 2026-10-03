---
name: steamworks-steam-deck
description: Get a game ready for Steam Deck (and Linux through Proton) - what Valve's compatibility review checks (input, display, seamlessness, system support), the project's own problems found by check_code, and the store information. Use when the user wants "Deck Verified", tests on Steam Deck, or asks about Linux/Proton.
---

# Steam Deck

Tools: `check_code`, `status`, `set_field`, `gap_report`

## 1. What the review checks

Valve reviews games for Steam Deck and rates them Verified, Playable, Unsupported or Unknown:

- **Input**: the whole game works with the Deck's controls; glyphs show Deck buttons; text entry opens the on-screen
  keyboard; no mouse or keyboard needed.
- **Display**: the default resolution fits the Deck's 1280x800 (16:10) screen, and text is readable at that size.
- **Seamlessness**: no launcher or compatibility warnings, no external account or install step that needs a mouse.
- **System support**: the game runs (natively on Linux or through Proton), anti-cheat and middleware included.

## 2. The project's own problems

`check_code(path, rules=["steamworks-steam-deck"])`:

- a forced fixed resolution (default to the display's native resolution instead);
- no gamepad input anywhere (the `steamworks-input` skill);
- Easy Anti-Cheat or BattlEye, which run on Deck and Linux only with Proton support turned on in their dashboards.

Also run `check_code(path, rules=["windows_paths"])`: hard-coded Windows paths break under Proton and Linux.

## 3. Test

On a Deck (or Linux with Steam), install from a beta branch and play the first hour with the controls only. Watch the
first launch (shader compilation, launchers), text size, and suspend/resume. ISteamUtils::IsSteamRunningOnSteamDeck
lets the game pick Deck-friendly defaults.

## 4. Record it

`game.platform_features.steam_deck` (verified, playable, unsupported, unknown) and
`game.platform_features.controller` in steamworks.yaml; save with `set_field`. `gap_report`
lists the Controller Support survey as a step in Steamworks.

Valve docs: https://partner.steamgames.com/doc/steamhardware/recommendations, https://partner.steamgames.com/doc/steamhardware/compat,
https://partner.steamgames.com/doc/steamhardware/proton
