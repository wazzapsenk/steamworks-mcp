# steamworks-mcp v0.1 (TypeScript, legacy)

The first version of this server, kept until the Python rewrite (repository root) has been checked on a real demo or
playtest app. New work happens in Python; this folder only gets fixes needed for the fixture recorder.

It is still runnable on its own (Node.js 20+):

```bash
cd legacy/ts
npm install
npm run build
npm test
node dist/index.js            # stdio
node dist/index.js --http     # Streamable HTTP; needs STEAMWORKS_MCP_TOKEN
```

v0.1 tools: `project_init`, `project_read`, `project_validate`, `localization_status` / `_pending` / `_set`,
`assets_generate`, `achievement_icons_prepare`, `screenshots_check`, `export_bundle`, the publisher Web API tools
(`steam_*`), the four Steamworks sync tools (`steamworks_store_text_sync`, `_achievements_sync`, `_cloud_sync`,
`_installation_sync`) and generic browser tools. Their Python counterparts are listed in the root README.

## Recording Steamworks fixtures

`scripts/live/record.ts` records the Steamworks traffic that the Python tests replay, and `scripts/live/sanitize.ts`
turns raw recordings into the committed fixtures. See
[`tests/fixtures/steamworks/README.md`](../../tests/fixtures/steamworks/README.md). Use a test app or a playtest,
never a released game; the recorder never publishes, prepares or reverts anything.
