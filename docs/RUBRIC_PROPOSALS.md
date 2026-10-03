# Store text rubric: proposed additional rules

Research date: 2026-10-03.

**Status: proposals only, waiting for the maintainer's approval.** Nothing here has been added to
`src/steamworks_mcp/data/style_guides/_base_rubric.yaml` (layer 2) or to any other file in the repository. Each rule
below is a candidate. The maintainer accepts, edits or rejects it.

## Scope and method

- **Already covered, so not proposed again:** the draft rubric (layer 2: `short_first_sentence_genre`,
  `short_first_sentence_players`, `short_main_action`, `short_no_lore_opening`, `banned_openers`,
  `hollow_adjectives`, `claims_match_store`, `long_hook`, `long_paragraph_length`, `long_feature_list`,
  `long_mentions_features`, `long_length_range`, `long_media_count`, `long_first_media_early`,
  `long_placeholders_left`). Valve's rules that are already in layer 1 (`data/store_rules.yaml`) are also left out:
  plain-text and 300-character short description, no time-based text in the short description, no links or implied
  URLs, no advertising of other games, no Steam UI imitation, media size limits, textless images, walls of text,
  "detailed and coherent", launch content only, planned features marked, accolades and review quotes, and the
  localization mechanics.
- **Sources:** the Steamworks documentation (partner.steamgames.com), Chris Zukowski (How To Market A Game: his
  2020 Steam page checklist and his 2019 user study on Game Developer), Ryan Clark (Brace Yourself Games),
  GameDiscoverCo (Simon Carless), and one community blog post on Game Developer (Joe Henson), which is marked as a
  secondary source.
- **Quotes:** every quote is verbatim and at most 25 words. All web quotes were checked on 2026-10-03 by script
  against the live pages (exact substring match after normalizing whitespace and typographic quotes). The Zukowski
  checklist is a PDF, so its quotes were transcribed from the rendered pages and read back by eye.
- **Evidence labels:** `2+ independent` = at least two sources from different authors or organizations. `Valve only`
  = several Valve documents, no outside source. `single source` = one document. `single author` = more than one
  document, all by the same person. `extrapolated` = the source states the principle for a neighbouring case.
  `opinion-heavy` = the threshold or judgement is mostly ours.
- **Format:** the "how to check" blocks use the field names of `_base_rubric.yaml` (`kind`, `params`, `question`).
  Kinds marked *new* do not exist in `validate/rubric.py` yet.

## Proposals at a glance

The maintainer decided on every proposal on 2026-10-03; each section starts with the decision.

| # | id | applies_to | check | severity | evidence |
|---|----|-----------|-------|----------|----------|
| 1 | `short_states_hook` | short | llm_judged | warning | 2+ independent (Valve, Zukowski, Clark) |
| 2 | `short_no_mood_filler` | short | deterministic | warning | single author (Zukowski, 2 docs) + partial Valve support |
| 3 | `short_genre_specific` | short | deterministic | info | single author (Zukowski, 2 docs) |
| 4 | `short_no_future_promises` | short | deterministic (+ llm confirm) | warning | 2+ independent (Valve x2, Henson) |
| 5 | `long_opening_not_copy_of_short` | long | deterministic | info | 2+ independent (Valve, Henson), partly extrapolated |
| 6 | `long_headers_are_core_loop_beats` | long | llm_judged (+ deterministic precheck) | info | 2+ independent (Zukowski x2, Valve) |
| 7 | `long_uses_genre_vocabulary` | long | deterministic | info | single author (Zukowski, 2 docs) |
| 8 | `plain_language_no_jargon` | both | deterministic | info | 2+ independent (Valve, Henson) |
| 9 | `store_text_localized_for_supported_languages` | both | deterministic | warning | 2+ independent (Valve, GameDiscoverCo) |
| 10 | `demo_text_matches_demo_content` | both | deterministic + llm_judged | warning | single source (Valve requirement) |
| 11 | `long_editions_explained` | long | deterministic (llm fallback) | info | single source (Valve) |
| 12 | `long_no_stale_time_text` | long | deterministic | info | Valve only, extrapolated |
| 13 | `product_focused_not_studio` | both | deterministic precheck + llm_judged | info | single source (Valve), partly opinion |

Proposals 10 and 11 come from Valve requirements or explicit Valve advice. Proposal 10 might belong in layer 1
(`store_rules.yaml`) instead of the rubric. That is the maintainer's call.

