---
name: steamworks-social
description: Steam's social features in a game - Rich Presence ("Playing Level 3 as Knight" in the friends list, join game), friends, avatars, invites, the Steam overlay (store, web pages, pausing), and Remote Play Together. Use when the user wants friends to see or join what they play, profile pictures in game, or a store link from the game.
---

# Friends, Rich Presence and the overlay

Tools: `status`, `set_field`, `check_code`

## Rich Presence

What friends see next to the player's name, and how they join.

- The game calls SetRichPresence(key, value). `steam_display` holds a token such as `#Status_InLevel`; the text comes
  from a Rich Presence localization file the developer uploads in Steamworks (Community > Rich Presence), where
  tokens can use other keys as %variables%: `"#Status_InLevel" "Level %level% as %class%"`.
- `connect`: a command line Steam passes to the game when a friend chooses "Join game" (e.g. `+connect_lobby 1234`).
- `steam_player_group` and `steam_player_group_size`: friends in the same group show as one party.
- Translate the tokens for every target language in the same file (one `"<language>"` block per language). The
  server's translation tracking does not cover this file yet: translate it in chat and let the user upload it.

## Friends and avatars

ISteamFriends: GetFriendCount / GetFriendByIndex (immediate friends), GetFriendPersonaName, GetPersonaState.
Avatars: GetSmallFriendAvatar / GetMediumFriendAvatar / GetLargeFriendAvatar give an image handle; ISteamUtils
GetImageSize and GetImageRGBA give the pixels (large avatars may arrive later through AvatarImageLoaded_t).

## The overlay

Shift+Tab by default. ActivateGameOverlay("friends"), ActivateGameOverlayToWebPage(url),
ActivateGameOverlayToStore(appid, …) for a DLC or the full game from a demo, ActivateGameOverlayInviteDialog(lobby).
GameOverlayActivated_t tells the game to pause. The overlay does not work in most engine editors; test in a build.

## Remote Play Together

Lets friends join local multiplayer over the internet with no network code. Turn it on in Steamworks for games with
local co-op or versus; the store category follows.

Valve docs: https://partner.steamgames.com/doc/features/enhancedrichpresence, https://partner.steamgames.com/doc/api/ISteamFriends,
https://partner.steamgames.com/doc/features/overlay, https://partner.steamgames.com/doc/features/remoteplay
