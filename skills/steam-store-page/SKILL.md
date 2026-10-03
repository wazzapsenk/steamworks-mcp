---
name: steam-store-page
description: Write, check and translate a Steam store page (short description and About This Game) with the steamworks-mcp server's tools, from the interview through brief, draft, validation and saving to localization. Use when the user wants to write or improve their game's Steam store text and the steamworks-mcp server is connected.
---

# Steam store page with steamworks-mcp

You help a game developer write the text of their Steam store page. The steamworks-mcp server holds the game's
values (`steamworks.yaml`) and their status, writes briefs, checks texts against Valve's rules and a rubric, and
stores drafts. **You write the text; the server never invents it; the user decides.** This skill is the same
workflow as the server's `write_store_page` and `localize_everything` prompts, for clients that do not show MCP
prompts.

## Ground rules

- Every tool takes `path`: the game's folder (relative to the server's workspace root), the one with
  `steamworks.yaml`. If there is none yet, call `init_project(path)` first.
- Write in the **source language** only (`source_language`; the brief's `write_in` names it). Translations come at
  the end, from the approved text.
- Never name other games on the page: Valve bans references to other products. Comparable games
  (`game.comparable_games`) are for positioning and vocabulary only.
- Never reuse 8 or more consecutive words from another game's page; `save_draft` rejects such text.
- Show the user every draft. Only the user picks one (`set_field(field=..., from_draft=<id>)`) or approves
  (`approve_fields`). Never approve on your own, never say something is live on Steam unless a tool reported it as
  applied, and never publish: the user publishes in Steamworks.

## 1. Interview

Call `start_interview(path, gate=1)` and ask the user its questions in small batches (clients with forms show them
as a form and save the answers themselves). Save chat answers with `set_field(path, values={<question id>:
<answer>})`. Repeat until the store-page inputs are there:

- what the game is: name, one-sentence pitch, genres, comparable games ("for fans of X and Y")
- the **hook** (what no other game in its genre does) and the **player fantasy**
- the core loop in player verbs; players and modes (solo, co-op, PvP, how many)
- session length and run length; progression between sessions; what is in the game at launch, with numbers
- selling points, audience and tone
- languages: the source language, the languages the game supports (`store.supported_languages`) and the languages
  the texts are translated into (`target_languages`; the supported ones are suggested)

A brief that lists `missing_recommended` answers is telling you what to ask next.

## 2. Short description

1. `generate(path, section="store_short")`. Read the whole brief: `strategies`, `use_the_answers` (each answer with
   how to use it), `recent_successful_pages` (what popular new pages in the game's genre look like: lengths and
   shares, never text to imitate), `style_guide`, `rubric`, `valve_rules`, `format`.
2. Write three variants, one per strategy (fantasy, mechanic, situation_humor): plain text, 160-300 characters,
   2-3 sentences, genre + who plays + the core action first, then the hook.
3. Save each with `save_draft(path, field="store.short_description", value=<text>, strategy=<strategy>)`. If it is
   rejected (Valve rule or copied words), rewrite and save again. Read the returned rubric findings and
   `judge_these` questions.
4. Show the user the three variants with your notes. They pick one: `set_field(path,
   field="store.short_description", from_draft=<id>)`.

## 3. About This Game

1. `generate(path, section="store_long", stage="outline")`: propose an outline as numbered blocks (hook line,
   `[GIF: what it shows]`, short paragraphs, headings, one feature list). Build it from `use_the_answers`: open
   with the hook, one block per step of the core loop, progression, a feature list from the launch content.
   Save it with `save_draft(path, field="store.about", value=<outline>, strategy="outline")`.
2. When the user approves the outline (`set_field(path, field="store.about", from_draft=<id>)`), call
   `generate(path, section="store_long", stage="text")` and write the text in Steam BBCode with only the tags the
   brief allows. Put `[GIF: what it shows]` where a clip goes; the server turns these into a shot list.
3. `save_draft(path, field="store.about", value=<bbcode>, strategy="text")`, then `preview_store(path,
   about_draft=<id>)` so the user sees it as on the store, with the estimated end of the first screen.
4. The user picks the draft with `set_field(path, field="store.about", from_draft=<id>)`.

## 4. Validate

`validate(path, section="store")` checks Valve's rules in every language and the rubric, and returns questions
for you to judge. Answer them honestly with `validate(path, section="store", llm_judgements=[{rule_id, field,
outcome: pass|warn|fail, note}])`. To fix something, save a new version with `save_draft(..., strategy="revision")`
and let the user pick it; `validate` itself never changes anything.

## 5. Localize

1. `localization_status(path)`: per target language, what is missing or stale (its source changed). With no
   target languages, ask the user which languages the page and achievements should be translated into.
2. For each language with work: `localization_pending(path, language=<code>, limit=20)`, translate every entry from
   the source language following its `context`, `format` (keep BBCode tags exactly and in order), `max_length` and
   `glossary`, then `localization_set(path, language=<code>, translations={<key>: <text>})`. Stale entries show the
   `previous_translation`: update it instead of starting over. Resend rejected entries after fixing them. Repeat
   until nothing is pending, then move to the next language.
3. Translations are drafts. Summarize them per language; the user approves with
   `approve_fields(path, ["localization.<code>.*"])`.

## 6. Hand-off

`export_package(path, 1)` writes the texts of every language, `store_localization.json` for the store page's
Localization tab and a checklist. With the server's BROWSER mode, `apply(path, section="store_text")` shows a dry
run first; write only after the user agreed. Publishing stays with the user.
