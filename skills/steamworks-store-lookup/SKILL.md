---
name: steamworks-store-lookup
description: Look any game up on the Steam store by name, app id or store link - price, review score and count, players right now, release date, genres, modes, Steam features, platforms, languages, achievements, DLC. Use when the user asks about a specific Steam game ("how is X doing", "what does X cost", "how many people play X").
---

# Steam store lookup

Tools: `store_lookup`, `compare_games`

1. Call `store_lookup(query)` with what the user gave: a name, an app id or a store link. Show the `display` table.
2. If the name matched several games, `other_matches` lists them; ask which one was meant when the first match looks
   wrong (soundtracks and DLC have similar names).
3. Explain what the numbers mean when it helps:
   - **Review score**: Steam's label (Overwhelmingly Positive … Overwhelmingly Negative) from the share of positive
     reviews; the total counts every language.
   - **Playing now**: players this minute, not owners. It swings with the time of day; compare at similar times.
   - **Price**: the US store's full price; "now" shows a running discount.
   - **Modes / Steam features**: the store's categories (Online Co-op, Steam Cloud, Full controller support, …).

For several games side by side use `compare_games(appids=[...])` (the `steamworks-compare-games` skill).

Data comes from Steam's public store, is cached for a few hours, and nothing is saved in the project. Never present
these numbers as sales: Steam does not publish sales.
