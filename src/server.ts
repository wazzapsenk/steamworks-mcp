import fs from "node:fs/promises";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { fillFields, inspectPage } from "./browser/forms.js";
import { assertEditable, assertNavigable, closeBrowser, getPage, isLoggedIn } from "./browser/session.js";
import type { Config } from "./config.js";
import { exportBundle } from "./export/bundle.js";
import { localizationStatus, pendingTranslations, setTranslations } from "./localization/store.js";
import { checkScreenshots, generateStoreAssets, prepareAchievementIcons } from "./media/images.js";
import { readManifest } from "./project/manifest.js";
import { projectPaths } from "./project/paths.js";
import { manifestTemplate } from "./project/template.js";
import { validateProject } from "./project/validate.js";
import { ASSET_SPECS, SCREENSHOT_MIN } from "./steam/assets.js";
import { STEAM_LANGUAGES } from "./steam/languages.js";
import { steamworksUrls } from "./steam/urls.js";
import { SteamWebApi } from "./steam/webapi.js";
import { diffAchievements } from "./steam/diff.js";

const VERSION = "0.1.0";

const INSTRUCTIONS = `steamworks-mcp keeps a Steam game's store page and Steamworks settings in a project folder:
  steamworks.yaml (source text + settings), localization/<language>.yaml (translations), steamworks-out/ (generated files).

Typical flow:
1. project_init (or project_read on an existing project) and project_validate.
2. Localization: localization_status → localization_pending(language) → translate the returned texts yourself → localization_set.
   Keep Steam BBCode tags identical to the source; respect maxLength. Repeat per language until nothing is pending.
3. assets_generate, achievement_icons_prepare, screenshots_check.
4. export_bundle writes per-language store text, an achievement localization CSV and STEAMWORKS_CHECKLIST.md.
5. Steamworks has no API for store text, achievements or cloud settings. To apply them, use the browser tools:
   steamworks_open → (user logs in themselves) → steamworks_inspect → steamworks_fill (dryRun first, show the user the diff) →
   steamworks_upload for images → ask the user before steamworks_click on Save/Publish.
Never type passwords or Steam Guard codes; the user logs in in the browser window.`;

const projectDir = z.string().describe("Project folder containing steamworks.yaml, relative to the server's workspace root (or absolute inside it).");

function ok(data: unknown): CallToolResult {
  return { content: [{ type: "text", text: typeof data === "string" ? data : JSON.stringify(data, null, 2) }] };
}

function fail(err: unknown): CallToolResult {
  return { isError: true, content: [{ type: "text", text: err instanceof Error ? err.message : String(err) }] };
}

function wrap<A>(fn: (args: A) => Promise<CallToolResult>) {
  return async (args: A): Promise<CallToolResult> => {
    try {
      return await fn(args);
    } catch (err) {
      return fail(err);
    }
  };
}

