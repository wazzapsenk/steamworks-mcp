---
name: steamworks-input
description: Controller support on Steam - Steam Input action sets and in-game actions, button glyphs for Xbox, PlayStation, Switch and Steam Deck, gamepad emulation, on-screen keyboard, and what "Full controller support" on the store requires. Use when the user adds gamepad support, asks about glyphs or Steam Input, or prepares for Steam Deck.
---

# Controllers and Steam Input

Tools: `status`, `check_code`, `set_field`, `gap_report`

`status(path)` shows `game.platform_features.controller` (none, partial, full) from the scan.
`check_code(path, rules=["no_gamepad_input"])` tells whether the game reads a gamepad at all.

## Two ways to support controllers

1. **The engine's own gamepad input** (Unity Input System, Godot joypad events, Unreal Enhanced Input) and let Steam
   Input's gamepad emulation translate other controllers to an Xbox pad. Least work; glyphs then come from the
   engine, so show the right ones for the controller in use.
2. **The Steam Input API (ISteamInput)**: the game defines actions ("Jump", "Move") and action sets ("Menu",
   "Driving") in an In-Game Actions file; players can remap anything, and the game gets the right glyph for any
   controller (GetGlyphPNGForActionOrigin / GetGlyphSVGForActionOrigin), Steam Deck included. Call Init, RunFrame
   each frame (or let SteamAPI_RunCallbacks do it), get handles with GetActionSetHandle / GetDigitalActionHandle /
   GetAnalogActionHandle, and ActivateActionSet when the context changes.

## Store field

`store.controller` records the support level: "Full controller support" means every part of the game, menus and
text entry included, works with a controller, with matching glyphs, without a mouse or keyboard. Text entry uses the
Steam on-screen keyboard (ShowGamepadTextInput or ShowFloatingGamepadTextInput). Save the answers with `set_field`;
`gap_report` lists the Controller Support survey in Steamworks as a manual step.

Steamworks > Application > Steam Input lets you choose a default configuration (for example a gamepad layout) that
players get before they change anything.

## Steam Deck

Deck players use the built-in controller: glyphs should show Deck buttons, and the game should never need a mouse
or keyboard. More in the `steamworks-steam-deck` skill.

Valve docs: https://partner.steamgames.com/doc/features/steam_controller, https://partner.steamgames.com/doc/features/steam_controller/getting_started_for_devs,
https://partner.steamgames.com/doc/features/steam_controller/steam_input_gamepad_emulation_bestpractices, https://partner.steamgames.com/doc/api/ISteamInput
