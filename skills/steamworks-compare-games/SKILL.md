---
name: steamworks-compare-games
description: Compare the user's game with its closest Steam games side by side - price, reviews, positive share, players right now, release date, modes, and which Steam features most of them use. Use for positioning ("how do we compare", "what do games like mine offer"), before setting a price, or when choosing Steam features.
---

# Compare with close games

Tools: `compare_games`, `store_lookup`, `study_market`, `save_market_study`, `price_brief`

## 1. Which games

- Default: the market study's games. If there is none, offer `study_market(path)` first (the `steamworks-market-research`
  skill): it finds the closest popular games by the game's store tags.
- Or the user names games: find their app ids with `store_lookup(name)`, then pass `appids=[...]` (at most 15).

## 2. Compare

`compare_games(path)` or `compare_games(path, appids=[...])`. Once the user's game is on the store it is included.
Show the `display` table, then read it for the user:

- **Price**: the median and the spread; where the user's planned price (`pricing.base_price_usd`) would sit.
- **Reviews**: counts show reach, the positive share shows reception; very new games have few reviews.
- **Modes**: if most close games have online co-op and the user's game does not, say what that means for the page.
- **Steam features**: the share of these games with achievements, cloud saves, controller support, trading cards,
  Workshop, Remote Play Together. Features most of them have are what players of the genre expect.

## 3. Next

Offer `price_brief` (the `steamworks-pricing` skill) for prices per country, and `study_reviews` (the
`steamworks-review-analysis` skill) for what players of these games praise and criticize.

Public store data, cached for a few hours; nothing is saved. Never name these games on the user's store page.