export function createServer(config: Config): McpServer {
  const server = new McpServer({ name: "steamworks-mcp", version: VERSION }, { instructions: INSTRUCTIONS });
  const paths = (dir: string) => projectPaths(config.workspaceRoot, dir);
  const load = async (dir: string) => {
    const p = paths(dir);
    return { p, m: await readManifest(p) };
  };
  const api = () => {
    if (!config.publisherKey) throw new Error("STEAMWORKS_PUBLISHER_KEY is not set in the server's environment.");
    return new SteamWebApi(config.publisherKey);
  };
  const appIdOf = async (dir: string | undefined, appId: number | undefined) => {
    if (appId) return appId;
    if (dir) {
      const { m } = await load(dir);
      if (m.appId) return m.appId;
    }
    throw new Error("Pass appId, or a projectDir whose steamworks.yaml has appId.");
  };

  // ---------- Project ----------

  server.registerTool(
    "project_init",
    {
      title: "Create steamworks.yaml",
      description: "Creates a commented steamworks.yaml template in a game's project folder. Fill it in, then run project_validate.",
      inputSchema: { projectDir, name: z.string().describe("Game name"), appId: z.number().int().positive().optional(), overwrite: z.boolean().default(false) },
      annotations: { destructiveHint: false },
    },
    wrap(async ({ projectDir, name, appId, overwrite }) => {
      const p = paths(projectDir);
      await fs.mkdir(p.dir, { recursive: true });
      const exists = await fs.access(p.manifest).then(() => true, () => false);
      if (exists && !overwrite) throw new Error(`${p.manifest} already exists. Pass overwrite: true to replace it.`);
      await fs.writeFile(p.manifest, manifestTemplate(name, appId), "utf8");
      return ok({ created: p.manifest, next: "Edit the file (texts, achievements, cloud, art paths), then run project_validate." });
    }),
  );

  server.registerTool(
    "project_read",
    {
      title: "Read project",
      description: "Returns the parsed steamworks.yaml (with defaults applied) and the Steamworks page URLs for the app.",
      inputSchema: { projectDir },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ projectDir }) => {
      const { p, m } = await load(projectDir);
      return ok({ dir: p.dir, manifest: m, steamworksPages: m.appId ? steamworksUrls(m.appId) : undefined });
    }),
  );

  server.registerTool(
    "project_validate",
    {
      title: "Validate project",
      description: "Checks steamworks.yaml, art, screenshots, achievement icons, cloud settings and translation coverage against Steam's rules.",
      inputSchema: { projectDir },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ projectDir }) => {
      const { p, m } = await load(projectDir);
      const findings = await validateProject(m, p);
      return ok({
        ready: !findings.some((f) => f.level === "error"),
        errors: findings.filter((f) => f.level === "error"),
        warnings: findings.filter((f) => f.level === "warning"),
      });
    }),
  );

  server.registerTool(
    "steam_languages",
    {
      title: "Steam languages",
      description: "Lists Steam's supported languages with their API codes (used everywhere in this server) and web codes.",
      inputSchema: {},
      annotations: { readOnlyHint: true },
    },
    wrap(async () => ok(STEAM_LANGUAGES)),
  );

  // ---------- Localization ----------

  server.registerTool(
    "localization_status",
    {
      title: "Localization status",
      description: "Per target language: how many store/achievement texts are translated, missing, or stale (source changed since translation).",
      inputSchema: { projectDir, languages: z.array(z.string()).optional().describe("Defaults to targetLanguages") },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ projectDir, languages }) => {
      const { p, m } = await load(projectDir);
      const status = await localizationStatus(m, p, languages ?? m.targetLanguages);
      return ok(status.map((s) => ({ ...s, missing: s.missing.length, stale: s.stale.length, orphaned: s.orphaned })));
    }),
  );

  server.registerTool(
    "localization_pending",
    {
      title: "Texts to translate",
      description:
        "Returns texts that still need translating into one language, with context, format (plain or Steam BBCode) and length hints. " +
        "Translate them and save with localization_set. For stale entries the previous translation is included so you can update it.",
      inputSchema: { projectDir, language: z.string().describe("Steam API language code, e.g. turkish"), limit: z.number().int().min(1).max(200).default(40) },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ projectDir, language, limit }) => {
      const { p, m } = await load(projectDir);
      const pending = await pendingTranslations(m, p, language, limit);
      return ok({ language, count: pending.length, sourceLanguage: m.sourceLanguage, entries: pending });
    }),
  );

  server.registerTool(
    "localization_set",
    {
      title: "Save translations",
      description: "Saves translations for one language into localization/<language>.yaml. Rejects unknown keys, empty texts and BBCode tag mismatches.",
      inputSchema: {
        projectDir,
        language: z.string(),
        translations: z.array(z.object({ key: z.string(), text: z.string() })).min(1),
      },
    },
    wrap(async ({ projectDir, language, translations }) => {
      const { p, m } = await load(projectDir);
      return ok(await setTranslations(m, p, language, translations));
    }),
  );

  // ---------- Media ----------

  server.registerTool(
    "assets_specs",
    {
      title: "Steam asset specs",
      description: "Lists Steam store/library image sizes and the screenshot rules this server checks against.",
      inputSchema: {},
      annotations: { readOnlyHint: true },
    },
    wrap(async () => ok({ assets: ASSET_SPECS, screenshots: SCREENSHOT_MIN })),
  );

  server.registerTool(
    "assets_generate",
    {
      title: "Generate capsules & library art",
      description:
        "Renders every Steam capsule, library image and icon at the exact required size from store.art.keyArt + store.art.logo " +
        "(or from hand-made overrides) into steamworks-out/assets/.",
      inputSchema: { projectDir, only: z.array(z.string()).optional().describe("Asset ids to render; default all (see assets_specs)") },
    },
    wrap(async ({ projectDir, only }) => {
      const { p, m } = await load(projectDir);
      return ok(await generateStoreAssets(m, p, only));
    }),
  );

  server.registerTool(
    "achievement_icons_prepare",
    {
      title: "Prepare achievement icons",
      description: "Converts each achievement's icon to a square JPG and creates the locked (greyscale) version when none is given. Output: steamworks-out/achievements/.",
      inputSchema: { projectDir, size: z.number().int().min(64).max(1024).default(256) },
    },
    wrap(async ({ projectDir, size }) => {
      const { p, m } = await load(projectDir);
      return ok(await prepareAchievementIcons(m, p, size));
    }),
  );

  server.registerTool(
    "screenshots_check",
    {
      title: "Check screenshots",
      description: "Checks screenshot count, resolution and aspect ratio. Files ending in _<language> (e.g. shot1_japanese.png) are treated as localized variants.",
      inputSchema: { projectDir },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ projectDir }) => {
      const { p, m } = await load(projectDir);
      return ok(await checkScreenshots(m, p));
    }),
  );

  // ---------- Export ----------

  server.registerTool(
    "export_bundle",
    {
      title: "Export for Steamworks",
      description:
        "Writes steamworks-out/store/<language>.json, achievements_localization.csv and STEAMWORKS_CHECKLIST.md — " +
        "every value to enter in Steamworks, page by page, with links.",
      inputSchema: { projectDir },
    },
    wrap(async ({ projectDir }) => {
      const { p, m } = await load(projectDir);
      return ok(await exportBundle(m, p));
    }),
  );

  // ---------- Steamworks Web API ----------

  server.registerTool(
    "steam_achievements_diff",
    {
      title: "Compare achievements with Steam",
      description:
        "Reads the app's achievement schema from the Steamworks Web API (publisher key) and compares it with steamworks.yaml: " +
        "missing in Steam, only in Steam, and differing names/descriptions/hidden flags.",
      inputSchema: { projectDir, language: z.string().optional().describe("Compare against a translation instead of the source language") },
      annotations: { readOnlyHint: true, openWorldHint: true },
    },
    wrap(async ({ projectDir, language }) => {
      const { p, m } = await load(projectDir);
      if (!m.appId) throw new Error("steamworks.yaml has no appId.");
      const schema = await api().getSchemaForGame(m.appId, language ?? m.sourceLanguage);
      return ok(await diffAchievements(m, p, schema, language ?? m.sourceLanguage));
    }),
  );

  server.registerTool(
    "steam_app_builds",
    {
      title: "List builds and branches",
      description: "Lists recent builds and beta branches of the app (Steamworks Web API, read-only).",
      inputSchema: { projectDir: projectDir.optional(), appId: z.number().int().positive().optional(), count: z.number().int().min(1).max(50).default(10) },
      annotations: { readOnlyHint: true, openWorldHint: true },
    },
    wrap(async ({ projectDir, appId, count }) => {
      const id = await appIdOf(projectDir, appId);
      const [builds, betas] = await Promise.all([api().getAppBuilds(id, count), api().getAppBetas(id)]);
      return ok({ builds, betas });
    }),
  );

  server.registerTool(
    "steam_leaderboards",
    {
      title: "List leaderboards",
      description: "Lists the app's leaderboards (Steamworks Web API).",
      inputSchema: { projectDir: projectDir.optional(), appId: z.number().int().positive().optional() },
      annotations: { readOnlyHint: true, openWorldHint: true },
    },
    wrap(async ({ projectDir, appId }) => ok(await api().getLeaderboardsForGame(await appIdOf(projectDir, appId)))),
  );

  server.registerTool(
    "steam_leaderboard_create",
    {
      title: "Create leaderboard",
      description: "Finds or creates a leaderboard (Steamworks Web API). Confirm the name and sort order with the user first.",
      inputSchema: {
        projectDir: projectDir.optional(),
        appId: z.number().int().positive().optional(),
        name: z.string().min(1),
        sortMethod: z.enum(["Ascending", "Descending"]),
        displayType: z.enum(["Numeric", "Seconds", "MilliSeconds"]),
        onlyTrustedWrites: z.boolean().default(false),
        onlyFriendsReads: z.boolean().default(false),
      },
      annotations: { openWorldHint: true },
    },
    wrap(async (a) =>
      ok(
        await api().findOrCreateLeaderboard({
          appid: await appIdOf(a.projectDir, a.appId),
          name: a.name,
          sortmethod: a.sortMethod,
          displaytype: a.displayType,
          onlytrustedwrites: a.onlyTrustedWrites,
          onlyfriendsreads: a.onlyFriendsReads,
        }),
      ),
    ),
  );

  // ---------- Steamworks browser ----------

  const pageNames = ["landing", "storePage", "achievements", "achievementLocalization", "cloud", "installation"] as const;

  server.registerTool(
    "steamworks_open",
    {
      title: "Open Steamworks page",
      description:
        "Opens a visible browser window on a Steamworks page (persistent profile). If the user isn't logged in, ask them to log in " +
        "in that window themselves — never type credentials — then call this again.",
      inputSchema: {
        page: z.enum(pageNames).optional().describe("Known page for the project's app"),
        url: z.string().url().optional().describe("Any partner.steamgames.com URL"),
        projectDir: projectDir.optional(),
        appId: z.number().int().positive().optional(),
      },
      annotations: { openWorldHint: true },
    },
    wrap(async ({ page: name, url, projectDir, appId }) => {
      let target = url;
      if (!target) {
        const id = await appIdOf(projectDir, appId).catch(() => undefined);
        target = id ? steamworksUrls(id)[name ?? "landing"] : "https://partner.steamgames.com/";
      }
      assertNavigable(target);
      const page = await getPage(config.browserProfileDir);
      await page.goto(target, { waitUntil: "domcontentloaded" });
      const loggedIn = await isLoggedIn(page);
      return ok({
        url: page.url(),
        title: await page.title(),
        loggedIn,
        ...(loggedIn ? {} : { next: "Ask the user to log in to Steamworks in the opened browser window, then call steamworks_open again." }),
      });
    }),
  );

  server.registerTool(
    "steamworks_inspect",
    {
      title: "Inspect Steamworks page",
      description: "Lists the form controls (with selector, label, current value) and buttons on the current Steamworks page.",
      inputSchema: { includeHidden: z.boolean().default(false), maxValueLength: z.number().int().min(20).max(5000).default(200) },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ includeHidden, maxValueLength }) => {
      const page = await getPage(config.browserProfileDir);
      const info = await inspectPage(page, maxValueLength);
      return ok({
        ...info,
        controls: includeHidden ? info.controls : info.controls.filter((c) => c.visible),
        buttons: includeHidden ? info.buttons : info.buttons.filter((b) => b.visible),
      });
    }),
  );

  server.registerTool(
    "steamworks_fill",
    {
      title: "Fill Steamworks form",
      description:
        "Fills inputs/textareas/selects/checkboxes on the current Steamworks page by selector (from steamworks_inspect). " +
        "Default dryRun=true returns a before/after diff without changing anything. Never submits; the form must be saved separately.",
      inputSchema: {
        fields: z.array(z.object({ selector: z.string(), value: z.union([z.string(), z.boolean()]) })).min(1),
        dryRun: z.boolean().default(true),
      },
    },
    wrap(async ({ fields, dryRun }) => {
      const page = await getPage(config.browserProfileDir);
      const changes = await fillFields(page, fields, dryRun);
      return ok({ dryRun, changes, next: dryRun ? "Show the user the diff; call again with dryRun=false once they agree." : "Fields filled. Ask the user before saving." });
    }),
  );

  server.registerTool(
    "steamworks_upload",
    {
      title: "Upload file to Steamworks",
      description: "Attaches a file from the project folder (e.g. steamworks-out/assets/header_capsule.png) to a file input on the current Steamworks page.",
      inputSchema: { projectDir, selector: z.string().describe("Selector of an <input type=file>"), file: z.string().describe("Path relative to the project folder") },
    },
    wrap(async ({ projectDir, selector, file }) => {
      const p = paths(projectDir);
      const abs = p.file(file);
      await fs.access(abs);
      const page = await getPage(config.browserProfileDir);
      assertEditable(page);
      await page.locator(selector).setInputFiles(abs);
      return ok({ uploaded: file, selector });
    }),
  );

  server.registerTool(
    "steamworks_click",
    {
      title: "Click on Steamworks page",
      description:
        "Clicks a button or link on the current Steamworks page (e.g. 'Save', 'New Achievement'). " +
        "Saving, publishing or deleting changes the live app: get the user's explicit OK first and pass it in userConfirmed.",
      inputSchema: {
        selector: z.string(),
        userConfirmed: z.boolean().describe("true only if the user approved this specific click in the chat"),
        waitForNavigation: z.boolean().default(false),
      },
      annotations: { destructiveHint: true },
    },
    wrap(async ({ selector, userConfirmed, waitForNavigation }) => {
      if (!userConfirmed) throw new Error("Ask the user to confirm this click, then call again with userConfirmed: true.");
      const page = await getPage(config.browserProfileDir);
      assertEditable(page);
      const loc = page.locator(selector);
      const count = await loc.count();
      if (count !== 1) throw new Error(count === 0 ? "No element matches this selector." : `${count} elements match; use a more specific selector.`);
      const text = (await loc.innerText().catch(() => "")).trim();
      if (waitForNavigation) await Promise.all([page.waitForLoadState("domcontentloaded"), loc.click()]);
      else await loc.click();
      await page.waitForTimeout(500);
      return ok({ clicked: selector, text, url: page.url() });
    }),
  );

  server.registerTool(
    "steamworks_screenshot",
    {
      title: "Screenshot Steamworks page",
      description: "Returns a screenshot of the visible part of the current Steamworks page.",
      inputSchema: { fullPage: z.boolean().default(false) },
      annotations: { readOnlyHint: true },
    },
    wrap(async ({ fullPage }) => {
      const page = await getPage(config.browserProfileDir);
      const buf = await page.screenshot({ fullPage, type: "jpeg", quality: 70 });
      return { content: [{ type: "image", data: buf.toString("base64"), mimeType: "image/jpeg" }] };
    }),
  );

  server.registerTool(
    "steamworks_close",
    {
      title: "Close browser",
      description: "Closes the Steamworks browser window. The login is kept in the profile for next time.",
      inputSchema: {},
    },
    wrap(async () => {
      await closeBrowser();
      return ok({ closed: true });
    }),
  );

  return server;
}
