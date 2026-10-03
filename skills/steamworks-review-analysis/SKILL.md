---
name: steamworks-review-analysis
description: What players praise and criticize in the closest Steam games, or in the user's own game after launch - read the most helpful positive and negative reviews, label them with a fixed list of themes, and turn the result into store-page and update priorities. Use for "what do players of games like mine want", "what are people complaining about", or after a launch.
---

# Review analysis

Tools: `study_reviews`, `save_review_study`, `launch_watch`, `store_lookup`, `generate`

## 1. Start

- Close games: `study_reviews(path)` uses the market study's games (run `study_market` first if there is none).
- Specific games, or the user's own game after launch: `study_reviews(path, appids=[...])`.
- `per_kind` sets how many positive and how many negative reviews per game (default 10, up to 25).

It returns each game's most helpful English reviews (text and hours played) and a fixed list of themes. Nothing about
the reviewers is fetched; the texts stay in a local cache.

## 2. Label each game

Read the reviews of each game and decide, by how often and how strongly a theme comes up (not by one loud review):

- `praised`: 1-4 themes, most important first;
- `criticized`: 0-4 themes;
- `insight`: one sentence in your own words about what its players care about.

Send all notes at once with `save_review_study(path, notes=[{appid, praised, criticized, insight}, ...])`. An insight
that repeats four or more words of a review is rejected: rephrase it. Fix rejected notes and send them all again.

## 3. Tell the user

Show the `display` (theme shares across the games) and explain in a few lines:

- what players of this genre value most, and whether the user's game delivers it;
- the most common complaints, and whether the user's game does better there;
- for the user's own game: the top complaint to fix first, and what to keep.

From now on, `generate(section="store_short")` and the About briefs include the result under `players`: lead with a
praised theme the game really delivers, and answer a common complaint only where the game really does better.

Never quote reviews or reviewers anywhere, never put review text on the store page, and never name other games there.
