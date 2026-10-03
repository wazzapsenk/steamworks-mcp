---
name: steamworks-api-reference
description: A map of the Steamworks SDK interfaces and the Steam Web API - which interface does what, callbacks versus call results, the publisher key versus a user key, which calls must stay on a server, and where each one is documented. Use when the user asks which Steam API to use, how a call works, or about the Web API.
---

# Steamworks API map

Tools: `integration_code`, `check_code`, `get_spec_info`

For code that matches the game's own names, prefer `integration_code(path)`; this map is for questions beyond it.

## SDK (in the game)

| Interface | For |
|---|---|
| steam_api (SteamAPI_Init/InitEx, RestartAppIfNecessary, RunCallbacks, Shutdown) | Starting and stopping |
| ISteamUserStats | Stats, achievements, leaderboards, global achievement rates, number of current players |
| ISteamRemoteStorage | Steam Cloud files from code (Auto-Cloud needs no code) |
| ISteamFriends | Friends, avatars, Rich Presence, overlay (store, web pages, invites) |
| ISteamMatchmaking / ISteamMatchmakingServers | Lobbies; the game server browser |
| ISteamNetworkingSockets / ISteamNetworkingMessages | Networking over Steam Datagram Relay (ISteamNetworking is deprecated) |
| ISteamGameServer | Dedicated servers: logon, player auth, server browser data |
| ISteamUser | Auth tickets, Steam ID, encrypted app tickets |
| ISteamApps | Ownership, DLC (BIsDlcInstalled), build id, beta branch, language |
| ISteamUGC | Workshop items |
| ISteamInventory | Steam Inventory Service items, drops, item store |
| ISteamInput | Steam Input actions, glyphs, controller types |
| ISteamUtils | Overlay state, on-screen keyboard, Steam Deck check, images, server time |
| ISteamScreenshots, ISteamTimeline, ISteamMusic, ISteamHTMLSurface | Screenshots, game recording markers, music, in-game browser |

**Callbacks** arrive through SteamAPI_RunCallbacks for events anyone can trigger (overlay opened, lobby invite);
**call results** answer one request (a leaderboard search) and need the handle the call returned (CCallResult in C++,
CallResult<T> in Steamworks.NET, signals in GodotSteam, async in Facepunch).

## Web API (on a server)

- `api.steampowered.com` with a user's Steam Web API key: public data (player summaries, owned games of public
  profiles, global stats).
- `partner.steam-api.com` with the **publisher key**: your apps' private data and actions (builds, branches,
  leaderboards with trusted writes, microtransactions, inventory, auth from a backend). The publisher key never goes
  into a game or a repository (`check_code` flags it); create it per group in Users & Permissions with the fewest
  permissions.
- Auth from a backend: ISteamUserAuth/AuthenticateUserTicket with a ticket from GetAuthTicketForWebApi.

## Versions

Use a recent SDK; very old calls (RequestCurrentStats before achievements) are gone in current SDKs, where stats load
at init. Wrappers lag the SDK slightly; check their release notes.

Valve docs: https://partner.steamgames.com/doc/sdk/api, https://partner.steamgames.com/doc/api/steam_api,
https://partner.steamgames.com/doc/webapi_overview, https://partner.steamgames.com/doc/webapi_overview/auth,
https://partner.steamgames.com/doc/webapi
