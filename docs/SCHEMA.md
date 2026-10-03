# Schema

A game project keeps two things apart: **values**, in `steamworks.yaml` (edited by people and by the server), and
**per-field metadata**, in `.steam-mcp/state.json` (written only by the server). This page summarises both. The
exact definitions are the pydantic models in [`src/steamworks_mcp/manifest/`](../src/steamworks_mcp/manifest/)
and the generated JSON Schemas in [`docs/schema/`](schema/). For autocomplete in editors that support
yaml-language-server, put this line at the top of `steamworks.yaml`:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/wazzapsenk/steamworks-mcp/main/docs/schema/steamworks.schema.json
```

## Files

```
my-game/
├── steamworks.yaml              values; edit by hand too (comments are preserved)
├── localization/<lang>.yaml     translations, flat "field path: text"
└── .steam-mcp/
    ├── .gitignore               written by init_project
    ├── state.json               per-field metadata               (commit)
    ├── drafts/<field>/<id>.json text alternatives                (commit)
    ├── scan/                    scanner evidence                 (ignored)
    ├── exports/gate_<n>/        generated upload packages        (ignored)
    ├── snapshots/               Steamworks state before each apply (ignored)
    ├── cache/                   reference data                   (ignored)
    └── audit.jsonl              every write to Steamworks        (ignored)
