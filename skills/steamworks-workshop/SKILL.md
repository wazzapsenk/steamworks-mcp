---
name: steamworks-workshop
description: Steam Workshop for mods and user-made content - turning it on in Steamworks, uploading and updating items from the game or a tool, the Workshop legal agreement, reading subscribed items, queries and tags, and the store category. Use when the user wants mod support, level sharing or user-generated content on Steam.
---

# Steam Workshop

Tools: `status`, `set_field`, `check_code`

## Turn it on

Steamworks > Workshop: enable the Workshop for the app, choose who can see it, and set the tags players can pick.
"Ready-to-use" items go straight into the game; curated items are for content the developer approves. Publish the
change in Steamworks.

## Uploading (ISteamUGC)

1. CreateItem(appid, k_EWorkshopFileTypeCommunity) gives CreateItemResult_t with the new id. If
   m_bUserNeedsToAcceptWorkshopLegalAgreement is set, open the item page so the player accepts the agreement
   (ActivateGameOverlayToWebPage with steam://url/CommunityFilePage/<id>); until then the item stays hidden.
2. StartItemUpdate, then SetItemTitle, SetItemDescription, SetItemContent (a folder), SetItemPreview (an image),
   SetItemTags, SetItemVisibility.
3. SubmitItemUpdate with a change note; GetItemUpdateProgress shows the upload.

Mod tools outside the game can use the same calls with the Steam client running.

## Using items in the game

GetNumSubscribedItems / GetSubscribedItems, then GetItemInstallInfo for each item's folder. DownloadItem forces an
update; ItemInstalled_t tells when an item arrives. Browse with CreateQueryAllUGCRequest (or by user) and
SendQueryUGCRequest, filtered by tags.

## Rules of thumb

- Load content defensively: user files can be broken or malicious. Never run code from an item without sandboxing.
- Version the format so old items keep working after updates.
- The store page category "Steam Workshop" belongs on the page once it works (`store.categories`).

Valve docs: https://partner.steamgames.com/doc/features/workshop, https://partner.steamgames.com/doc/features/workshop/implementation,
https://partner.steamgames.com/doc/api/ISteamUGC