---

## 1. `short_states_hook`

**Decision (2026-10-03):** accepted as a warning (`short_states_hook`, llm_judged).

- **applies_to:** short
- **check:** llm_judged
- **question:** "Apart from the genre and the core action, does the short description say what makes this game different
  from other games in its genre (its hook or twist)?"
- **severity:** warning
- **Rationale:** The genre and the core action tell a shopper what kind of game this is. The hook tells them why to
  pick this one over the genre's best-known game. Valve frames the short description around the game's unique
  value proposition. Clark notes that when a hook doesn't come across in trailers and text descriptions, the game has
  to rely on people playing it and spreading the word.
- **Overlap:** This complements `short_first_sentence_genre` and `short_main_action`. It does not require the hook to
  be in the first sentence.
- **Suggested fix text:** "Add one clause that names the twist only your game has."
- **Sources:**
  - Valve, *Store Page Written Description (Steamworks Documentation)*,
    https://partner.steamgames.com/doc/store/page/description (no date shown). "convey why a customer would want to
    learn more about this game in particular. What sets it apart from other games?"
  - Chris Zukowski, *Steam page checklist* (PDF, "as of March 2020"),
    https://howtomarketagame.com/wp-content/uploads/2020/03/SteamPageChecklistv1.pdf. "Short description identifies
    your game's "hook.""
  - Ryan Clark, *What Makes an Indie Hit?: How to Choose the Right Design*, Game Developer, 2015-09-17,
    https://gamedeveloper.com/business/what-makes-an-indie-hit-how-to-choose-the-right-design. "If the game's hooks
    do not translate well to trailers and text descriptions, you will be relying on people playing the game"
- **Evidence:** 2+ independent.

## 2. `short_no_mood_filler`

**Decision (2026-10-03):** accepted as info (`short_no_mood_filler`, kind `phrase_list_max`).

- **applies_to:** short
- **check:** deterministic
- **How to check:** a phrase list. Extend `word_list_max` to accept multi-word phrases, or add a *new* kind
  `phrase_list_max`:
  ```yaml
  kind: phrase_list_max
  params:
    max: 0
    phrases: [beautiful world, war-torn, vast world, rich lore, rich story, mysterious world, ancient evil,
              dark secrets, fate of the world, save the world, battle evil, forces of evil, epic journey,
              embark on a journey, adventure awaits, a world where, long-forgotten]
  ```
  The seed list includes the checklist's own examples. Match case-insensitively on word boundaries.
- **severity:** warning
- **Rationale:** In Zukowski's observation study, shoppers skipped mood and setting words and scanned the short
  description for verbs. The checklist says the setting comes across better in the screenshots and trailer. Stock
  setting phrases spend scarce characters on what the capsule and screenshots already show.
- **Overlap:** `short_no_lore_opening` only judges the opening. `hollow_adjectives` only counts single adjectives.
  This rule catches stock setting phrases anywhere in the short text, cheaply and without an LLM call.
- **Suggested fix text:** "Cut the setting phrase and spend the characters on what the player does."
- **Sources:**
  - Chris Zukowski, *Steam page checklist* (March 2020),
    https://howtomarketagame.com/wp-content/uploads/2020/03/SteamPageChecklistv1.pdf. "Remove descriptive "setting"
    text such as "beautiful world" or "war-torn land." Your game's setting is better established in your screenshots
    and trailer."
  - Chris Zukowski, *How Steam users see your game*, Game Developer, 2019-09-06,
    https://www.gamedeveloper.com/business/how-steam-users-see-your-game. "Participants skipped over "mood setting"
    words."
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "The short
    description is generally not the best place to describe your game's full feature list or its narrative story
    arc."
- **Evidence:** single author (two Zukowski documents, one of them a user study). Valve supports the idea only
  partly: it is about story arc, not setting phrases. The phrase list itself is ours.

## 3. `short_genre_specific`

**Decision (2026-10-03):** accepted as info (`short_genre_specific`, kind `genre_specificity`).

- **applies_to:** short
- **check:** deterministic
- **How to check:** a *new* kind `genre_specificity`. Use the genre words from `_genre_words()` (manifest genres plus
  the top 5 tags) and collect the ones that appear in the short text. Warn when every match is an umbrella term and
  none of the more specific top-5 tags appears.
  ```yaml
  kind: genre_specificity
  params:
    umbrella: [action, adventure, action-adventure, action adventure, indie, casual, simulation, strategy,
               rpg, arcade, sports, racing, singleplayer, multiplayer]
  ```
  Return `not_applicable` when the manifest has no tags more specific than the umbrella list.
