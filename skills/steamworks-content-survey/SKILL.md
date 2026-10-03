---
name: steamworks-content-survey
description: Steam's content survey and disclosures - mature content descriptors, the AI-generated content disclosure, third-party software and DRM, in-game purchases, and the accessibility features list - answered honestly from what the game contains. Use before the store page review, when the user asks about age ratings, AI disclosure, or why the store shows a content warning.
---

# Content survey and disclosures

Tools: `start_interview`, `set_field`, `gap_report`, `validate`, `export_package`, `mark_applied`

Steamworks asks these on the store page's content survey; the answers show on the store and in Steam's filters.
`gap_report(path, gate=1)` lists them; `start_interview(path, gate=1)` asks the questions this tool tracks.

## Mature content

`content.mature_descriptors` (e.g. some nudity or sexual content, frequent violence or gore, general mature
content) and `content.mature_description`, a plain sentence about what players will see. Optional age ratings go
in `content.ratings` (e.g. PEGI, ESRB). Be accurate: under-reporting gets the
page rejected or the game removed; over-reporting hides it from players who filter content.

## AI-generated content

Valve asks whether the game uses AI-generated content made before release (art, text, audio) and whether it
generates content live while playing, and how live generation is kept from producing illegal content
(`content.ai`: uses_ai, pre_generated, live_generated, guardrails). Answer what the team really did; the store
shows the disclosure. `validate` checks that the answers are complete. This tool never guesses these answers.

## Other disclosures

- **In-game purchases**, loot boxes: the store category and survey answers (the `steamworks-inventory` skill).
- **Third-party DRM and accounts**: an external launcher, an account that must be created, kernel-level anti-cheat.
- **Accessibility features**: Steamworks has a features survey (subtitles, remapping, colorblind modes…); list only
  what exists.
- **Controller support**: the Controller Support survey (the `steamworks-input` skill).

Save the answers with `set_field`. The survey itself is filled in Steamworks by the user: `export_package(path,
gate=1)` gives the checklist with every answer, and `mark_applied` records it once they are entered
(`content.survey_completed`).

Valve docs: https://partner.steamgames.com/doc/store/page, https://partner.steamgames.com/doc/store/review_process

Never claim something was done in Steamworks unless a tool reported it as applied, never approve values without the
user's agreement, and never publish: the user publishes in Steamworks.
