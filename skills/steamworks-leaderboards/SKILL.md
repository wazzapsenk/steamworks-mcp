---
name: steamworks-leaderboards
description: Design Steam leaderboards (sort, display type, trusted writes), create them in Steamworks, and upload and show scores from the game. Use when the user wants high scores, speedrun times or rankings, or asks why scores do not appear.
---

# Leaderboards

Tools: `generate`, `set_field`, `approve_fields`, `apply`, `steamworks_inspect`, `integration_code`, `check_code`

## 1. Design

Each leaderboard in `steamworks.yaml` (`leaderboards`) has:

- `name`: the API name the code uses (letters, digits, underscores);
- `sort_method`: `descending` (higher is better: points) or `ascending` (lower is better: times);
- `display_type`: `numeric`, `seconds` or `milliseconds` (how Steam shows the score);
- `only_trusted_writes`: only a server (Web API with the publisher key) may write scores, against cheating;
- `only_friends_reads`: players only see friends' scores.

One leaderboard per level or mode is normal. `generate(path, section="code")` finds the leaderboards the code already
uses. Save new ones with `set_field` and approve them after the user agreed.

## 2. Create them in Steamworks

`apply(path, section="leaderboards")` creates missing leaderboards through the Web API (publisher key; dry run first,
then with the user's OK). Settings of an existing leaderboard cannot be changed this way without deleting it and its
scores. `steamworks_inspect(path, what="leaderboards")` shows what Steamworks has. Without the key, create them on the
Stats & Achievements > Leaderboards page.

## 3. Code

`integration_code(path, features=["leaderboards"])` writes upload code that finds the leaderboard and keeps the best
score. Reading scores: DownloadLeaderboardEntries with Global (top), GlobalAroundUser (around the player) or Friends,
then GetDownloadedLeaderboardEntry for each row. Up to 64 extra integers per entry can carry details (a replay id,
a character). A leaderboard with trusted writes rejects client uploads: the game's server calls the Web API instead.

`check_code` catches names that steamworks.yaml does not define.

Valve docs: https://partner.steamgames.com/doc/features/leaderboards

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