- **severity:** info
- **Rationale:** Shoppers check whether a game belongs to the genres and subgenres they already like. A label such as
  "action adventure" passes `short_first_sentence_genre` but tells a roguelite or metroidvania fan nothing. Naming
  the subgenre is what lets them match the game to their taste.
- **Suggested fix text:** "Name the subgenre players search for (for example Roguelite, Deckbuilder,
  Metroidvania), not only the umbrella genre."
- **Sources:**
  - Chris Zukowski, *Steam page checklist* (March 2020),
    https://howtomarketagame.com/wp-content/uploads/2020/03/SteamPageChecklistv1.pdf. "Write your game's genre (be
    specific, don't say "action adventure")"
  - Chris Zukowski, *How Steam users see your game*, Game Developer, 2019-09-06,
    https://www.gamedeveloper.com/business/how-steam-users-see-your-game. "they were trying to determine if this
    game belongs to the genres and subgenres that they like to play."
- **Evidence:** single author (Zukowski, two documents). This is why the suggested severity is info.

## 4. `short_no_future_promises`

**Decision (2026-10-03):** accepted as a warning (`short_no_future_promises`, kind `regex_forbidden`).

- **applies_to:** short
- **check:** deterministic, with an optional llm_judged confirmation to rule out false positives
- **How to check:** the existing kind `regex_forbidden`. These patterns add to what layer 1's
  `short_description_no_time_based_text` already catches ("coming soon/on/in"):
  ```yaml
  kind: regex_forbidden
  params:
    patterns:
      - '\b(?:planned|future|upcoming) (?:update|content|feature|mode|character|map)s?\b'
      - '\bin (?:a|an|the) (?:future|upcoming|later) (?:update|patch|release)\b'
      - '\bwill be added\b'
      - '\b(?:roadmap|post-launch|full release)\b'
      - '\bmore \w+ (?:to come|on the way)\b'
  ```
  Optional confirmation question: "Does the short description promise content or features that are not in the
  current build?"
- **severity:** warning
- **Rationale:** Valve's review expects the store page to list only content that exists at launch. Early Access rules
  forbid selling on promises about the future. About This Game has room to mark planned content clearly (layer 1
  `description_planned_features_marked`). The short description has no such room, so future content should stay out
  of it entirely.
- **Overlap:** Layer 1 covers this for About This Game (llm_judged) and as a manual store-wide check. This rule adds a
  cheap automated check for the short description.
- **Suggested fix text:** "Describe what is in the game today. Move plans to an event or the Early Access
  questionnaire."
- **Sources:**
  - Valve, *Review Process (Steamworks Documentation)*, https://partner.steamgames.com/doc/store/review_process (no
    date shown). "Your store page should only contain features and content that will be available at launch"
  - Valve, *Early Access (Steamworks Documentation)*, https://partner.steamgames.com/doc/store/earlyaccess (no date
    shown). "Customers should be buying your game based on its current state, not on promises of a future that may or
    may not be realized."
  - Joe Henson (secondary: community blog), *How to Improve the About This Game section on your Steam store page*,
    Game Developer, 2021-05-10,
    https://www.gamedeveloper.com/business/how-to-improve-the-about-this-game-section-on-your-steam-store-page. "But
    be careful to not oversell/promise something! Stick to the core mechanics your game actually offers."
- **Evidence:** 2+ independent (two Valve documents plus one outside blog).

## 5. `long_opening_not_copy_of_short`

**Decision (2026-10-03):** accepted as info (`long_opening_not_copy_of_short`, kind `overlap_with_short`).

- **applies_to:** long (it also reads the short description)
- **check:** deterministic
- **How to check:** a *new* kind `overlap_with_short`. Compare the first N words of About This Game with the short
  description. Warn on a shared run of 8 or more words, which matches the repo's existing 8-word anti-copy
  convention, or when more than half of the short text's word 4-grams reappear.
  ```yaml
  kind: overlap_with_short
  params: {window_words: 60, max_shared_run: 7, max_4gram_ratio: 0.5}
  ```
