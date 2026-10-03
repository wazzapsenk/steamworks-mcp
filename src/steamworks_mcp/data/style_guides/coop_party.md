---
# Machine-readable part (layer 3 of the store text rules: genre overrides of the base rubric).
# Ranges come from the derived analysis of the references below (data/references/analysis/*.json, 2026-10-03).
id: coop_party
title: Co-op party games ("friend-slop")
status: draft
applies_to_tags: [coop_party]
references: [3241660, 3527290, 3892270, 4069520]
short_description:
  chars: {min: 160, max: 300}
  sentences: {min: 2, max: 3}
  must_mention_players: true
  second_person: preferred
long_description:
  words: {min: 120, max: 400}
  max_paragraph_words: 60
  media: {min: 3, max: 10}
  first_media_within_words: 80
  feature_list_items: {min: 3, max: 9}
  headings: optional
achievements:
  count: {min: 30, max: 70}
  kinds: {progression: {min: 0.5}, skill: {max: 0.25}, secret_funny: {min_count: 1}}
  description_words: {max: 12}
store:
  screenshots: {min: 8}
  trailers: {min: 2}
---

# Co-op party games

*Draft written by the tool from four references' derived measurements. Edit it freely: this file is the source of
truth for the genre. It never quotes the references and generated text must never copy them (8-word rule).*

## Who buys these games

Groups of two to six friends who already play together, often on voice chat. A purchase starts with one person
sending the store page to the group, so the page has to answer two questions fast: **how many of us can play**, and
**what will we be laughing about in ten minutes**. Low prices are normal ($7.99 to $12.99 among the references),
which makes "buy it for the whole group" an easy decision.

## Short description (160–300 characters, 2–3 sentences)

- All four references mention the player count or mode (co-op, friends, a number) in the short description. Put
  it in the first sentence, together with the core action.
- Address the player as "you" (three of four do it). The friends are the protagonists, not a story character.
- Sentence 1: what you and your friends do. Sentence 2: the twist that causes chaos (physics, betrayal, a monster,
  a bet). Optional sentence 3: the payoff or the stakes.
- No lore openers, no "welcome to", no genre lists. Concrete verbs: climb, haul, bet, smash, scream, carry.

## About This Game (120–400 words)

- **Show it, then say it.** References use 3–8 animated clips, and the first clip comes within the first ~80
  words. Two of four open with an animation before any text.
- **Short block + clip, repeated.** Paragraphs average 20–32 words (never over ~60). The page reads as a sequence
  of short paragraphs, each with a clip of the moment it describes.
- **Headings are optional.** Two references use a heading per section; two use none. With headings, keep each one
  a short, punchy phrase.
- **One bullet list near the end** (3–9 items): modes, player count, platforms, progression, cosmetics.
- Say the player count and session length plainly somewhere, and mention proximity voice chat or Remote Play
  Together if the game has them. This is what a group decides on.
- Humor comes from situations the game creates ("someone knocked the whole stack over"), not from jokes about the genre.

## Media

- Screenshots: 5 to 17 among the references (8+ recommended). Show several players on screen at once; a lone
  character undersells a co-op game.
- Trailers: 2–4. The first should be gameplay with friends' reactions; a short clip with a clear hook beats a
  long cinematic.

## Achievements

- Not required: one reference launched without any. The others have **50–64**.
- Mix: mostly progression (55–70%) such as reaching areas, finishing runs and milestones. A smaller skill group
  (0–20%) for runs under a condition, and at least one silly or secret achievement about a typical group mishap.
- Names are short (2–3 words). Descriptions are about 7 words and often contain a number (about 40%).
- API names: consistent UPPER_SNAKE or PascalCase. Pick one style and keep it.
- A median global unlock rate around 10% means most achievements are goals a regular group reaches over several
  sessions, not in the first hour.

## Steam features seen in the references

Online Co-op (4/4), Steam Achievements (3/4), Steam Cloud (3/4), Full controller support (2/4), Trading Cards (1/4).
None list Remote Play Together, even though friend groups often expect it: a possible differentiator if the game
supports it. Languages ranged from English only at launch to 17; adding languages later is common in this genre.
