---
name: steamworks-pricing
description: Decide a Steam price with data - what the closest games charge in the US and per country, a base price turned into regional prices, launch discount, Early Access and free-to-play questions - and record the decision in steamworks.yaml. Use when the user asks what to charge, about regional pricing, or about launch discounts.
---

# Pricing

Tools: `price_brief`, `compare_games`, `estimate_sales`, `set_field`, `mark_applied`, `gap_report`

The price is the user's decision. Bring the numbers, explain the trade-offs, then record what they choose.

## 1. What close games charge

`price_brief(path)` uses the market study's games (or `appids=[...]`). It shows their US full prices (median, middle
half) and for each country store how much they charge per US dollar, applied to the game's base price
(`pricing.base_price_usd`, else their median). `countries=[...]` picks other stores (two-letter codes).

Read it for the user: where the game sits against the middle half, and which stores price far below the US (many
Steam markets use regional prices well under the exchange rate, and some use US dollars at a lower price).

## 2. Things to weigh

- **Content and length** against the close games, not production cost.
- **Steam's recommended regional prices**: Steamworks proposes them from the base price; most developers accept them.
  Custom prices are possible per currency.
- **Launch discount**: allowed once around release; check Valve's current limits on the Discounting page before
  promising a percentage.
- **Discount rules**: Valve limits how often a game can be discounted and how a price change interacts with
  discounts. Plan seasonal sales with the `steamworks-sales-calendar` skill.
- **Early Access**: say on the page whether the price will change at full release (`release.early_access_answers.pricing_plans`).
- **Free-to-play**: `pricing.free_to_play: true`; monetization then goes through in-game purchases
  (the `steamworks-inventory` skill).
- `estimate_sales(path, wishlists=...)` shows what a price means for a first week, as a rough range.

## 3. Record the decision

`set_field(path, values={"pricing.base_price_usd": 19.99, "pricing.regional_pricing": "steam_recommended",
"pricing.launch_discount_percent": 10})` with what the user chose. The price itself is submitted by the user in
Steamworks (Valve approves price changes, which takes time); confirm it with `mark_applied` when they say it is done.
`gap_report(path, gate=3)` shows the remaining release steps.

Valve docs: https://partner.steamgames.com/doc/store/pricing, https://partner.steamgames.com/doc/marketing/discounts

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
