# steamworks-mcp

**Your Steam store page and Steamworks settings as code — localized by your AI assistant.**

An [MCP](https://modelcontextprotocol.io) server for game developers who are tired of re-entering the same store
descriptions, translations, capsules, achievements and Steam Cloud settings for every game. Works with **Claude**
(Desktop, Code), **ChatGPT** (developer mode connectors) and any other MCP client.

> Not affiliated with or endorsed by Valve. "Steam" and "Steamworks" are trademarks of Valve Corporation.

> **Rewrite in progress.** The server is being rebuilt in Python as a full release assistant (release gates, gap
> reports, interviews, generators, validators, export packages, optional Steamworks automation). The TypeScript
> v0.1 described below now lives in [`legacy/ts/`](legacy/ts/) and still works: run every `npm`/`npx`/`node` command
> below from that folder. It will be removed once the Python server can do everything it does.

## What it does

| Problem | Tool(s) |
|---|---|
| Store text + achievements written once, in one file | `steamworks.yaml` (`project_init`, `project_read`) |
| Translating everything into 10+ languages, keeping it in sync | `localization_status` → `localization_pending` → *the model translates* → `localization_set` |
| Capsules in 11 different sizes | `assets_generate` — one key art + one logo → every capsule, library and icon image at the exact size |
| Achievement icons + locked variants | `achievement_icons_prepare` |
| "Did I upload enough screenshots / right size?" | `screenshots_check`, `project_validate` |
| Knowing what to enter where in Steamworks | `export_bundle` → `STEAMWORKS_CHECKLIST.md`, per-language JSON, achievement localization CSV |
| Is Steam in sync with my file? | `steam_achievements_diff` (Web API) |
| Builds, branches, leaderboards | `steam_app_builds`, `steam_leaderboards`, `steam_leaderboard_create` (Web API) |
| **Store description + About in every language → Steamworks** | `steamworks_store_text_sync` |
| **Achievements (create/update, all languages, icons) → Steamworks** | `steamworks_achievements_sync` |
| **Steam Cloud quotas, Auto-Cloud paths, root overrides → Steamworks** | `steamworks_cloud_sync` |
| **Install folder + launch options (localized) → Steamworks** | `steamworks_installation_sync` |
| Anything else on a Steamworks page | `steamworks_open` / `inspect` / `fill` / `upload` / `click` (generic browser tools) |

### Translations are done by your assistant, not by a paid API

The server doesn't call any translation service. `localization_pending` hands the model the texts that still need
translating, with context, length hints and format rules. The model translates them and `localization_set` saves them.
The server checks every translation: Steam BBCode tags must match the source, unknown keys are rejected, and changing
the source text marks the old translations as **stale** so they get redone.

```
my-game/
├── steamworks.yaml            ← source text + settings (you edit this)
├── localization/
│   ├── turkish.yaml           ← translations (the model writes these)
│   ├── german.yaml
│   └── .lock.json             ← which source text each translation was made from
├── store/art/keyart.png, logo.png
├── store/screenshots/*.png    ← shot1_japanese.png = Japanese variant of shot1.png
├── achievements/*.png
└── steamworks-out/            ← generated: assets/, achievements/, store/<lang>.json, checklist, CSV
```

See [`examples/demo-game/steamworks.yaml`](examples/demo-game/steamworks.yaml) for a complete example.

### Steamworks has no API for store text, achievements or cloud settings

Valve's partner Web API can read the achievement schema and manage builds and leaderboards, but it **cannot** edit the
store page, create achievements, or change Steam Cloud and installation settings. Those exist only in the Steamworks
web UI. So this server drives that UI for you:

- It opens a real, visible **Chrome or Edge** window with its own profile (not your everyday browser profile).
  **You log in yourself** (password + Steam Guard); the server never sees your credentials and only checks that the
  login cookie exists.
- The `steamworks_*_sync` tools don't type into fields one by one. They use what the Steamworks page itself uses:
  the store page's **Localization import/export**, and the page's own requests for achievements, Steam Cloud and
  launch options. That makes them fast (seconds, not minutes) and resistant to UI redesigns.
- Every sync tool works the same way: **`dryRun: true` (default) shows a diff** → you approve in chat → it applies
  with `userConfirmed: true` → it **reads Steamworks back and verifies** the result.
- Nothing is ever **published**. Changes land as unpublished drafts; you review and press Publish in Steamworks.
  Achievements or cloud paths that exist only in Steam are reported, not deleted.

Verified against the live Steamworks site in October 2026. If Valve changes something, `steamworks_inspect` and the
generic `steamworks_fill` / `steamworks_click` tools still let the assistant work through the page manually.

## Install

Requires Node.js 20+.

```bash
git clone https://github.com/wazzapsenk/steamworks-mcp.git
cd steamworks-mcp
npm install
npm run build
```

The browser tools use your installed Google Chrome or Microsoft Edge. If you have neither, run
`npx playwright install chromium`.

### Environment

| Variable | Purpose |
|---|---|
| `STEAMWORKS_MCP_ROOT` | Folder that contains your game projects. Tools refuse paths outside it. |
| `STEAMWORKS_PUBLISHER_KEY` | Steamworks **publisher** Web API key (Users & Permissions → Manage Groups → Web API key). Optional; only `steam_*` tools need it. |
| `STEAMWORKS_MCP_TOKEN` | Required token for HTTP mode. |
| `STEAMWORKS_MCP_BROWSER_PROFILE` | Where the Steamworks login is stored (default `~/.steamworks-mcp/browser-profile`). |
| `STEAMWORKS_MCP_BROWSER` | `auto` (default: Chrome → Edge → Playwright Chromium), `chrome`, `msedge` or `chromium`. |

The publisher key is a secret: keep it in your MCP client's `env` block or a local `.env` file, and never commit it.

## Connect it

### Claude Code

```bash
claude mcp add steamworks -e STEAMWORKS_MCP_ROOT=D:/Games -e STEAMWORKS_PUBLISHER_KEY=xxxx -- node /path/to/steamworks-mcp/dist/index.js
```

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "steamworks": {
      "command": "node",
      "args": ["/path/to/steamworks-mcp/dist/index.js"],
      "env": {
        "STEAMWORKS_MCP_ROOT": "D:/Games",
        "STEAMWORKS_PUBLISHER_KEY": "xxxx"
      }
    }
  }
}
```

### ChatGPT (developer mode)

ChatGPT connects to **remote** MCP servers over HTTPS, so run the HTTP transport and expose it through a tunnel:

```bash
STEAMWORKS_MCP_TOKEN=$(openssl rand -hex 24) STEAMWORKS_MCP_ROOT=D:/Games node dist/index.js --http --port 8787
cloudflared tunnel --url http://localhost:8787
```

The tunnel's public hostname has to be allowed explicitly (DNS-rebinding protection), e.g.
`STEAMWORKS_MCP_ALLOWED_HOSTS=your-tunnel.trycloudflare.com,localhost`.

In ChatGPT: **Settings → Apps & Connectors → Advanced → Developer mode**, then create a connector with the URL
`https://<your-tunnel>/mcp?token=<STEAMWORKS_MCP_TOKEN>`. Clients that can send headers should use
`Authorization: Bearer <token>` instead of the query string.

