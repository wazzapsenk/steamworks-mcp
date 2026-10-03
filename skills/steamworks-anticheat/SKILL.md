---
name: steamworks-anticheat
description: Cheating and anti-cheat on Steam - server authority and verified tickets first, then VAC, Easy Anti-Cheat or BattlEye, their Steam Deck and Linux (Proton) settings, game bans, and leaderboard protection. Use when the user ships competitive multiplayer, worries about cheaters, or asks whether their anti-cheat works on Steam Deck.
---

# Anti-cheat

Tools: `check_code`, `status`, `set_field`

## 1. The foundation (no anti-cheat replaces it)

- The server (or host) decides what happens; clients send inputs, not results.
- Verify who a player is: the server checks the auth ticket (BeginAuthSession, or the Web API from a backend) before
  trusting the Steam ID. `check_code(path, rules=["auth_ticket_unverified"])` finds tickets nothing verifies.
- Leaderboards that matter use trusted writes: only the server submits scores through the Web API
  (`leaderboards.<name>.only_trusted_writes`, the `steamworks-leaderboards` skill).
- Never ship the publisher Web API key in the game (`check_code` flags it).

## 2. Anti-cheat services

| Service | What it is | Steam Deck / Linux |
|---|---|---|
| VAC (Valve Anti-Cheat) | Valve's system for multiplayer games; turned on in Steamworks; needs the game's servers to use Steam auth | works with Proton |
| Easy Anti-Cheat | Free through Epic Online Services; client + server integration | turn on Linux/Proton support in its dashboard |
| BattlEye | Commercial; client + server | Proton support must be enabled with BattlEye |

`check_code(path, rules=["anticheat_proton"])` notes when one is used. A Deck "Unsupported" rating often comes from
anti-cheat that refuses Proton (the `steamworks-steam-deck` skill).

## 3. Bans

Valve lets developers issue game bans from their own detection (through the Web API), shown on the player's
profile. Have clear rules, evidence and an appeal path before banning.

## 4. Store

Kernel-level anti-cheat must be disclosed on the store page (Steamworks asks about it with the third-party software
and DRM questions).

Valve docs: https://partner.steamgames.com/doc/features/anticheat, https://partner.steamgames.com/doc/features/auth,
https://partner.steamgames.com/doc/steamhardware/proton
