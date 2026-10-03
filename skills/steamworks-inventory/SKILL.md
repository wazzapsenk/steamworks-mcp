---
name: steamworks-inventory
description: In-game items and purchases on Steam - the Steam Inventory Service (item definitions, playtime drops, crafting, an item store, trading and the Community Market) and Steam microtransactions for custom purchases, with the server-side checks each one needs. Use when the user plans cosmetics, loot drops, an in-game shop, DLC-like items or free-to-play monetization.
---

# Items and in-game purchases

Tools: `status`, `set_field`, `check_code`

Two Steam systems; pick by what the game sells.

## Steam Inventory Service (items Steam keeps for the player)

- **Item definitions**: a JSON schema uploaded in Steamworks (Inventory Service). Each definition has an id, a type
  (item, bundle, generator, playtime generator, tag generator), name, description, icon, and flags such as
  tradable and marketable.
- **Drops**: a playtime generator plus TriggerItemDrop gives items for time played, with limits Steam enforces.
- **Crafting**: an `exchange` recipe in the definition; the game calls ExchangeItems.
- **Item store**: definitions with a price; the game opens the purchase with StartPurchase and Steam handles payment.
- **Trading and Community Market**: per-definition flags; the market needs Valve's approval for the app.
- **Reading**: GetAllItems / GetResultItems. For anything a server must trust, send the serialized result to the
  server, or check the player's inventory with the Web API from the backend.

## Microtransactions (the game's own catalog)

ISteamMicroTxn is a Web API: the game's server calls InitTxn with the order, Steam shows the purchase overlay, the
client gets MicroTxnAuthorizationResponse_t, and the server calls FinalizeTxn. It needs the publisher key, so it
always runs on a server, never in the game. The server must also handle refunds and chargebacks (the transaction
reports).

## Store page and rules

- In-game purchases must be stated on the store page (the "In-App Purchases" category) and in the content survey.
- Loot boxes may be restricted in some countries; show odds where required.
- `pricing.free_to_play` in steamworks.yaml for free games with purchases.
- `check_code` reports a Web API key found in the game's files.

Valve docs: https://partner.steamgames.com/doc/features/inventory, https://partner.steamgames.com/doc/features/inventory/schema,
https://partner.steamgames.com/doc/features/microtransactions, https://partner.steamgames.com/doc/features/microtransactions/implementation