- **severity:** info
- **Rationale:** The short description sits at the top of the same page. By the time someone reaches About This Game
  they have read it and looked at the screenshots and trailer. Repeating the short text wastes the section's "second
  hook". The opening should add something new, ideally the concrete moment of play that `long_hook` asks for.
- **Suggested fix text:** "Don't repeat the short description. Open with a moment of play it doesn't mention."
- **Sources:**
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "it is
    useful to consider what information the player needs now that they have likely looked at your screenshots and
    gameplay trailer."
  - Joe Henson (secondary: community blog), *How to Improve the About This Game section on your Steam store page*,
    Game Developer, 2021-05-10,
    https://www.gamedeveloper.com/business/how-to-improve-the-about-this-game-section-on-your-steam-store-page. "It's
    technically your second hook (your short description is your first"
- **Evidence:** 2+ independent, partly extrapolated. Neither source says "do not copy" in so many words. Both say
  the About section serves a reader who has already seen the short text and the media. Henson makes the same "don't
  show the same thing twice" point about GIFs.

## 6. `long_headers_are_core_loop_beats`

**Decision (2026-10-03):** accepted as info (precheck `long_headers_min`, kind `headers_min`, off when a genre guide makes headings optional; question `long_headers_are_core_loop_beats`).

- **applies_to:** long
- **check:** llm_judged, with a deterministic precheck
- **How to check:**
  - Precheck (*new* kind `headers_min`): count `[h1]`–`[h3]` lines, plus lines that consist only of `[b]…[/b]`. When
    About has 150 or more words, require at least 2 headers. A genre guide can turn the precheck off, as
    `coop_party` already does with `headings: optional`. The LLM part then runs only when headers exist.
  - Question: "Does each section header name one beat of the core gameplay loop as a player action (like Valve's
    example 'Build Beautiful Bouquets'), rather than a generic label such as 'Features', 'Story' or 'About'?"
- **severity:** info
- **Rationale:** People who read the long description skim its headings to work out how the game is played. That
  makes header text the most-read text in the section. Valve's own example header is a verb phrase placed next to the
  gameplay it describes, and Zukowski's checklist asks for one header per loop beat.
- **Overlap:** Layer 1 `description_avoid_walls_of_text` recommends bolding headers. This rule checks what the headers
  say. The same idea could later extend to the bullets of `long_feature_list` (lead with a player verb, not a tech
  spec). The evidence for that extension is the same as for this rule.
- **Suggested fix text:** "Give each section a short verb-led header naming one thing the player does."
- **Sources:**
  - Chris Zukowski, *Steam page checklist* (March 2020),
    https://howtomarketagame.com/wp-content/uploads/2020/03/SteamPageChecklistv1.pdf. "Section your description into
    subheads. Each one should be one of the primary beats of your game's loop."
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "if your
    game is about managing a flower shop, the headline 'Build Beautiful Bouquets' could be positioned next to a
    screenshot"
  - Chris Zukowski, *How Steam users see your game*, Game Developer, 2019-09-06,
    https://www.gamedeveloper.com/business/how-steam-users-see-your-game. "If a participant did read it they were
    looking for headings and keywords that explain the genre and how the game is played."
- **Evidence:** 2+ independent (Zukowski and Valve).

## 7. `long_uses_genre_vocabulary`

**Decision (2026-10-03):** accepted as info (`long_uses_genre_vocabulary`, kind `genre_keyword_coverage`; keywords come from the game's own genres and top tags).

- **applies_to:** long
- **check:** deterministic
- **How to check:** a *new* kind `genre_keyword_coverage`. Build a keyword set per genre from the top-5 tags plus
  single mechanic words that are frequent in the reference games' derived analysis (`data/references/analysis`).
  Use single words only, so the repo's anti-copy rule is never at risk. Require at least `min` distinct keywords in
  About This Game.
  ```yaml
  kind: genre_keyword_coverage
  params: {min: 2}
  ```
  If no reference analysis exists for the genre, fall back to llm_judged: "Would a fan of this genre's best-known
  game recognize its mechanics from the words used here?"
- **severity:** info
- **Rationale:** Shoppers buy within genres they already like and look for proof that a game plays like their
  favourites. When they read About This Game at all, it is usually because the screenshots and tags left the genre
  unclear, and they scan for genre keywords. Using the established vocabulary of the genre's anchor games answers
  that question.
- **Suggested fix text:** "Use the words fans of the genre use for its mechanics (runs, decks, permadeath, base
  building…)."