```

## `steamworks.yaml`

A full example is [`examples/example-game/steamworks.yaml`](../examples/example-game/steamworks.yaml). Keys are
snake_case. Unknown keys are errors. A missing value (`null`, empty string or empty list) means "not known yet" and
shows up in gap reports. The file aims to cover every field of Steamworks and the store page; only filled values are
ever written to Steamworks, empty ones are left as they are there.

| Section | What it holds | Usually filled by |
|---|---|---|
| `game` | What the game is: name, pitch, genres, players, session length, core loop, USPs, audience, tone, platform features, engine, reference app ids. Input for writing; never uploaded. | scan + interview |
| `source_language`, `target_languages` | Steam API language codes (`english`, `schinese`, `koreana`, `brazilian`, `latam`, …) | interview |
| `apps` (`main`, `demo`, `playtest`) | One profile per Steam app: `appid`, `installation` (install folder, launch options), `cloud` (quotas, Auto-Cloud paths, root overrides), `builds` (depots with their language, branches) | scan + interview |
| `prerequisites` | Gate 0: partner account, Steam Direct fee (+ date), tax, bank, identity, restricted automation account | interview (never scanned) |
| `store` | Main store page: short description, About This Game (BBCode), developers, publishers, support info (website, e-mail, phone), platforms, primary genre and genres, tags, categories, languages table, system requirements, controller support (the Controller Support wizard), accessibility features (the Accessibility Features wizard), third-party DRM and accounts, links, legal line | generators + interview |
| `assets` | Source art (key art and its focus point, logo, screenshots folder, hand-made overrides, trailers); store images are cropped from these, never generated | user |
| `content` | Content survey, mature content descriptors, AI disclosure, ratings | interview (never assumed) |
| `achievements`, `stats`, `leaderboards` | Main game's definitions, keyed by API name | scan + generators |
| `pricing` | F2P, base price (USD), regional pricing, launch discount, submitted | interview |
| `release` | Coming Soon date, planned date, display date, Early Access answers, review results, Steam events | interview |

The models reject values Steamworks itself accepts silently (see
[STEAMWORKS_INTERNALS.md](STEAMWORKS_INTERNALS.md#what-the-server-does-not-validate)): duplicate API names, unknown
Auto-Cloud roots, empty Auto-Cloud patterns, quotas above 10,000,000,000 bytes / 10,000 files, and launch options
without an executable.

## Field paths

Every tracked value has a dotted path:

| Path | Addresses |
|---|---|
| `store.short_description` | an attribute |
| `store.supported_languages.german` | a dict entry |
| `achievements.ACH_WIN.name` | an item of a keyed list (`achievements`/`stats`/`leaderboards` by API name, `depots`/`branches` by name) |
| `apps.main.installation.launch_options.0.executable` | an item of any other list, by index |
| `store.system_requirements.windows.minimum` | a *unit*: small objects tracked as one field (requirements block, language row, player counts, progress) |
| `store.tags` | a list of plain values is one field |
| `localization.german.store.about` | a translation (stored in `localization/german.yaml`). Translated from the source language: `store.short_description`, `store.about`, `release.early_access_answers.*` (unless the game is not in Early Access), `achievements.*.name` and `.description`, `apps.*.installation.launch_options.*.description` |
| `checklist.<rule id>` | a manual gate step the user confirmed |

Patterns use `*` for one segment: `achievements.*.icon`. Gate files may only reference paths that exist in the
schema; a test enforces it.

## `.steam-mcp/state.json`

```json
{
  "state_version": 1,
  "fields": {
    "store.short_description": {
      "status": "approved",
      "source": "generated",
      "confidence": 0.0,
      "gate": 1,
      "execution_mode": "BROWSER",
      "value_hash": "sha256:…",
      "evidence": [{"file": "Assets/Scripts/Steam.cs", "line": 42, "note": ""}],
      "updated_at": "2026-10-03T12:00:00Z",
      "applied_at": null,
      "notes": ""
    }
  }
}
```

| `status` | Meaning |
|---|---|
| `missing` | no value |
| `draft` | from a scan, a generator or a reference default, and not yet reviewed |
| `needs_review` | the value changed after it was approved or applied (hash mismatch), or a translation's source changed |
| `approved` | the user confirmed it |
| `applied` | confirmed present in Steamworks: read back through API/BROWSER, or the user confirmed a manual step |

`source` is one of `scan`, `user`, `generated`, `reference_default`. Rules:

- Answers the user gives are stored as `approved`. Scanned, generated and default values are always `draft`; they
  are never auto-approved.
- Only `approved` values can be marked `applied`, and only while their hash still matches.
- Each time the files are loaded, the state is reconciled with the values. A changed value drops to `needs_review`.
  A value that was removed becomes `missing`. **A value with no state entry was typed by the user and counts as
  `approved`.**
- Translations are reconciled too: `localization/.lock.json` keeps the hash of the source text each translation was
  made from, and when the source changes, its translations drop to `needs_review`. Approving such a translation
  again records that it fits the new source.

## Drafts

Generators and reviews never overwrite a value. They save candidates in `.steam-mcp/drafts/<field>/<id>.json`:
the value, the strategy that produced it (`fantasy`, `mechanic`, `situation_humor`, `outline`, `revision`, …),
rubric results and score, and status `candidate | chosen | rejected`. The user picks one with
`set_field(path, from_draft=id)`.

## Gate files

`src/steamworks_mcp/data/gates/gate_{0..3}.yaml` list what Steam requires at each release gate. Each rule has a
check kind (`present`, `any_present`, `answered`, `confirmed`, `min_items`, `range`, `date_gap`, `asset`, `screenshots`,
`store_rules`, `crosscheck`, `checklist`, `info`), optional `when` conditions, `severity`
(`required | recommended | optional`), an `execution_mode`, and, for Valve rules, `source_doc` plus a verbatim
`quote` of at most 25 words. Rules the docs do not confirm are marked `unverified: true`. Gates 1 and 2 follow the
two release checklists on the app's Steamworks landing page ("Your Store Presence", "Your Game Build"): every item
has a rule whose `steamworks_checklist` names it and whose quote is Valve's explanation of the item, including the
items Steamworks adds once a feature is on (Steam Cloud quotas, achievements). In the BROWSER mode,
`steamworks_inspect(what="checklist")` reads the live checklists and links each item to its rule. The schema is
[`gate.schema.json`](schema/gate.schema.json).

Other bundled data:

- `store_rules.yaml`: Valve's store text, BBCode and asset content rules.
- `asset_specs.yaml`: image sizes and formats.
- `events.yaml`: Next Fest, sales and fests, with the discount and planning rules. Checks warn when it is stale.
- `capabilities.yaml`: rendered as [CAPABILITIES.md](CAPABILITIES.md).
- `languages.yaml`: Steam language codes.
