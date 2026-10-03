# steamworks-mcp: advanced setup and details

The [README](../README.md) covers the quick install and what the tool does. This page has everything else:
configuration, keys and accounts, connecting each app by hand, remote clients, how writes to Steamworks work, the
BROWSER mode, the files the tool keeps, and development.

**Contents:** [Configure](#configure) · [Connect it by hand](#connect-it-by-hand) ·
[Writing to Steam](#writing-to-steam-the-protocol) · [BROWSER mode](#browser-mode-read-this-first) ·
[Files](#files) · [Development](#development)

## Configure

`steamworks-mcp setup` writes the games folder (and, if you give them, the publisher key and the BROWSER mode) to
`~/.steamworks-mcp/settings.env`, which every app then uses. You can also copy
[`.env.example`](../.env.example) to `.env` in the folder the server starts in. Every value is optional; the real
environment wins over `.env`, which wins over `settings.env`. Never commit `.env`.

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

Needed only for the API mode (builds, beta branches, leaderboards, reading the achievement schema). Creating it takes
an administrator of your Steamworks account:

1. **Users & Permissions → Manage Groups → Create new group**, for example `MCP`. Valve recommends a group of its
   own for each key: the key only reaches the apps of its group.
2. Add only the apps this tool should manage to that group.
3. Open the group and choose **Create WebAPI Key**.
4. Under **Key Permissions** tick **General** only. This tool needs nothing else; leave **Microtransactions**,
   **Economy** and especially **Financial** (sales data) unticked.
5. **Whitelisted IPs** is optional. If your machine has a fixed public IP, adding it blocks the key everywhere else
   (other addresses get `403 Forbidden`); leave it empty otherwise.
6. **Save Changes**, then copy the key from the right-hand side into `.env`:
   `STEAMWORKS_PUBLISHER_KEY=...`

Treat the key like a password: keep it in `.env` (never committed) and out of screenshots and chats. The server never
shows it in results, errors or logs. If it ever leaks, delete it on the same page and create a new one.

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

The one thing Steam makes live without a Publish step is store tags. `apply(section="store_tags")` writes them only
with `goes_live_now=true` on top of the usual confirmation, and never removes community tags. Packages, which
Steam also changes at once, are never edited.

## Connect it by hand

`steamworks-mcp setup` does this for Claude Desktop, Claude Code, Cursor and Codex. By hand:

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

### Cursor

The repository is also a local Cursor plugin (server, skills, and the code rules as Cursor rules in `rules/*.mdc`).
Clone it into Cursor's local plugin folder, reload the window (Developer: Reload Window), and set the games folder
once:

```bash
git clone https://github.com/wazzapsenk/steamworks-mcp ~/.cursor/plugins/local/steamworks
uvx --from git+https://github.com/wazzapsenk/steamworks-mcp steamworks-mcp setup --only-settings
```

Or only the server, in `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

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

### Codex

Codex (CLI, IDE extension or app) runs the server locally like Claude does. In `~/.codex/config.toml`:

```toml
[mcp_servers.steamworks]
command = "uv"
args = ["--directory", "/path/to/steamworks-mcp", "run", "steamworks-mcp"]
env = { STEAMWORKS_MCP_ROOT = "/path/to/your/games" }
default_tools_approval_mode = "writes"
tool_timeout_sec = 900  # build uploads and Steamworks writes can take minutes
```

`"writes"` lets Codex call the read-only tools (gap report, validate, inspect…) without asking and asks before every
tool that changes something. The tools say which they are (MCP tool annotations). `codex exec` runs without
prompts, so there it needs `"approve"`.

### Remote clients: ChatGPT, claude.ai

**The easy way:** `steamworks-mcp remote` ([README](../README.md#chatgpt-and-claudeai)). It keeps an access code
in `~/.steamworks-mcp/settings.env` (made once), opens a Cloudflare quick tunnel to the local server, starts the HTTP
server with OAuth sign-in on that address and prints what to paste into ChatGPT and claude.ai. Ctrl+C stops both.
The BROWSER mode stays off over the internet unless `STEAM_MCP_BROWSER_REMOTE=1` is set.

**A fixed address:** a quick tunnel gets a new address on every start. For one that stays, point a
[named Cloudflare tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (or any
https reverse proxy) at `http://127.0.0.1:8787` and start the server with
`steamworks-mcp remote --url https://steam.example.com`: it then opens no tunnel of its own.

**By hand:** remote clients use the Streamable HTTP transport, reachable over HTTPS (for example through a tunnel):

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

**ChatGPT:** add a server with the URL `https://your-tunnel.example.com/mcp` and OAuth authentication. On the web,
that is Settings → Apps & Connectors → Advanced → Developer mode, then create a connector. In the desktop app, it is
Settings → Plugins → MCPs → Add. Then approve on the page that opens by typing `STEAMWORKS_MCP_TOKEN`.

ChatGPT connects from OpenAI's servers, so it cannot reach `localhost`: it needs the public address above. Local
clients (Claude Code and Desktop, Codex) don't.

A remote Codex setup works too, with the token in an environment variable:

```toml
[mcp_servers.steamworks]
url = "https://your-tunnel.example.com/mcp"
bearer_token_env_var = "STEAMWORKS_MCP_TOKEN"
```

Anyone who has the token can read and write the game projects under `STEAMWORKS_MCP_ROOT`. Use a long random token,
a narrow root folder, and stop the tunnel when you're done. The BROWSER mode stays off over HTTP unless you also set
`STEAM_MCP_BROWSER_REMOTE=1`.

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
least visible area to the most visible one: Steam Cloud, a hidden test achievement, installation, depots, the store
page, store text last. `restore_snapshot` also undoes an apply, except for images (the tool only fills empty image
slots and cannot remove one) and store tags.

`scripts/live/validate.py <appid>` runs exactly that on a test app of yours and restores everything afterwards: a
quick way to check that Steamworks still behaves as this tool expects. Steamworks keeps an "uncommitted" revision
for every section it saved, even when nothing changed; `steamworks_inspect(what="pending")` lists only the sections
with real changes.

## BROWSER mode: read this first

The BROWSER mode (`STEAM_MCP_BROWSER=1`, `[browser]` extra) is optional. Without it, everything still works through
export packages and checklists.

- **It is undocumented.** It uses the requests the Steamworks pages themselves make (recorded and documented in
  [`docs/STEAMWORKS_INTERNALS.md`](STEAMWORKS_INTERNALS.md)). Valve can change them at any time; the tool then
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
  drafts that you review in the Publish tab of your everyday browser (where "Revert Changes" undoes them). The one
  exception is store tags, which Steam applies at once: they need a separate `goes_live_now=true` from you.

## Files

```
my-game/
├── steamworks.yaml          ← every value (comments are kept when tools edit it)
├── localization/<lang>.yaml ← translations, plus .lock.json (which source text each was made from)
└── .steam-mcp/
    ├── state.json           ← per-field status (commit it)
    ├── drafts/              ← text drafts (commit them)
    ├── market/              ← the market study: labels and numbers, no page text
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
uv run python scripts/gen_skills.py --check # six skills come from the server's prompts
uv run python scripts/gen_rules.py --check  # rules/*.mdc come from data/code_rules.yaml
```

The BROWSER-mode tests replay real, sanitized Steamworks traffic from
[`tests/fixtures/steamworks/`](../tests/fixtures/steamworks/). `scripts/live/record.py` re-records it and
`scripts/live/sanitize.py` turns the raw recordings into fixtures; a test checks that no fixture or tracked file
contains secrets or private terms. `scripts/live/validate.py` checks a live app against the whole write protocol.

Reference data: [`docs/SCHEMA.md`](SCHEMA.md) (the manifest), `src/steamworks_mcp/data/` (gates, store rules,
asset specs, events, style guides, store patterns). `scripts/build_store_patterns.py` refreshes
`data/store_patterns.json` from Steam's current Popular New Releases (public store data, one request per second, at
most 80 games); like `scripts/build_references.py` it commits derived numbers only, never text from the pages.