- **Sources:**
  - Chris Zukowski, *Steam page checklist* (March 2020),
    https://howtomarketagame.com/wp-content/uploads/2020/03/SteamPageChecklistv1.pdf. "Rely on the keywords you
    recorded when looking at your anchor game"
  - Chris Zukowski, *How Steam users see your game*, Game Developer, 2019-09-06,
    https://www.gamedeveloper.com/business/how-steam-users-see-your-game. "your store page must convince potential
    buyers that your game plays like the other games that they like."
- **Evidence:** single author (Zukowski, two documents). The threshold is ours.

## 8. `plain_language_no_jargon`

**Decision (2026-10-03):** accepted as info (`plain_language_no_jargon`, kind `unexplained_acronyms`).

- **applies_to:** both
- **check:** deterministic
- **How to check:** a *new* kind `unexplained_acronyms`. Flag tokens matching `^[A-Z0-9][A-Za-z0-9]{1,5}$` that
  contain at least two capitals (DPS, TTK, PvPvE). Skip a token when it is on the allowlist, part of the game title,
  or expanded in parentheses on first use. Also flag an optional list of insider words.
  ```yaml
  kind: unexplained_acronyms
  params:
    max: 0
    allow: [PC, AI, UI, HD, 4K, 2D, 3D, VR, RPG, FPS, RTS, MMO, PvP, PvE, DLC, NPC]
    jargon: [meta, ttk, dps, i-frames, procgen, min-max]
  ```
  Genre guides (layer 3) can extend `allow`, for example with `4X` for strategy.
- **severity:** info
- **Rationale:** Many shoppers meet the game, and sometimes its whole genre, for the first time on the store page.
  Valve explicitly asks developers to cut insider jargon and acronyms. The secondary source makes the same point about
  matching reading level and word complexity to the audience.
- **Suggested fix text:** "Spell out the term or replace it with what the player experiences."
- **Sources:**
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "They may
    be unfamiliar with your game or perhaps even your game's genre. Help onboard them by minimizing "insider" jargon or
    acronyms."
  - Joe Henson (secondary: community blog), *How to Improve the About This Game section on your Steam store page*,
    Game Developer, 2021-05-10,
    https://www.gamedeveloper.com/business/how-to-improve-the-about-this-game-section-on-your-steam-store-page. "This
    includes how many GIFs, how many bullet points, the reading level, the word complexity usage"
- **Evidence:** 2+ independent. The allowlist is opinion-heavy and should be tuned.

## 9. `store_text_localized_for_supported_languages`

**Decision (2026-10-03):** accepted as a warning (`store_text_localized_for_supported_languages`, kind `localized_text_coverage`, checked on the final text).

- **applies_to:** both
- **check:** deterministic
- **How to check:** a *new* kind `localized_text_coverage`. For every language that `steamworks.yaml` declares with
  Interface or Subtitles support, the short description and About This Game must be non-empty in that language. They
  must also differ from the English text, or pass a simple language-ID check. Optionally (info level), suggest store
  text in languages the game does not support yet but where wishlists are strong.
- **severity:** warning
- **Rationale:** Language support on Steam is decided by the game, not the store page. A game localized into German
  can still show German players an English store page. Valve recommends localizing the store page whenever the game
  is localized. GameDiscoverCo treats translation as a discovery lever and recommends translating the Steam page by
  launch. It notes that an untranslated game is not shown by default to most players in China, and it quotes an
  estimate that over 90% of post-announcement East Asian wishlists were due to localization.
- **Overlap:** Layer 1 explains the mechanics (`store_page_localizable_content`, `language_support_from_ingame`,
  `english_fallback_required`). This rule checks that the text actually exists for each supported language.
- **Suggested fix text:** "Add the short description and About text for every language the game supports."
- **Sources:**
  - Valve, *Localization and Languages (Steamworks Documentation)*,
    https://partner.steamgames.com/doc/store/localization (no date shown). "if your product is localized, you should
    consider localizing your store page as well"
  - Simon Carless, *Game localization for discovery: it's trickier than you think!*, GameDiscoverCo newsletter,
    2021-06-23, https://newsletter.gamediscover.co/p/game-localization-for-discovery-its. "Translate your Steam
    pages for launch, and even make translated versions of your Steam demos/Next Fest showcases for maximum reach."
