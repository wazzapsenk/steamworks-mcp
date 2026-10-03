---
name: steamworks-multiplayer
description: Steam multiplayer for a game - lobbies and invites, matchmaking, peer-to-peer and server networking over Steam's relay, dedicated servers, and the engine libraries that wrap them (Netcode, Mirror, FishNet, GodotSteam, Unreal online subsystem) - plus the store categories and checks that come with them. Use when the user adds co-op or online play, friends' invites, or picks a networking stack.
---

# Multiplayer on Steam

Tools: `status`, `set_field`, `check_code`, `gap_report`, `compare_games`

Start with `status(path)`: `game.players` (local, online co-op, PvP, max players) and the engine decide the plan. Save
missing answers with `set_field`.

## The building blocks

| Need | Steamworks | Notes |
|---|---|---|
| Party / room | ISteamMatchmaking lobbies | CreateLobby (private, friends-only, public, invisible), lobby data key/values, chat, member list |
| Invite friends | ISteamFriends | ActivateGameOverlayInviteDialog; accepting fires GameLobbyJoinRequested_t, or the game starts with `+connect_lobby <id>` |
| Find strangers | Lobby list | RequestLobbyList with string, number, distance and slots filters |
| Send data, P2P | ISteamNetworkingSockets (connections) or ISteamNetworkingMessages (no connection, closest to the old API) | Traffic goes through Steam Datagram Relay: no port forwarding, players' IPs stay hidden |
| Dedicated servers | ISteamGameServer + server browser | Servers log on anonymously or with a game server login token; players are verified with auth tickets |

The old ISteamNetworking P2P API (SendP2PPacket) is deprecated: `check_code` flags it.

## Engine libraries

- **Unity**: Netcode for GameObjects, Mirror or FishNet with a Steam transport (Facepunch or Steamworks.NET based),
  or Facepunch.Steamworks / Steamworks.NET directly for lobbies.
- **Godot**: GodotSteam lobbies plus its Steam multiplayer peer for Godot's high-level multiplayer.
- **Unreal**: the Online Subsystem Steam sessions and the Steam Sockets net driver.

Keep one source of truth for who is in the session (the lobby), and let the host or server own the game state.

## Security

Never trust what a client says about itself. A server verifies an auth ticket (BeginAuthSession, or the Web API from a
backend) before it trusts the Steam ID; `check_code` reports tickets that nothing verifies. Anti-cheat: the
`steamworks-anticheat` skill.

## Store and checks

- Store categories must match what the game really has: Online Co-op, Online PvP, LAN, Shared/Split Screen,
  Cross-Platform Multiplayer, Remote Play Together (local multiplayer over the internet, no code needed).
  `gap_report` checks that the categories match `game.players`.
- `compare_games` shows which modes close games offer.
- Test with two Steam accounts on two machines (a second account needs its own copy: a developer key or a beta).

Valve docs: https://partner.steamgames.com/doc/features/multiplayer, https://partner.steamgames.com/doc/features/multiplayer/matchmaking,
https://partner.steamgames.com/doc/features/multiplayer/networking, https://partner.steamgames.com/doc/features/multiplayer/game_servers

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
