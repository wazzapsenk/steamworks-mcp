# Recorded Steamworks traffic

Real request/response pairs from the Steamworks partner site, recorded once on an unreleased test app. They let
the offline tests check the `BROWSER` mode without a Steam account. What they show is written up in
[`docs/STEAMWORKS_INTERNALS.md`](../../../docs/STEAMWORKS_INTERNALS.md).

| File | Content |
|---|---|
| `<area>/<step>.har` | HAR 1.2. Only page documents and XHR/fetch calls to Steam hosts; static assets dropped. |
| `<area>/<step>.json` | The JSON exchanges of the step (method, url, form fields, status, parsed response). |
| `uploads/` | Files uploaded through page forms. The browser does not expose their bytes to the recorder. |

Areas: `store`, `achievements`, `cloud`, `installation` (each `read` → `write` → `readback`), `errors`
(invalid values and a session without cookies), `visibility` (Publish page diff before/after), `cleanup` (deleting
and restoring, which is itself a flow the tool needs), and `api` (Web API; key-based samples not recorded yet).

A few response bodies are missing because the page itself navigated away right after the request (for example the
launch-option delete reloads the page). Those entries say so in `response.content.comment`.

## Placeholders

| Placeholder | Stands for |
|---|---|
| `1000000` | the recorded app id; `1000001+` other app ids of the account |
| `2000000` | the store item id of that app |
| `900000` | the partner (publisher) id |
| `76561190000000001` / `100000001` | the SteamID64 / account id of the person who recorded |
| `ffff…NNNN` (same length) | any hex id of 24+ chars: session id, keys, CDN image hashes |
| `REDACTED` | cookie, token and key values |
| `ExampleGame` / `Redacted` | app names / other local terms from `SANITIZE_DENYLIST` |
| `[turkish text removed]` | Turkish sample text, replaced in the first recordings (no longer done: sample text may be in any language) |

Cookie, `Set-Cookie` and `Authorization` headers are removed. Large store editor pages keep their body only in
`store/read.har`.

## Re-recording

```bash
uv sync --extra browser
uv run python scripts/live/record.py <appId> list      # steps
uv run python scripts/live/record.py <appId> sprint    # everything, cleanup last
uv run python scripts/live/sanitize.py <appId>         # raw recordings -> this folder
uv run pytest tests/test_fixture_sanitization.py
```

Use a test app or a playtest, never a released game. Raw recordings stay in `.steamworks-mcp/recordings/` (ignored
by git). The recorder sends everything through the BROWSER mode's own code and guard, so it never publishes,
prepares or reverts anything, and it only deletes rows it created itself. Tests that check recorded values (for
example the quota in `cloud/read`) need updating after a re-recording on another app.

The recordings here were made with the first, TypeScript recorder; `sanitize.py` reproduces them byte for byte
from the same raw files (apart from the Turkish sample text, which it no longer replaces).