- **Evidence:** 2+ independent.

## 10. `demo_text_matches_demo_content`

**Decision (2026-10-03):** accepted as a Valve rule (`demo_text_matches_demo_content` in `store_rules.yaml`, error).

- **applies_to:** both, only when the app is a demo with its own store page
- **check:** deterministic + llm_judged
- **How to check:**
  - Deterministic: run the `claims_match_store` logic against the demo's own manifest data (languages, player
    modes, platform features) instead of the base game's. Flag base-game-only claims unless the same sentence marks
    them as full-game content ("in the full game", "base game only").
  - Question: "Does the text say clearly what the demo contains (levels, modes, roughly how long it lasts) and mark
    anything that is only in the full game?"
- **severity:** warning. If it moves to layer 1, error.
- **Rationale:** A demo page that reuses the base game's text can promise modes or languages the demo doesn't have.
  Valve asks for a description of the demo's actual content. Base-game features, languages or player modes that the
  demo lacks must be left off the demo's page or clearly labelled.
- **Suggested fix text:** "Describe what the demo includes and label full-game-only features as such."
- **Sources:**
  - Valve, *Demos (Steamworks Documentation)*, https://partner.steamgames.com/doc/store/application/demos (no date
    shown). "Provide a detailed written description that clearly outlines the specific content of the demo." Also:
    "If certain features, languages, or player modes are supported in the base game but not in the demo, do not
    include them"
- **Evidence:** single source, but it is a Valve requirement. The maintainer may prefer to put it in
  `store_rules.yaml` (layer 1).

## 11. `long_editions_explained`

**Decision (2026-10-03):** accepted as info (`long_editions_explained`, llm_judged until steamworks.yaml holds edition data).

- **applies_to:** long
- **check:** deterministic, with an llm_judged fallback
- **How to check:** a *new* kind `editions_mentioned`. When the manifest lists two or more purchase options for the
  game (for example Standard, Deluxe, Supporter, Soundtrack Edition), each edition name must appear in About This
  Game with at least one item it includes. Otherwise return `not_applicable`. If edition data isn't available, ask:
  "If the game is sold in several editions, does the text say what each one adds?"
- **severity:** info
- **Rationale:** Shoppers choosing between editions need to see what the extra money buys, and Valve names About This
  Game as the place to explain it. Without that, the edition boxes in the purchase area are just names and prices.
- **Suggested fix text:** "Add a short 'Editions' block listing what each edition adds."
- **Sources:**
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "If your
    game has multiple editions, this is also a great place to describe what's included with the different purchase
    options."
- **Evidence:** single source (Valve advice, not a rule).

## 12. `long_no_stale_time_text`

**Decision (2026-10-03):** not taken.

- **applies_to:** long
- **check:** deterministic
- **How to check:** the existing kind `regex_forbidden`, reusing the patterns of layer 1
  `short_description_no_time_based_text` plus:
  ```yaml
  kind: regex_forbidden
  params:
    patterns:
      - '\bwishlist (?:now|today)\b'
      - '\bjust (?:released|launched)\b'
      - '\bdemo (?:is )?(?:out|available) now\b'
      - '\bkickstarter (?:is )?(?:live|campaign)\b'
      - '\bthis (?:spring|summer|fall|autumn|winter|year)\b'
      - '\b(?:early|mid|late|q[1-4]) 20\d\d\b'
  ```
- **severity:** info
- **Rationale:** Valve states its time-based text rule for the short description, but the reason it gives (such text
  soon goes out of date) applies just as much to About This Game. About is also edited less often. Valve's Next Fest
  guidance asks developers to review and update their descriptions before an event. Dated phrases are exactly what
  makes that review necessary.
- **Suggested fix text:** "Remove dates and 'now' phrases. Use events and announcements for news."
- **Sources:**
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "Don't add
    "time-related" text to the short description as these will soon become old and out of date."
  - Valve, *Steam Next Fest (Steamworks Documentation)*,
    https://partner.steamgames.com/doc/marketing/upcoming_events/nextfest (no date shown). "Review and update your
    store page with your latest assets, game descriptions, and tags"
- **Evidence:** Valve only, extrapolated from the short-description rule.

## 13. `product_focused_not_studio`

**Decision (2026-10-03):** accepted as info (precheck `product_focused_not_studio`, kind `studio_talk`; question `product_focused_not_studio_review`).

