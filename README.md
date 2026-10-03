# steamworks-mcp

**A release assistant for Steam games: from "nothing configured" to "released", with your AI assistant doing the
legwork and you approving every step.**

An [MCP](https://modelcontextprotocol.io) server for game developers. It knows Valve's release requirements, finds
what your project already has, asks you for the rest, drafts store text, achievements and settings, checks them
against Valve's rules, and either hands you correctly named files with a checklist or applies them to Steamworks
after you approved them. Works with **Claude** (Code, Desktop, claude.ai), **ChatGPT**, **Codex** and any other MCP
client.

> Not affiliated with or endorsed by Valve. "Steam" and "Steamworks" are trademarks of Valve Corporation.

> **Status: 0.2 in development.** This is the Python rewrite. The TypeScript v0.1 still lives in
> [`legacy/ts/`](legacy/ts/) until the new execution layer has been checked on a real demo or playtest app; it also
> holds the recorder for the Steamworks test fixtures.

## What it does

| Step | Tools |
|---|---|
| Start tracking a game; read what the Unity project already says (name, platforms, input, saves, achievements and stats in code, SteamPipe settings) | `init_project`, `scan_project` |
| See what is missing before each release gate (0 prerequisites, 1 store page, 2 build review, 3 release), with Valve's source for every rule | `gap_report` |
| Answer short batches of questions (shown as a form when your client supports it) | `start_interview`, `set_field`, `approve_fields` |
| Draft store text, achievements, Steam Cloud, depots, system requirements | `generate`, `save_draft`, `preview_store` |
| Check everything against Valve's rules, a store-text rubric and an anti-copy check | `validate` |
| Translate with your assistant (no paid translation API), keep translations in sync | `localization_status`, `localization_pending`, `localization_set` |
| Cut every capsule, library image and icon from one key art and one logo | `prepare_images` |
| Learn from successful games without copying them (derived measurements only) | `fetch_reference` |
| Get every file plus a checklist that says which Steamworks page and field it goes to | `export_package` |
| Apply approved values to Steam, look at what Steam has | `apply`, `steamworks_inspect`, `set_build_live`, `restore_snapshot` |
| Confirm manual steps | `mark_applied` |

Text is never invented by the server: `generate` returns a brief, your assistant writes, and the server validates and
stores the result as a draft until you approve it. Every value in `steamworks.yaml` has a status in
`.steam-mcp/state.json`: missing, draft, needs_review, approved, applied.

Resources: `steam://capabilities`, `steam://gates/{n}`, `steam://style-guide/{genre}`, `steam://references/{appid}`,
`steam://manifest/{project}` (and `get_spec_info` returns the same for clients that only use tools). Prompts:
`release_assistant`, `write_store_page`, `design_achievements`, `review_gate`.

## How things get done in Steamworks

Valve's partner Web API covers builds, branches, leaderboards and reading the achievement schema. It cannot edit
the store page, achievements, Steam Cloud or installation settings: those exist only in the Steamworks website.
So every area has a mode:

| Mode | Meaning |
|---|---|
| **API** | Official tooling: the partner Web API (publisher key) and steamcmd / SteamPipe. |
| **BROWSER** | Opt-in. Drives the Steamworks site in a browser window you log into yourself, using the requests the site's own pages make. Undocumented; read [BROWSER mode](#browser-mode-read-this-first). |
| **ARTIFACT** | The tool makes the file (images, localization JSON, VDF scripts, CSV) and tells you where to upload it. |
| **MANUAL** | A checklist item with the exact page and field; you confirm it with `mark_applied`. |

[`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) lists every area with its mode and how well it is verified.
[`docs/STEAMWORKS_INTERNALS.md`](docs/STEAMWORKS_INTERNALS.md) documents the recorded Steamworks behaviour the
BROWSER mode relies on.

## Install

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) (or pip).

```bash
git clone https://github.com/wazzapsenk/steamworks-mcp.git
cd steamworks-mcp
uv sync
```

For the BROWSER mode, add the optional extra. It uses your installed Google Chrome or Microsoft Edge:

```bash
uv sync --extra browser
```

With pip instead: `pip install -e .` or `pip install -e ".[browser]"`.

## Configure

Copy [`.env.example`](.env.example) to `.env` in the steamworks-mcp folder and fill in what you need. Every value
is optional; the real environment wins over `.env`. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `STEAMWORKS_MCP_ROOT` | Folder that holds your game projects. Tools refuse paths outside it. |
| `STEAMWORKS_PUBLISHER_KEY` | Publisher Web API key (API mode: builds, branches, leaderboards). |
| `STEAM_WEB_API_KEY` | A regular Steam Web API key; adds hidden-achievement data for reference games. |
| `STEAMCMD_PATH`, `STEAMCMD_USERNAME` | steamcmd and the builder account for build uploads. |
| `STEAM_MCP_BROWSER` | `1` turns the BROWSER mode on. |
| `STEAMWORKS_MCP_TOKEN` | Bearer token (24+ characters) for the HTTP transport. |
| `STEAMWORKS_MCP_ALLOWED_HOSTS`, `STEAMWORKS_MCP_ALLOWED_ORIGINS` | Extra Host / Origin values accepted over HTTP. |
| `STEAMWORKS_MCP_PUBLIC_URL`, `STEAMWORKS_MCP_OAUTH_REDIRECTS` | The server's public https address (turns on OAuth sign-in) and extra allowed OAuth redirects. |
| `STEAM_MCP_BROWSER_REMOTE` | `1` also allows the BROWSER mode over HTTP (not recommended). |
| `STEAMWORKS_MCP_CACHE`, `STEAMWORKS_MCP_HOME` | Reference cache and per-user data (default `~/.steamworks-mcp`). |

### App IDs

Create the apps in Steamworks yourself (the Steam Direct fee is paid there). Put the app ids into
`steamworks.yaml`, or pass `appid` to `init_project`. A demo and a playtest are separate apps with their own ids:

```yaml
apps:
  main:     { appid: 1234560 }
  demo:     { appid: 1234570 }
  playtest: { appid: 1234580 }
```

### Publisher Web API key

Steamworks → **Users & Permissions → Manage Groups** → your group → create a Web API key. The key acts with the
group's permissions on the group's apps. Keep it in `.env` (or your MCP client's `env` block) and never commit it;
the server never shows it in results, errors or logs.

### steamcmd builder account

Valve recommends uploading builds with a separate Steam account:

1. Create a new Steam account for builds and invite it under **Users & Permissions → Manage Users**.
2. Give it only what uploads need: **Edit App Metadata** and **Publish App Changes To Steam**, for the apps it uploads.
3. [Install steamcmd](https://developer.valvesoftware.com/wiki/SteamCMD) and log in once yourself, in a terminal:
   `steamcmd +login <builder account>`. You type the password and the Steam Guard code; steamcmd keeps the session.
4. Set `STEAMCMD_PATH` (full path to steamcmd) and `STEAMCMD_USERNAME` (the account name only, never the password).

`apply(section="build")` then uploads with `steamcmd +login <name> +run_app_build <script> +quit`. If steamcmd asks
for a login again, the tool stops and asks you to log in once more.

### A restricted Steamworks user for the BROWSER mode

Don't let automation act as your administrator account. Create a separate Steam account, invite it, and give it only
**Edit App Metadata** (Steam Cloud, installation, achievements) and **Edit App Marketing Data** (store text). It does
not need publish, pricing or financial permissions, because this tool never publishes. `gap_report` reminds you
of this in gate 0.

## Connect it

### Claude Code

```bash
claude mcp add steamworks -e STEAMWORKS_MCP_ROOT=/path/to/your/games -- uv --directory /path/to/steamworks-mcp run steamworks-mcp
```

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "steamworks": {
      "command": "uv",
      "args": ["--directory", "/path/to/steamworks-mcp", "run", "steamworks-mcp"],
      "env": { "STEAMWORKS_MCP_ROOT": "/path/to/your/games" }
    }
  }
}
```

`uv --directory` runs the server in the steamworks-mcp folder, so it reads the `.env` there; keys don't have to be
in the client's configuration. `--env-file <path>` points to another file.

### Remote clients: ChatGPT, Codex, claude.ai

Remote clients use the Streamable HTTP transport, reachable over HTTPS (for example through a tunnel):

```bash
STEAMWORKS_MCP_TOKEN=$(openssl rand -hex 24) \
STEAMWORKS_MCP_PUBLIC_URL=https://your-tunnel.example.com \
uv run steamworks-mcp --http --port 8787
cloudflared tunnel --url http://localhost:8787
```

The server binds to `127.0.0.1`, checks Host and Origin headers (the public URL's host is allowed automatically;
others with `STEAMWORKS_MCP_ALLOWED_HOSTS` / `STEAMWORKS_MCP_ALLOWED_ORIGINS`) and accepts two kinds of sign-in, like
any standard remote MCP server:

- **OAuth 2.1** (ChatGPT, claude.ai, `codex mcp login`). With `STEAMWORKS_MCP_PUBLIC_URL` set, the server publishes
  the MCP authorization metadata, lets clients register, and runs the authorization-code flow with PKCE. When a client
  connects for the first time, a page of this server opens: it says which app asks for access and where the result
  goes, and you approve by typing `STEAMWORKS_MCP_TOKEN` there. Codes are only sent to ChatGPT, Claude and loopback
  addresses (more with `STEAMWORKS_MCP_OAUTH_REDIRECTS`). Refresh tokens are kept hashed in
  `~/.steamworks-mcp/oauth.json`; delete that file to sign every client out.
- **Bearer token** for clients that send a header: `Authorization: Bearer <STEAMWORKS_MCP_TOKEN>`. Tokens in the URL
  are never accepted (they end up in logs).

**ChatGPT:** Settings → Apps & Connectors → Advanced → Developer mode, then create a connector with the URL
`https://your-tunnel.example.com/mcp` and OAuth authentication.

**Codex** (`~/.codex/config.toml`), with the token in an environment variable:

```toml
[mcp_servers.steamworks]
url = "https://your-tunnel.example.com/mcp"
bearer_token_env_var = "STEAMWORKS_MCP_TOKEN"
```

Codex can also run the server locally over stdio: `command = "uv"`,
`args = ["--directory", "/path/to/steamworks-mcp", "run", "steamworks-mcp"]`.

Anyone who has the token can read and write the game projects under `STEAMWORKS_MCP_ROOT`. Use a long random token,
a narrow root folder, and stop the tunnel when you're done. The BROWSER mode stays off over HTTP unless you also set
`STEAM_MCP_BROWSER_REMOTE=1`.

## A typical session

> **You:** Help me get my game in `PillowFort/` onto Steam. Coming Soon page first.

1. `init_project` creates `steamworks.yaml` and scans the Unity project. Found values are drafts with evidence.
2. `gap_report` shows what gate 1 still needs; `start_interview` asks for the rest, three questions at a time.
3. `generate("store_short")`: the assistant writes three variants, the server checks them, you pick one.
   The same for the long description (outline first), achievements, Steam Cloud and depots.
4. `validate`, then `localization_pending` / `localization_set` for every language, then `prepare_images`.
5. `export_package(1)` writes `.steam-mcp/exports/gate_1/` with the files and a `CHECKLIST.md`.
6. With the BROWSER mode: `steamworks_open`, then `apply(..., dry_run=true)` per section, and the write only after
   you agreed. Without it, follow the checklist.
7. You review the unpublished changes in Steamworks and press **Publish** yourself.

See [`examples/example-game/steamworks.yaml`](examples/example-game/steamworks.yaml) for a complete manifest.

## Writing to Steam: the protocol

Every write, through the API or the BROWSER mode, goes the same way:

1. **Read first.** Steam's current state is read and saved as a snapshot (`.steam-mcp/snapshots/`). If a Steamworks
   page no longer looks like it did when this tool was verified, it stops before writing anything.
2. **Dry run.** `dry_run=true` is the default and only shows the differences.
3. **Your OK.** Writing needs `user_confirmed=true`, which the assistant may only send after you saw the changes.
4. **Approved values only.** Drafts and values you haven't approved are refused. Rows that exist only in Steam are
   kept unless you explicitly ask for `remove_extra`.
5. **Read back.** After writing, Steam is read again; only what Steam really has becomes `applied`.
6. **Audit.** Every write is logged in `.steam-mcp/audit.jsonl`.

When you first try the BROWSER mode, use a **demo or playtest app**, not your main game. The first write on every app
has to be `restore_snapshot` of the snapshot just taken: it writes every row back unchanged and reads it again,
proving the tool reads and writes that app correctly. Until that worked, `apply` refuses to write. Then go from the
least visible area to the most visible one: Steam Cloud, a hidden test achievement, installation, store text last.
`restore_snapshot` also undoes an apply.

`scripts/live/validate.py <appid>` runs exactly that on a test app of yours and restores everything afterwards: a
quick way to check that Steamworks still behaves as this tool expects. Steamworks keeps an "uncommitted" revision
for every section it saved, even when nothing changed; `steamworks_inspect(what="pending")` lists only the sections
with real changes.

## BROWSER mode: read this first

The BROWSER mode (`STEAM_MCP_BROWSER=1`, `[browser]` extra) is optional. Without it, everything still works through
export packages and checklists.

- **It is undocumented.** It uses the requests the Steamworks pages themselves make (recorded and documented in
  [`docs/STEAMWORKS_INTERNALS.md`](docs/STEAMWORKS_INTERNALS.md)). Valve can change them at any time; the tool then
  stops instead of guessing. Valve does not document or endorse this kind of automation; using it is your decision.
- **It acts as the account you log in with.** Use the restricted user described above, not an administrator.
- **You log in yourself.** `steamworks_open` opens a visible Chrome or Edge window with its own profile
  (`~/.steamworks-mcp/browser-profile`), separate from your everyday browser. You type the password and the Steam
  Guard code. The tool only checks that a login cookie exists; it never reads cookie values.
- **One-time consent.** The first `steamworks_open` shows these risks; you accept once, and the answer is stored with
  its version in `~/.steamworks-mcp/consent.json`.
- **Publishing is blocked in code.** Every request the tool makes is checked before it is sent, and the browser
  window itself blocks the Publish page and every publish, prepare-for-publishing and revert request, even if someone
  clicks them there. The tool's writes go only to the handful of endpoints it uses. Your changes stay unpublished
  drafts that you review in the Publish tab of your everyday browser (where "Revert Changes" undoes them).

## What this tool never does

- Publish, prepare to publish, revert, submit for review or release anything. You do that in Steamworks.
- Set a build live on the default branch. That stays a manual step in App Admin; `set_build_live` only handles beta
  branches, after your OK.
- Delete anything in Steam unless you explicitly ask for it (`remove_extra`).
- Type, store or ask for passwords or Steam Guard codes; read cookie values.
- Write into your game project. The scanner only reads; `init_project` suggests `.gitignore` lines instead of editing
  the file.
- Generate artwork. Images are cropped from your own art; missing art is reported.
- Send your texts to a translation or AI service. Your own assistant translates; the server only checks the result.

## Files

```
my-game/
├── steamworks.yaml          ← every value (comments are kept when tools edit it)
├── localization/<lang>.yaml ← translations, plus .lock.json (which source text each was made from)
└── .steam-mcp/
    ├── state.json           ← per-field status (commit it)
    ├── drafts/              ← text drafts (commit them)
    ├── exports/gate_<n>/    ← files to upload and CHECKLIST.md
    ├── snapshots/           ← what Steam had before each write
    └── audit.jsonl          ← every write
```

`.steam-mcp/.gitignore` keeps exports, snapshots, scan output and the audit log out of git.

## Development

```bash
uv sync --extra browser
uv run pytest                     # unit, replay and in-memory MCP tests; no network, no Steam account
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
uv run python scripts/gen_docs.py --check   # docs/CAPABILITIES.md and docs/schema/ are generated
```

The BROWSER-mode tests replay real, sanitized Steamworks traffic from
[`tests/fixtures/steamworks/`](tests/fixtures/steamworks/), which also explains how to re-record it. A test checks
that no fixture or tracked file contains secrets or private terms.

Reference data: [`docs/SCHEMA.md`](docs/SCHEMA.md) (the manifest), `src/steamworks_mcp/data/` (gates, store rules,
asset specs, events, style guides).

## License

MIT
