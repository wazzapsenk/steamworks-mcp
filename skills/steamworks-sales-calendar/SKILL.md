---
name: steamworks-sales-calendar
description: Plan a release date, Steam Next Fest, seasonal sales and discounts with the bundled Steam event calendar and the game's own dates - registration deadlines, demo timing, weekday release, discount spacing. Use when the user picks a release date, asks about Next Fest or Steam sales, or plans discounts.
---

# Release date, festivals and sales

Tools: `get_spec_info`, `gap_report`, `set_field`, `status`

## 1. The calendar

`get_spec_info("events")` lists the upcoming Steam Next Fest editions (with registration deadlines and the demo
requirements) and the seasonal sales, with when the data was last checked. If the data is old, tell the user to
confirm the dates in Steamworks (Marketing & Visibility > Upcoming Events) before planning around them.

## 2. Release date

- `release.planned_date` (and `release.display_date` like "Q2 2027" while it is not fixed). Set with `set_field`.
- `gap_report(path, gate=3)` checks the release date against the rules it knows: weekday releases, avoiding the big
  seasonal sales, the waiting period after paying the app fee, and the store page being public as Coming Soon long
  enough to collect wishlists.
- Releasing right before or inside a big seasonal sale puts a full-price new game next to thousands of discounts.

## 3. Steam Next Fest

A free online festival with demos. A game can take part once, before its release, with a demo that is ready by the
registration deadline. Plan the demo (the `steamworks-playtest-demo` skill) and the store page (Coming Soon) well
before. `release.events` records the events the user signed up for.

## 4. Discounts

- A launch discount is possible around release.
- Seasonal sales and other discounts are opt-in in Steamworks; Valve limits how often a game can be discounted and
  how price changes and discounts interact. Check the current rules on the Discounting page before a plan.
- Steam emails wishlisters when the game is discounted, which makes the first good discount count.

Valve docs: https://partner.steamgames.com/doc/marketing/upcoming_events, https://partner.steamgames.com/doc/marketing/upcoming_events/nextfest,
https://partner.steamgames.com/doc/marketing/discounts, https://partner.steamgames.com/doc/store/coming_soon

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
