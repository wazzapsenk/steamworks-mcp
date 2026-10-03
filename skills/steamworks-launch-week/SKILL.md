---
name: steamworks-launch-week
description: The days after a Steam release - track reviews, players and price against the last check, find what players praise and complain about, plan the first patch and the announcement for it. Use when the game was just released, or the user asks how the launch is going.
---

# Launch week

Tools: `launch_watch`, `study_reviews`, `save_review_study`, `compare_games`, `store_lookup`

## Every day

`launch_watch(path)` shows the review score and count, players right now and the price, against the previous check
(kept in `.steam-mcp/market/launch.jsonl`). Show the `display` and say what changed: new reviews, the positive share
moving up or down, players compared with the last check at a similar time of day.

Steam's review label changes at fixed shares of positive reviews and needs enough reviews to show at all; a few
early negative reviews weigh a lot.

## What players say

`study_reviews(path, appids=[<the game's app id>], per_kind=20)`, label the game (praised, criticized, insight) and
save it with `save_review_study`. Turn the criticized themes into a short list for the user, most frequent first,
with what would fix each: bugs and performance first, then balance and quality-of-life, then content.

## The first patch

- Fix crashes and progress-blocking bugs first; a quick patch in the first days is normal and noticed.
- Post an update announcement with the patch notes (the `steamworks-community` skill drafts it).
- Reply to negative reviews that report a fixed problem: Steamworks lets developers respond publicly. Be short and
  factual, never argue.
- Builds go up with SteamPipe; test on a beta branch before setting the default branch live (the
  `steamworks-build-upload` skill). The default branch is set live by hand in Steamworks.

## Compare

`compare_games(path)` puts the game next to its closest games now that it is on the store.

Never claim something was done in Steamworks unless a tool reported it as applied, and never publish: the user
publishes in Steamworks.