- **applies_to:** both
- **check:** deterministic precheck + llm_judged
- **How to check:**
  - Precheck: run the existing kind `regex_start_forbidden` on each paragraph's start (or the first 40 words):
    `^(?:we are|we're|our (?:small |tiny |indie )?(?:team|studio)|i am|i'm|as a solo dev(?:eloper)?|hi,? (?:i'm|we're))`.
    Also scan anywhere for `\b(?:kickstarter|patreon) backers\b`, `\bspecial thanks\b` and `\bthank you\b`.
  - Question: "Is any paragraph about the developers, their history, crowdfunding thanks or credits rather than about
    what the player gets from the game?"
- **severity:** info
- **Rationale:** Valve asks that descriptions stay on the product and on why a shopper should buy it: the game's theme,
  style, story and features. Studio backstory, thank-you notes and backer credits push gameplay text below the fold
  for readers who skim. Exception: when "made by one person" is itself part of the appeal, one line near the end is
  enough. This judgement is ours, not Valve's.
- **Suggested fix text:** "Move studio story and credits to the developer homepage or an event. Keep the
  description about the game."
- **Sources:**
  - Valve, *Store Page Written Description*, https://partner.steamgames.com/doc/store/page/description. "Your Store
    Page descriptions should stay focused on your product and why a potential customer should consider it as their
    next game purchase."
  - Same page. "These sections should be focused on describing the overall theme, style, story, and features of your
    game."
- **Evidence:** single source (Valve, one page). The "below the fold" argument is opinion.

---

## Considered and not proposed

- **No ALL CAPS or exclamation overuse:** no reputable source found, only SEO and tool vendors.
- **No review quotes or "award-winning" in the short description:** Valve's accolade rules cover capsules and the
  dedicated Reviews/Awards sections (already in layer 1). No source found that addresses the text fields directly.
- **Second-person voice ("you"):** already in the `coop_party` genre guide (layer 3). There is no general source
  beyond examples.
- **Sentence length or readability score:** the only support is Valve's "the more you write, the less likely a
  customer will read all of it" and Henson's reading-level remark. Any threshold would be ours, and
  `long_paragraph_length` plus `long_length_range` already cover most of it.
- **"Put the best content before Read more (~600 words)":** a single secondary source (Henson). It overlaps
  `long_length_range` and `long_first_media_early`.
- **Mike Rose (No More Robots), Valve GDC talks:** no text-specific store description guidance with a verifiable
  written quote was found. Rose's public advice is mostly about capsules, wishlists and timing. Valve's GDC talks are
  video-only, and the written documentation above is the citable equivalent.

## Source list

| Source | Author / org | Date | URL |
|--------|--------------|------|-----|
| Store Page Written Description | Valve | not shown | https://partner.steamgames.com/doc/store/page/description |
| Review Process | Valve | not shown | https://partner.steamgames.com/doc/store/review_process |
| Early Access | Valve | not shown | https://partner.steamgames.com/doc/store/earlyaccess |
| Localization and Languages | Valve | not shown | https://partner.steamgames.com/doc/store/localization |
| Demos | Valve | not shown | https://partner.steamgames.com/doc/store/application/demos |
| Steam Next Fest | Valve | not shown | https://partner.steamgames.com/doc/marketing/upcoming_events/nextfest |
| Steam page checklist (PDF) | Chris Zukowski | March 2020 | https://howtomarketagame.com/wp-content/uploads/2020/03/SteamPageChecklistv1.pdf |
| How Steam users see your game | Chris Zukowski (Game Developer) | 2019-09-06 | https://www.gamedeveloper.com/business/how-steam-users-see-your-game |
| What Makes an Indie Hit?: How to Choose the Right Design | Ryan Clark (Game Developer) | 2015-09-17 | https://gamedeveloper.com/business/what-makes-an-indie-hit-how-to-choose-the-right-design |
| Game localization for discovery: it's trickier than you think! | Simon Carless (GameDiscoverCo) | 2021-06-23 | https://newsletter.gamediscover.co/p/game-localization-for-discovery-its |
| How to Improve the About This Game section on your Steam store page | Joe Henson (Game Developer community blog, secondary) | 2021-05-10 | https://www.gamedeveloper.com/business/how-to-improve-the-about-this-game-section-on-your-steam-store-page |
