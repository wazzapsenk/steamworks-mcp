---
name: steamworks-sales-estimate
description: Rough sales estimates on Steam - copies sold by close games from their review counts, and a first-week range for the user's game from its wishlists and price, with every rule of thumb listed. Use when the user asks how many copies a game sold, what their wishlists might turn into, or wants a revenue range.
---

# Sales estimates

Tools: `estimate_sales`, `get_spec_info`, `store_lookup`, `price_brief`

Steam publishes no sales numbers. Everything here is a rule of thumb with a wide range: present it that way, every
time, and never as a forecast.

## Close games

`estimate_sales(path)` (the market study's games) or `estimate_sales(appids=[...])`. For each game: reviews, copies sold
as a low–high range (reviews × copies per review), and gross revenue at the full US price. Explain:

- **Copies per review**: commonly quoted between about 20 and 60. Cheap games, older games and games with many
  non-English players sit higher; expensive or niche games lower.
- **Gross at full price** is an upper bound: discounts, regional prices, refunds, taxes and Valve's share make the real
  revenue much lower.

## The user's game

`estimate_sales(path, wishlists=N)` with the wishlists on release day (Steamworks shows them under Wishlists). With
`pricing.base_price_usd` (and `pricing.launch_discount_percent`) set, it adds first-week revenue before and after
Valve's share. Commonly quoted: around 10 percent of launch wishlists buy in the first week; a strong launch, a
discount and good early reviews push it up.

The rules of thumb are in `get_spec_info("estimates")`; if the user has better numbers for their genre, say how the
range would change.

## Wishlists

Ways to grow them before launch: a Coming Soon page early, a demo for Steam Next Fest (the `steamworks-playtest-demo`
and `steamworks-sales-calendar` skills), events and announcements on the store page, and showing the game where its
players are. Steam emails wishlisters when the game releases and when it is discounted.

Valve docs: https://partner.steamgames.com/doc/marketing/wishlist