⚠️ Anyone who has that URL and token can read and write files under `STEAMWORKS_MCP_ROOT` and drive your logged-in
Steamworks browser. Use a long random token, a narrow root folder, and stop the tunnel when you're done.

## A typical session

> **You:** Set up the Steam page for my game in `D:/Games/SkyRaid`. Translate everything into Turkish, German, Japanese
> and Simplified Chinese.

1. `project_init` → you fill in `steamworks.yaml` (or ask the assistant to draft it from your design doc).
2. `project_validate` → fix what it reports.
3. For each language: `localization_pending` → translate → `localization_set`, until `localization_status` is all green.
4. `assets_generate`, `achievement_icons_prepare`, `screenshots_check`.
5. `export_bundle` → open `steamworks-out/STEAMWORKS_CHECKLIST.md`.
6. `steamworks_open` → log in in the window that opens. Then let the assistant run, one by one,
   `steamworks_store_text_sync`, `steamworks_achievements_sync`, `steamworks_cloud_sync` and
   `steamworks_installation_sync`: each shows you a diff first and applies only after your OK.
7. Upload capsules/screenshots from `steamworks-out/assets/` (or let the assistant use `steamworks_upload`), review
   everything in Steamworks, and **Publish** yourself.

## Development

```bash
npm run dev          # stdio, from source
npm run dev:http     # HTTP, from source
npm test             # unit + in-memory MCP end-to-end tests
node scripts/smoke-http.mjs   # after npm run build: HTTP transport + auth smoke test
```

### Live checks

`scripts/live/` contains helpers for testing against a real Steamworks account (use an unreleased app):

```bash
npx tsx scripts/live/login.ts                       # open the browser, wait for you to log in, list your apps
npx tsx scripts/live/inspect.ts <appId>             # read-only: dump every form on the main Steamworks pages
npx tsx scripts/live/mcp-call.ts <tool> '<json>'    # call any tool exactly as an MCP client would
npx tsx scripts/live/webapi.ts <appId>              # check the publisher Web API key (reads .env)
```

## Roadmap

- Graphical assets upload (capsules, library art, screenshots) through a dedicated sync tool.
- Stats definitions, and achievements bound to progress stats.
- Store page tags, supported languages table and system requirements sync.
- Localized capsules and screenshots per language.

## License

MIT
