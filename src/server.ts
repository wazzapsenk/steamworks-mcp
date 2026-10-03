import fs from "node:fs/promises";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { fillFields, inspectPage } from "./browser/forms.js";
import { assertEditable, assertNavigable, closeBrowser, getPage, isLoggedIn, wasRedirectedToSignIn } from "./browser/session.js";
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
import { fetchAchievementLocalization, fetchStoreLocalization, storeItemId, uploadStoreLocalization } from "./browser/steamworks.js";
import { buildStoreLocalization, diffStoreLocalization, normalizeStoreText } from "./export/storeLocalization.js";
import path from "node:path";
import { createAchievement, openAchievements, saveAchievement, uploadAchievementIcon } from "./browser/achievements.js";
import { desiredAchievements, planAchievements } from "./sync/achievements.js";
import { deleteOverride, deleteRoot, readCloud, setOverride, setRoot, setUfs } from "./browser/cloud.js";
import { planCloud, planIsEmpty } from "./sync/cloud.js";
import { readInstallation, setInstallFolder, setLaunchOption } from "./browser/installation.js";
import { installationPlanIsEmpty, planInstallation } from "./sync/installation.js";

const VERSION = "0.1.0";

const INSTRUCTIONS = `steamworks-mcp keeps a Steam game's store page and Steamworks settings in a project folder:
  steamworks.yaml (source text + settings), localization/<language>.yaml (translations), steamworks-out/ (generated files).

Typical flow:
1. project_init (or project_read on an existing project) and project_validate.
2. Localization: localization_status → localization_pending(language) → translate the returned texts yourself → localization_set.
   Keep Steam BBCode tags identical to the source; respect maxLength. Repeat per language until nothing is pending.
3. assets_generate, achievement_icons_prepare, screenshots_check.
4. export_bundle writes per-language store text, an achievement localization CSV and STEAMWORKS_CHECKLIST.md.
5. Steamworks has no API for store text, achievements, cloud or launch options. After steamworks_open (the user logs in
   in the browser window themselves), use the sync tools: steamworks_store_text_sync, steamworks_achievements_sync,
   steamworks_cloud_sync, steamworks_installation_sync. Always run them with dryRun first, show the user the plan, and
   only then call again with dryRun=false and userConfirmed=true. They save drafts; never publish for the user.
   For anything else: steamworks_inspect → steamworks_fill (dryRun first) → steamworks_upload → ask before steamworks_click.
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
      const page = await getPage(config.browserProfileDir, config.browser);
      await page.goto(target, { waitUntil: "domcontentloaded" });
      const loggedIn = (await isLoggedIn(page)) && !wasRedirectedToSignIn(page);
      return ok({
        url: page.url(),
        title: await page.title(),
        loggedIn,
        ...(loggedIn ? {} : { next: "Ask the user to click \"Sign in\" and log in to Steamworks in the opened browser window themselves, then call steamworks_open again. If they are logged in but still see the landing page, their account may lack access to this app." }),
      });
    }),
  );

  server.registerTool(
    "steamworks_store_text_sync",
    {
      title: "Sync store description texts",
      description:
        "Compares the short description and About This Game in every language (steamworks.yaml + localization/*.yaml) with what " +
        "Steamworks has, using the store page's official Download/Upload Localization. dryRun=true (default) only returns the diff. " +
        "With dryRun=false and userConfirmed=true it uploads the JSON, re-downloads and verifies. Saves the store page draft; never publishes.",
      inputSchema: {
        projectDir,
        languages: z.array(z.string()).optional().describe("Defaults to source + all target languages"),
        dryRun: z.boolean().default(true),
        userConfirmed: z.boolean().default(false).describe("true only after the user approved the shown diff"),
      },
      annotations: { openWorldHint: true },
    },
    wrap(async ({ projectDir, languages, dryRun, userConfirmed }) => {
      const { p, m } = await load(projectDir);
      if (!m.appId) throw new Error("steamworks.yaml has no appId.");
      const page = await getPage(config.browserProfileDir, config.browser);
      const itemId = await storeItemId(page, m.appId);
      const current = await fetchStoreLocalization(page, itemId);
      const next = await buildStoreLocalization(m, p, itemId, languages);
      const changes = diffStoreLocalization(current, next);
      const preview = changes.map((c) => ({ ...c, before: c.before.slice(0, 160), after: c.after.slice(0, 160) }));
      if (dryRun || changes.length === 0) {
        return ok({ itemId, changes: preview, ...(changes.length ? { next: "Show the user these changes; on approval call again with dryRun=false, userConfirmed=true." } : { inSync: true }) });
      }
      if (!userConfirmed) throw new Error("Show the diff to the user and get their OK, then call again with userConfirmed: true.");
      // Upload only the languages/fields that change.
      const upload = { itemid: itemId, languages: {} as Record<string, Record<string, string>> };
      for (const c of changes) (upload.languages[c.language] ??= {})[c.field] = c.after;
      const file = path.join(p.outputDir, "store", `storepage_${itemId}_upload.json`);
      await fs.mkdir(path.dirname(file), { recursive: true });
      await fs.writeFile(file, JSON.stringify(upload, null, 2), "utf8");
      const message = await uploadStoreLocalization(page, m.appId, file);
      const after = await fetchStoreLocalization(page, itemId);
      const failed = changes.filter((c) => {
        const f = after.languages[c.language];
        return normalizeStoreText((Array.isArray(f) || !f ? {} : f)[c.field] ?? "") !== normalizeStoreText(c.after);
      });
      return ok({
        itemId,
        steamMessage: message,
        uploaded: changes.length,
        verified: changes.length - failed.length,
        failed: failed.map((c) => `${c.language} ${c.field}`),
        note: "Saved as a store page draft. Publishing is a separate step in Steamworks.",
      });
    }),
  );

  server.registerTool(
    "steamworks_achievement_loc_download",
    {
      title: "Download achievement localization",
      description: "Downloads the app's achievement localization (KeyValues/VDF, all languages) from Steamworks into steamworks-out/ and returns it.",
      inputSchema: { projectDir },
      annotations: { readOnlyHint: true, openWorldHint: true },
    },
    wrap(async ({ projectDir }) => {
      const { p, m } = await load(projectDir);
      if (!m.appId) throw new Error("steamworks.yaml has no appId.");
      const page = await getPage(config.browserProfileDir, config.browser);
      await page.goto(steamworksUrls(m.appId).achievementLocalization, { waitUntil: "domcontentloaded" });
      if (!(await isLoggedIn(page)) || wasRedirectedToSignIn(page)) throw new Error("Not logged in to Steamworks; call steamworks_open first.");
      const vdf = await fetchAchievementLocalization(page, m.appId);
      const file = path.join(p.outputDir, `achievements_loc_${m.appId}_steam.vdf`);
      await fs.mkdir(p.outputDir, { recursive: true });
      await fs.writeFile(file, vdf, "utf8");
      return ok({ file, content: vdf.length > 20000 ? `${vdf.slice(0, 20000)}\n… (truncated)` : vdf });
    }),
  );

  server.registerTool(
    "steamworks_achievements_sync",
    {
      title: "Sync achievements",
      description:
        "Creates/updates achievements in Steamworks from steamworks.yaml: API name, name and description in every language " +
        "(source + localization/*.yaml), hidden flag, and unlocked/locked icons (from achievement_icons_prepare). " +
        "dryRun=true (default) returns the plan. Achievements that exist only in Steam are reported, never deleted. " +
        "Changes stay unpublished until the user publishes in Steamworks.",
      inputSchema: {
        projectDir,
        icons: z.enum(["missing", "all", "none"]).default("missing").describe("Upload icons for new achievements and ones without icons (missing), always (all), or never"),
        dryRun: z.boolean().default(true),
        userConfirmed: z.boolean().default(false).describe("true only after the user approved the shown plan"),
      },
      annotations: { openWorldHint: true },
    },
    wrap(async ({ projectDir, icons, dryRun, userConfirmed }) => {
      const { p, m } = await load(projectDir);
      if (!m.appId) throw new Error("steamworks.yaml has no appId.");
      const page = await getPage(config.browserProfileDir, config.browser);
      const state = await openAchievements(page, m.appId);
      const plan = planAchievements(await desiredAchievements(m, p), state.achievements, icons);
      const summary = {
        create: plan.changes.filter((c) => c.action === "create").map((c) => ({ id: c.id, changes: c.changes, icons: c.uploadIcons })),
        update: plan.changes.filter((c) => c.action === "update").map((c) => ({ id: c.id, changes: c.changes })),
        skipped: plan.changes.filter((c) => c.action === "skip").map((c) => ({ id: c.id, reason: c.reason })),
        unchanged: plan.unchanged,
        onlyInSteam: plan.onlyInSteam,
      };
      const work = plan.changes.filter((c) => c.action !== "skip");
      if (dryRun || work.length === 0) {
        return ok({ ...summary, ...(work.length ? { next: "Show the user this plan; on approval call again with dryRun=false, userConfirmed=true." } : { inSync: true }) });
      }
      if (!userConfirmed) throw new Error("Show the plan to the user and get their OK, then call again with userConfirmed: true.");

      let iconFiles = new Map<string, { unlocked?: string; locked?: string }>();
      if (work.some((c) => c.uploadIcons)) {
        const prepared = await prepareAchievementIcons(m, p);
        iconFiles = new Map(prepared.map((r) => [r.id, { unlocked: r.unlocked, locked: r.locked }]));
      }
      const done: string[] = [];
      const errors: { id: string; error: string }[] = [];
      for (const c of work) {
        try {
          const target = c.action === "create" ? await createAchievement(page, m.appId, state) : c.steam!;
          const statId = String(target.stat_id);
          const bitId = String(target.bit_id);
          await saveAchievement(page, m.appId, {
            statId,
            bitId,
            apiName: c.id,
            displayName: c.next.name,
            description: c.next.description,
            hidden: c.next.hidden,
            permission: Number(target.permission ?? 0),
          });
          if (c.uploadIcons) {
            const f = iconFiles.get(c.id);
            if (f?.unlocked) await uploadAchievementIcon(page, m.appId, statId, bitId, f.unlocked, false);
            if (f?.locked) await uploadAchievementIcon(page, m.appId, statId, bitId, f.locked, true);
          }
          done.push(`${c.action} ${c.id}`);
        } catch (err) {
          errors.push({ id: c.id, error: err instanceof Error ? err.message : String(err) });
        }
      }
      // Verify by reading back.
      const after = planAchievements(await desiredAchievements(m, p), (await openAchievements(page, m.appId)).achievements, "missing");
      return ok({
        done,
        errors,
        stillDifferent: after.changes.filter((c) => c.action !== "skip").map((c) => ({ id: c.id, changes: c.changes })),
        note: "Saved in Steamworks but not published. Publish from Steamworks when ready.",
      });
    }),
  );

  server.registerTool(
    "steamworks_cloud_sync",
    {
      title: "Sync Steam Cloud settings",
      description:
        "Applies steamworks.yaml `cloud` to Steamworks: byte/file quotas, shared app id, developers-only and sync-on-suspend flags, " +
        "Auto-Cloud root paths and root overrides (matched by position). dryRun=true (default) returns the plan. " +
        "Rows in Steam beyond the manifest's lists are kept unless removeExtra=true. Changes stay unpublished until the user publishes.",
      inputSchema: {
        projectDir,
        removeExtra: z.boolean().default(false),
        dryRun: z.boolean().default(true),
        userConfirmed: z.boolean().default(false).describe("true only after the user approved the shown plan"),
      },
      annotations: { openWorldHint: true },
    },
    wrap(async ({ projectDir, removeExtra, dryRun, userConfirmed }) => {
      const { m } = await load(projectDir);
      if (!m.appId) throw new Error("steamworks.yaml has no appId.");
      if (!m.cloud) throw new Error("steamworks.yaml has no `cloud` section.");
      const page = await getPage(config.browserProfileDir, config.browser);
      const cur = await readCloud(page, m.appId);
      const plan = planCloud(m, cur, removeExtra);
      if (dryRun || planIsEmpty(plan)) {
        return ok({ current: cur, plan, ...(planIsEmpty(plan) ? { inSync: true } : { next: "Show the user this plan; on approval call again with dryRun=false, userConfirmed=true." }) });
      }
      if (!userConfirmed) throw new Error("Show the plan to the user and get their OK, then call again with userConfirmed: true.");
      const wantsAutoCloud = plan.setRoots.length + plan.setOverrides.length > 0;
      const quotas = plan.ufs?.to ?? cur;
      if (wantsAutoCloud && (!quotas.byteQuota || !quotas.fileQuota)) {
        throw new Error("Auto-Cloud needs byteQuota and fileQuota > 0; set them in steamworks.yaml.");
      }
      if (plan.ufs) await setUfs(page, m.appId, plan.ufs.to);
      for (const o of plan.deleteOverrides) await deleteOverride(page, m.appId, o.index);
      for (const r of plan.deleteRoots) await deleteRoot(page, m.appId, r.index);
      for (const { row } of plan.setRoots) await setRoot(page, m.appId, row);
      for (const { row } of plan.setOverrides) await setOverride(page, m.appId, row);
      const after = planCloud(m, await readCloud(page, m.appId), removeExtra);
      return ok({
        applied: {
          quotas: !!plan.ufs,
          roots: plan.setRoots.length,
          overrides: plan.setOverrides.length,
          deleted: plan.deleteRoots.length + plan.deleteOverrides.length,
        },
        verified: planIsEmpty(after),
        ...(planIsEmpty(after) ? {} : { stillDifferent: after }),
        note: "Saved in Steamworks but not published. Publish from Steamworks when ready.",
      });
    }),
  );

  server.registerTool(
    "steamworks_installation_sync",
    {
      title: "Sync install folder & launch options",
      description:
        "Applies steamworks.yaml `app` to Steamworks Installation → General: install folder and launch options (executable, arguments, " +
        "working dir, type, OS, arch, beta key, DLC requirement, localized description). Launch options are matched by position; " +
        "extra ones in Steam are reported, not deleted. dryRun=true (default) returns the plan. Changes stay unpublished.",
      inputSchema: {
        projectDir,
        dryRun: z.boolean().default(true),
        userConfirmed: z.boolean().default(false).describe("true only after the user approved the shown plan"),
      },
      annotations: { openWorldHint: true },
    },
    wrap(async ({ projectDir, dryRun, userConfirmed }) => {
      const { p, m } = await load(projectDir);
      if (!m.appId) throw new Error("steamworks.yaml has no appId.");
      if (!m.app) throw new Error("steamworks.yaml has no `app` section.");
      const page = await getPage(config.browserProfileDir, config.browser);
      const cur = await readInstallation(page, m.appId);
      const plan = await planInstallation(m, p, cur);
      const view = {
        installFolder: plan.installFolder,
        launchOptions: plan.setLaunchOptions.map((x) => ({ index: x.row.index, changes: x.changes })),
        extraLaunchOptions: plan.extraLaunchOptions.map((o) => `${o.index}: ${o.executable}`),
      };
      if (dryRun || installationPlanIsEmpty(plan)) {
        return ok({ ...view, ...(installationPlanIsEmpty(plan) ? { inSync: true } : { next: "Show the user this plan; on approval call again with dryRun=false, userConfirmed=true." }) });
      }
      if (!userConfirmed) throw new Error("Show the plan to the user and get their OK, then call again with userConfirmed: true.");
      if (plan.installFolder) await setInstallFolder(page, m.appId, plan.installFolder.to);
      for (const { row } of plan.setLaunchOptions) await setLaunchOption(page, m.appId, row);
      const after = await planInstallation(m, p, await readInstallation(page, m.appId));
      return ok({
        applied: { installFolder: !!plan.installFolder, launchOptions: plan.setLaunchOptions.length },
        verified: installationPlanIsEmpty(after),
        ...(installationPlanIsEmpty(after) ? {} : { stillDifferent: after.setLaunchOptions.map((x) => ({ index: x.row.index, changes: x.changes })) }),
        note: "Saved in Steamworks but not published. Publish from Steamworks when ready.",
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
      const page = await getPage(config.browserProfileDir, config.browser);
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
      const page = await getPage(config.browserProfileDir, config.browser);
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
      const page = await getPage(config.browserProfileDir, config.browser);
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
      const page = await getPage(config.browserProfileDir, config.browser);
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
      const page = await getPage(config.browserProfileDir, config.browser);
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
