// Records real Steamworks partner-site and Web API traffic for ONE test app, step by step, as HAR files.
//
// Raw recordings contain cookies, session ids and real ids. They stay in .steamworks-mcp/recordings/ (gitignored)
// and only reach the repo through scripts/live/sanitize.ts.
//
// Usage (the app id is always passed explicitly; nothing app-specific is stored in this file):
//   npx tsx scripts/live/record.ts <appId> list
//   npx tsx scripts/live/record.ts <appId> <step> [<step> ...]
//   npx tsx scripts/live/record.ts <appId> sprint            every step in order, cleanup last
//
// Safety rules enforced here:
//   - Nothing is ever published: any non-GET request whose URL mentions "publish" is aborted, and every other
//     non-GET request to partner.steamgames.com must hit an endpoint on the allowlist below.
//   - Writes only touch rows this script created (marker "SWMCP_REC" / "SWMCPRec" / "-swmcp-rec").
//   - Cleanup restores the baseline captured by the read steps; it never deletes rows without a marker,
//     except achievements whose API name starts with TEST_STEAMWORKS_MCP (left over from earlier tool tests).
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { chromium, type Page } from "playwright";
import {
  createAchievement,
  openAchievements,
  saveAchievement,
  uploadAchievementIcon,
  type SteamAchievement,
} from "../../src/browser/achievements.js";
import {
  deleteOverride,
  deleteRoot,
  readCloud,
  setOverride,
  setRoot,
  setUfs,
  type CloudState,
} from "../../src/browser/cloud.js";
import { readInstallation, setInstallFolder, setLaunchOption, type InstallationState } from "../../src/browser/installation.js";
import { closeBrowser, ensureNameShim, getPage } from "../../src/browser/session.js";
import { storeItemId, uploadStoreLocalization } from "../../src/browser/steamworks.js";
import { loadConfig } from "../../src/config.js";
import { fetchEntry, HarRecorder } from "./har.js";

try {
  process.loadEnvFile();
} catch {}

const BASE = "https://partner.steamgames.com";
const WEBAPI = "https://partner.steam-api.com";
const REC = path.resolve(".steamworks-mcp/recordings");
const RAW = path.join(REC, "raw");
const BASELINE = path.join(REC, "baseline");
const TMP = path.join(REC, "tmp");
const STATE_FILE = path.join(REC, "run-state.json");
const LOG_FILE = path.join(REC, "log.txt");

const TEST_ACH = "SWMCP_REC_A";
const TEST_LEADERBOARD = "SWMCP_REC_TEST";
const LEGACY_ACH_PREFIX = "TEST_STEAMWORKS_MCP";

/** Non-GET partner endpoints this script may call. Everything else is aborted. */
const POST_ALLOWLIST = [
  /^\/apps\/(newachievement|saveachievement|deleteachievement|uploadachievementloc|setufsparameters|setautocloudpath|setautocloudoverride|setappinstallfolder|setlaunchoption)\b/,
  /^\/images\/uploadachievement\b/,
  // Store page Localization tab: "Upload translations" (the form posts here, not to the store save action).
  /^\/admin\/game\/uploadloc\/\d+$/,
  // "View Diffs" on the Publish page: read-only list of unpublished changes.
  /^\/apps\/diff\/\d+$/,
];
/** Never allowed, whatever the allowlist says: publishing, preparing to publish, reverting unpublished work. */
const POST_DENYLIST = /publish|\/apps\/(prepare|revert)\//i;

interface Ctx {
  appId: number;
  page: Page;
  rec: HarRecorder;
  key?: string;
  state: RunState;
}

interface RunState {
  storeItemId?: string;
  notes: string[];
}

type Step = { id: string; describe: string; run: (c: Ctx) => Promise<string> };

// ---------------------------------------------------------------- helpers

async function goto(page: Page, url: string): Promise<void> {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  await ensureNameShim(page);
}

async function pageGet(page: Page, url: string): Promise<{ status: number; text: string }> {
  return page.evaluate(
    async (u) => {
      const r = await fetch(u, { credentials: "same-origin", headers: { Accept: "application/json, text/plain, */*" } });
      return { status: r.status, text: await r.text() };
    },
    url,
  );
}

async function pagePost(page: Page, url: string, params: Record<string, string>, withSession = true): Promise<{ status: number; text: string }> {
  return page.evaluate(
    async ({ u, p, s }) => {
      const body = new URLSearchParams(p);
      if (s) body.set("sessionid", (window as any).g_sessionID ?? "");
      const r = await fetch(u, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
      return { status: r.status, text: await r.text() };
    },
    { u: url, p: params, s: withSession },
  );
}

const short = (s: string, n = 160) => s.replace(/\s+/g, " ").slice(0, n);

async function readJson<T>(file: string): Promise<T | undefined> {
  try {
    return JSON.parse(await fs.readFile(file, "utf8")) as T;
  } catch {
    return undefined;
  }
}

async function saveBaseline(name: string, data: unknown): Promise<boolean> {
  const file = path.join(BASELINE, name);
  if (await readJson(file)) return false; // never overwrite: the first read is the state to restore
  await fs.mkdir(BASELINE, { recursive: true });
  await fs.writeFile(file, typeof data === "string" ? JSON.stringify({ text: data }) : JSON.stringify(data, null, 2));
  return true;
}

async function baseline<T>(name: string): Promise<T> {
  const v = await readJson<T>(path.join(BASELINE, name));
  if (!v) throw new Error(`Baseline ${name} is missing: run the read steps first.`);
  return v;
}

async function storeId(c: Ctx): Promise<string> {
  c.state.storeItemId ??= await storeItemId(c.page, c.appId);
  return c.state.storeItemId;
}

async function downloadStoreLoc(c: Ctx): Promise<any> {
  const id = await storeId(c);
  const r = await pageGet(c.page, `${BASE}/admin/game/downloadloc/${id}?language=all&format=json`);
  if (r.status !== 200) throw new Error(`downloadloc HTTP ${r.status}`);
  return JSON.parse(r.text);
}

async function downloadAchievementLoc(c: Ctx): Promise<string> {
  await goto(c.page, `${BASE}/apps/loc/${c.appId}`);
  const r = await pageGet(c.page, `${BASE}/apps/downloadachievementloc/${c.appId}?download_language=all`);
  if (r.status !== 200) throw new Error(`downloadachievementloc HTTP ${r.status}`);
  return r.text;
}

/** Calls the page's own delete flow (confirm dialog accepted) and waits for its AJAX response. */
async function pageDelete(page: Page, script: string, urlPart: string): Promise<string> {
  page.once("dialog", (d) => void d.accept());
  const [res] = await Promise.all([page.waitForResponse((r) => r.url().includes(urlPart), { timeout: 30_000 }), page.evaluate(script)]);
  return `${res.status()} ${short(await res.text().catch(() => ""), 120)}`;
}

async function webApi(c: Ctx, method: "GET" | "POST", iface: string, params: Record<string, string | number | boolean>): Promise<string> {
  if (!c.key) return `${iface}: skipped (STEAMWORKS_PUBLISHER_KEY not set)`;
  const q = new URLSearchParams({ key: c.key, ...Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])) });
  const url = method === "GET" ? `${WEBAPI}${iface}?${q}` : `${WEBAPI}${iface}`;
  const r = await fetchEntry(method, url, { body: method === "POST" ? q : undefined, redact: [c.key] });
  c.rec.addManual(r.entry);
  return `${iface}: HTTP ${r.status} ${short(r.text, 140)}`;
}

async function publicGet(c: Ctx, url: string): Promise<string> {
  const r = await fetchEntry("GET", url);
  c.rec.addManual(r.entry);
  return `${new URL(url).pathname}: HTTP ${r.status} ${short(r.text, 140)}`;
}

async function pageText(c: Ctx, url: string, file: string): Promise<string> {
  await goto(c.page, url);
  const text = await c.page.locator("body").innerText().catch(() => "");
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, `URL: ${c.page.url()}\n\n${text}`);
  const pending = /unpublished changes/i.test(text);
  return `${new URL(url).pathname} -> ${new URL(c.page.url()).pathname} (unpublished-changes notice: ${pending})`;
}

async function steamcmdAppInfo(c: Ctx, file: string): Promise<string> {
  const exe = process.env.STEAMCMD_PATH?.trim();
  if (!exe) return "steamcmd: skipped (STEAMCMD_PATH not set)";
  const { stdout } = await promisify(execFile)(exe, ["+login", "anonymous", "+app_info_update", "1", "+app_info_print", String(c.appId), "+quit"], {
    timeout: 180_000,
    maxBuffer: 20 * 1024 * 1024,
  }).catch((err) => ({ stdout: String(err?.stdout ?? err) }));
  await fs.writeFile(file, stdout);
  return `steamcmd app_info_print: ${stdout.length} bytes`;
}

const isMarked = (...values: (string | undefined)[]) => values.some((v) => /SWMCP_?REC|-swmcp-rec/i.test(v ?? ""));

// ---------------------------------------------------------------- steps

const steps: Step[] = [
  // ---- visibility (before anything changes)
  {
    id: "visibility/before",
    describe: "Public data + Publish page text before any write (read-only)",
    run: async (c) => visibility(c, "before"),
  },

  // ---- reads (also capture the baseline to restore)
  {
    id: "store/read",
    describe: "Open the store page (redirect to item id) and download the localization JSON",
    run: async (c) => {
      const data = await downloadStoreLoc(c);
      const saved = await saveBaseline("store-loc.json", data);
      return `item ${c.state.storeItemId}, languages=${Object.keys(data.languages ?? {}).length}, baseline ${saved ? "saved" : "kept"}`;
    },
  },
  {
    id: "achievements/read",
    describe: "Open Stats & Achievements and fetch the achievement list",
    run: async (c) => {
      const st = await openAchievements(c.page, c.appId);
      const saved = await saveBaseline("achievements.json", st);
      return `${st.achievements.length} achievements (${st.achievements.map((a) => a.api_name).join(", ")}), max stat/bit ${st.maxStatId}/${st.maxBitId}, baseline ${saved ? "saved" : "kept"}`;
    },
  },
  {
    id: "achievements/loc_read",
    describe: "Download the achievement localization file (KeyValues)",
    run: async (c) => {
      const text = await downloadAchievementLoc(c);
      const saved = await saveBaseline("achievement-loc.json", text);
      return `${text.length} chars, baseline ${saved ? "saved" : "kept"}`;
    },
  },
  {
    id: "cloud/read",
    describe: "Open the Steam Cloud page",
    run: async (c) => {
      const st = await readCloud(c.page, c.appId);
      const saved = await saveBaseline("cloud.json", st);
      return `quota ${st.byteQuota}/${st.fileQuota}, roots ${st.roots.length}, overrides ${st.overrides.length}, baseline ${saved ? "saved" : "kept"}`;
    },
  },
  {
    id: "installation/read",
    describe: "Open Installation > General",
    run: async (c) => {
      const st = await readInstallation(c.page, c.appId);
      const saved = await saveBaseline("installation.json", st);
      return `folder "${st.installFolder}", launch options ${st.launchOptions.map((o) => o.index).join(",")}, baseline ${saved ? "saved" : "kept"}`;
    },
  },
  {
    id: "api/read",
    describe: "Partner Web API reads with the publisher key",
    run: async (c) =>
      [
        await webApi(c, "GET", "/ISteamUserStats/GetSchemaForGame/v2/", { appid: c.appId, l: "english" }),
        await webApi(c, "GET", "/ISteamApps/GetAppBuilds/v1/", { appid: c.appId, count: 5 }),
        await webApi(c, "GET", "/ISteamApps/GetAppBetas/v1/", { appid: c.appId }),
        await webApi(c, "GET", "/ISteamLeaderboards/GetLeaderboardsForGame/v2/", { appid: c.appId }),
      ].join("\n    "),
  },
  {
    id: "api/public_unkeyed",
    describe: "Public endpoints without any key (what anyone can see)",
    run: async (c) =>
      [
        await publicGet(c, `https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/?appid=${c.appId}`),
        await publicGet(c, `https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid=${c.appId}`),
        await publicGet(c, `https://store.steampowered.com/api/appdetails?appids=${c.appId}&l=english`),
      ].join("\n    "),
  },

  // ---- writes (least visible first) + readback
  {
    id: "cloud/write",
    describe: "setufsparameters, setautocloudpath (new row), setautocloudoverride (new row)",
    run: async (c) => {
      const base = await baseline<CloudState>("cloud.json");
      await readCloud(c.page, c.appId);
      const out: string[] = [];
      await setUfs(c.page, c.appId, { ...base, byteQuota: base.byteQuota + 1_048_576, fileQuota: base.fileQuota + 1 });
      out.push("ufs ok");
      await setRoot(c.page, c.appId, { index: base.roots.length, root: "WinAppDataLocalLow", path: "SWMCPRec/Saves", pattern: "*.sav", os: "", recursive: true });
      out.push(`root #${base.roots.length} ok`);
      await setOverride(c.page, c.appId, {
        index: base.overrides.length,
        root: "WinAppDataLocalLow",
        os: "MacOS",
        useInstead: "MacAppSupport",
        addPath: "SWMCPRec",
        replacePath: false,
      });
      out.push(`override #${base.overrides.length} ok`);
      return out.join(", ");
    },
  },
  {
    id: "cloud/readback",
    describe: "Re-open the Steam Cloud page",
    run: async (c) => {
      const st = await readCloud(c.page, c.appId);
      return `quota ${st.byteQuota}/${st.fileQuota}, devOnly ${st.developersOnly}, roots ${JSON.stringify(st.roots.map((r) => r.path))}, overrides ${JSON.stringify(st.overrides.map((o) => o.addPath))}`;
    },
  },
  {
    id: "achievements/write",
    describe: "newachievement + saveachievement (hidden, English+German) + both icons",
    run: async (c) => {
      const st = await openAchievements(c.page, c.appId);
      const existing = st.achievements.find((a) => a.api_name === TEST_ACH);
      const a = existing ?? (await createAchievement(c.page, c.appId, st));
      const saved = await saveAchievement(c.page, c.appId, {
        statId: String(a.stat_id),
        bitId: String(a.bit_id),
        apiName: TEST_ACH,
        displayName: { english: "Recording Test A", german: "Aufnahme-Test A" },
        description: { english: "Fixture recording, safe to delete.", german: "Fixture-Aufnahme, kann gelöscht werden." },
        hidden: true,
        permission: 0,
      });
      const icons = path.resolve(".steamworks-mcp/live");
      await uploadAchievementIcon(c.page, c.appId, String(a.stat_id), String(a.bit_id), path.join(icons, "test_icon.jpg"), false);
      await uploadAchievementIcon(c.page, c.appId, String(a.stat_id), String(a.bit_id), path.join(icons, "test_icon_gray.jpg"), true);
      return `${existing ? "updated" : "created"} ${saved.api_name} (${a.stat_id}/${a.bit_id}), icons uploaded`;
    },
  },
  {
    id: "achievements/readback",
    describe: "fetchachievements again",
    run: async (c) => {
      const st = await openAchievements(c.page, c.appId);
      const a = st.achievements.find((x) => x.api_name === TEST_ACH);
      return a ? `found ${TEST_ACH}: ${short(JSON.stringify(a), 300)}` : `${TEST_ACH} NOT FOUND`;
    },
  },
  {
    id: "achievements/loc_write",
    describe: "Download the achievement loc file, change the German name of the test achievement, upload it",
    run: async (c) => {
      const downloaded = await downloadAchievementLoc(c);
      // The export only seems to contain published data; when it lacks our draft, build the same layout from the
      // achievement list (English + German only), changing nothing but the test achievement's German name.
      const st = await openAchievements(c.page, c.appId);
      const kv = downloaded.includes("Aufnahme-Test A")
        ? downloaded.replace("Aufnahme-Test A", "Aufnahme-Test A (Datei)")
        : buildAchievementKv(st.achievements, ["english", "german"], (a, lang, field, v) =>
            a.api_name === TEST_ACH && lang === "german" && field === "NAME" ? `${v} (Datei)` : v,
          );
      await fs.mkdir(TMP, { recursive: true });
      await fs.writeFile(path.join(TMP, "achievement_loc_upload.vdf"), kv);
      await goto(c.page, `${BASE}/apps/loc/${c.appId}`);
      // Same multipart POST as the page's upload form (which submits through a hidden iframe).
      const r = await c.page.evaluate(
        async ({ appId, text }) => {
          const fd = new FormData();
          fd.set("sessionid", (window as any).g_sessionID);
          fd.set("MAX_FILE_SIZE", "9000000");
          fd.set("appID", String(appId));
          fd.set("kv", new Blob([text], { type: "application/octet-stream" }), "achievement_loc_upload.vdf");
          const res = await fetch("/apps/uploadachievementloc", { method: "POST", body: fd, credentials: "same-origin" });
          return { status: res.status, text: await res.text() };
        },
        { appId: c.appId, text: kv },
      );
      return `export had test achievement: ${downloaded.includes("Aufnahme-Test A")}; uploaded ${kv.length} chars: HTTP ${r.status} ${short(r.text.replace(/<[^>]+>/g, " "), 200)}`;
    },
  },
  {
    id: "achievements/loc_write_single",
    describe: 'Upload the single-language KeyValues layout ("Language" + "Tokens") in German (not enabled for the app)',
    run: async (c) => uploadSingleLanguageKv(c, "german", "Aufnahme-Test A (Datei)", "Fixture-Aufnahme (Datei)."),
  },
  {
    id: "achievements/loc_write_single_english",
    describe: 'Upload the single-language KeyValues layout ("Language" + "Tokens") in English (enabled for the app)',
    run: async (c) => uploadSingleLanguageKv(c, "english", "Recording Test A (file)", "Fixture recording (file)."),
  },
  {
    id: "achievements/loc_write_name_token",
    describe: "Same English upload with the token style new achievements show by default (NEW_ACHIEVEMENT_NAME_<stat>_<bit>)",
    run: async (c) => uploadSingleLanguageKv(c, "english", "Recording Test A (token)", "Fixture recording (token).", (s, b, f) => `NEW_ACHIEVEMENT_${f}_${s}_${b}`),
  },
  {
    id: "achievements/loc_readback",
    describe: "Download the achievement loc file again and re-fetch the achievement list",
    run: async (c) => {
      const text = await downloadAchievementLoc(c);
      const st = await openAchievements(c.page, c.appId);
      const a = st.achievements.find((x) => x.api_name === TEST_ACH);
      const german = typeof a?.display_name === "object" ? a.display_name.german : undefined;
      return `export: ${text.length} chars, has changed name: ${text.includes("(Datei)")} | list German name: ${german}`;
    },
  },
  {
    id: "installation/write",
    describe: "setappinstallfolder (same value) + setlaunchoption (new row)",
    run: async (c) => {
      const base = await baseline<InstallationState>("installation.json");
      await readInstallation(c.page, c.appId);
      const out: string[] = [];
      try {
        await setInstallFolder(c.page, c.appId, base.installFolder);
        out.push(`install folder re-saved as "${base.installFolder}"`);
      } catch (err: any) {
        out.push(`install folder: ${err.message}`);
      }
      const template = base.launchOptions[0];
      const index = Math.max(-1, ...base.launchOptions.map((o) => o.index)) + 1;
      await setLaunchOption(c.page, c.appId, {
        index,
        executable: "SWMCPRec.exe",
        arguments: "-swmcp-rec",
        workingDir: "",
        type: "option1",
        os: "windows",
        arch: "64",
        betaKey: "",
        ownsDlc: "",
        descriptions: { english: "Recording test option", german: "Aufnahme-Testoption" },
        oscpu: template?.oscpu ?? "",
        realm: template?.realm ?? "",
        steamdeck: template?.steamdeck ?? "",
      });
      out.push(`launch option #${index} ok`);
      return out.join(", ");
    },
  },
  {
    id: "installation/readback",
    describe: "Re-open Installation > General",
    run: async (c) => {
      const st = await readInstallation(c.page, c.appId);
      return `folder "${st.installFolder}", options ${JSON.stringify(st.launchOptions.map((o) => [o.index, o.executable, o.descriptions]))}`;
    },
  },
  {
    id: "store/write",
    describe: "Upload a localization JSON that changes only the English short description",
    run: async (c) => {
      const base = await baseline<any>("store-loc.json");
      const en = base.languages?.english ?? {};
      const key = "app[content][short_description]";
      const next = `${String(en[key] ?? "").slice(0, 250)} (recording)`;
      await fs.mkdir(TMP, { recursive: true });
      const file = path.join(TMP, "store_loc_upload.json");
      await fs.writeFile(file, JSON.stringify({ itemid: await storeId(c), languages: { english: { [key]: next } } }, null, 2));
      const msg = await uploadStoreLocalization(c.page, c.appId, file);
      return `uploaded: ${short(msg)}`;
    },
  },
  {
    id: "store/readback",
    describe: "Download the localization JSON again",
    run: async (c) => {
      const data = await downloadStoreLoc(c);
      const en = data.languages?.english ?? {};
      return `english short: ${short(String(en["app[content][short_description]"] ?? ""), 120)} | about kept: ${Boolean(en["app[content][about]"])}`;
    },
  },
  {
    id: "api/leaderboard_write",
    describe: "FindOrCreateLeaderboard (test board) + GetLeaderboardsForGame readback",
    run: async (c) =>
      [
        await webApi(c, "POST", "/ISteamLeaderboards/FindOrCreateLeaderboard/v2/", {
          appid: c.appId,
          name: TEST_LEADERBOARD,
          sortmethod: "Descending",
          displaytype: "Numeric",
          createifnotfound: true,
          onlytrustedwrites: false,
          onlyfriendsreads: false,
        }),
        await webApi(c, "GET", "/ISteamLeaderboards/GetLeaderboardsForGame/v2/", { appid: c.appId }),
      ].join("\n    "),
  },

  // ---- error cases
  {
    id: "errors/cloud_quota_too_big",
    describe: "setufsparameters above the documented limits (10 GB / 10,000 files)",
    run: async (c) => {
      const base = await baseline<CloudState>("cloud.json");
      await goto(c.page, `${BASE}/apps/cloud/${c.appId}`);
      const params = (cb: string, cfiles: string) => ({
        cb,
        cfiles,
        appidRedirect: String(base.sharedAppId),
        hideInClient: base.developersOnly ? "1" : "0",
        syncOnSuspend: base.syncOnSuspend ? "1" : "0",
      });
      const a = await pagePost(c.page, `${BASE}/apps/setufsparameters/${c.appId}`, params("10000000001", String(base.fileQuota || 10)));
      const afterA = await readCloud(c.page, c.appId);
      const b = await pagePost(c.page, `${BASE}/apps/setufsparameters/${c.appId}`, params(String(base.byteQuota || 1_048_576), "10001"));
      const afterB = await readCloud(c.page, c.appId);
      return `bytes>10GB: ${a.status} ${short(a.text, 120)} -> stored ${afterA.byteQuota}/${afterA.fileQuota} | files>10000: ${b.status} ${short(b.text, 120)} -> stored ${afterB.byteQuota}/${afterB.fileQuota}`;
    },
  },
  {
    id: "errors/cloud_invalid_root",
    describe: "setautocloudpath with an unknown root name",
    run: async (c) => {
      const cur = await readCloud(c.page, c.appId);
      const r = await pagePost(c.page, `${BASE}/apps/setautocloudpath/${c.appId}`, {
        index: String(cur.roots.length),
        root: "NotARealRoot",
        path: "SWMCPRec/Invalid",
        pattern: "*.sav",
        oslist: "",
        recursive: "false",
      });
      const after = await readCloud(c.page, c.appId);
      return `${r.status} ${short(r.text, 160)} | rows now ${JSON.stringify(after.roots.map((x) => [x.root, x.path]))}`;
    },
  },
  {
    id: "errors/cloud_missing_pattern",
    describe: "setautocloudpath with an empty pattern",
    run: async (c) => {
      const cur = await readCloud(c.page, c.appId);
      const r = await pagePost(c.page, `${BASE}/apps/setautocloudpath/${c.appId}`, {
        index: String(cur.roots.length),
        root: "WinAppDataLocalLow",
        path: "SWMCPRec/NoPattern",
        pattern: "",
        oslist: "",
        recursive: "false",
      });
      const after = await readCloud(c.page, c.appId);
      return `${r.status} ${short(r.text, 160)} | rows now ${JSON.stringify(after.roots.map((x) => [x.root, x.path, x.pattern]))}`;
    },
  },
  {
    id: "errors/achievement_duplicate_apiname",
    describe: "A second achievement saved with an API name that already exists",
    run: async (c) => {
      const st = await openAchievements(c.page, c.appId);
      if (!st.achievements.some((a) => a.api_name === TEST_ACH)) return `${TEST_ACH} missing; run achievements/write first`;
      const a = await createAchievement(c.page, c.appId, st);
      const r = await pagePost(c.page, `${BASE}/apps/saveachievement/${c.appId}`, {
        statid: String(a.stat_id),
        bitid: String(a.bit_id),
        apiname: TEST_ACH,
        displayname: JSON.stringify("Recording duplicate"),
        description: JSON.stringify("Duplicate API name test."),
        permission: "0",
        hidden: "true",
        progressStat: "-1",
        progressMin: "",
        progressMax: "",
      });
      return `new ${a.api_name} (${a.stat_id}/${a.bit_id}); save as ${TEST_ACH}: ${r.status} ${short(r.text, 200)}`;
    },
  },
  {
    id: "errors/launch_empty_executable",
    describe: "setlaunchoption on a NEW index with an empty executable (an all-empty row means delete)",
    run: async (c) => {
      const cur = await readInstallation(c.page, c.appId);
      const index = Math.max(-1, ...cur.launchOptions.map((o) => o.index)) + 1;
      const r = await pagePost(c.page, `${BASE}/apps/setlaunchoption/${c.appId}`, {
        index: String(index),
        executable: "",
        arguments: "-swmcp-rec-empty",
        workingdir: "",
        type: "option2",
        description: "Empty executable test",
        osversion: "windows",
        osarch: "64",
        oscpu: "",
        betakey: "",
        ownsdlc: "",
        realm: cur.launchOptions[0]?.realm ?? "",
        steamdeck: cur.launchOptions[0]?.steamdeck ?? "",
      });
      const after = await readInstallation(c.page, c.appId);
      return `index ${index}: ${r.status} ${short(r.text, 160)} | rows now ${JSON.stringify(after.launchOptions.map((o) => [o.index, o.executable, o.arguments]))}`;
    },
  },
  {
    id: "errors/session_expired",
    describe: "A cookie-less browser: protected page redirect + AJAX GET/POST without a session",
    run: async (c) => {
      const browser = await chromium.launch({ channel: "chrome", headless: true }).catch(() => chromium.launch({ channel: "msedge", headless: true }));
      try {
        const ctx = await browser.newContext();
        await guard(ctx);
        const page = await ctx.newPage();
        c.rec.attach(page);
        await page.goto(`${BASE}/apps/achievements/${c.appId}`, { waitUntil: "domcontentloaded" });
        await ensureNameShim(page);
        const landed = page.url();
        const get = await pageGet(page, `${BASE}/apps/fetchachievements/${c.appId}`);
        const post = await pagePost(page, `${BASE}/apps/setufsparameters/${c.appId}`, { cb: "0", cfiles: "0", appidRedirect: "0", hideInClient: "0", syncOnSuspend: "0", sessionid: "0" }, false);
        await page.waitForTimeout(500);
        return `landed ${landed.replace(String(c.appId), "<app>")} | GET ${get.status} ${short(get.text, 100)} | POST ${post.status} ${short(post.text, 100)}`;
      } finally {
        await browser.close();
      }
    },
  },

  // ---- visibility while drafts exist
  {
    id: "visibility/after_writes",
    describe: "Public data + Publish page text while the drafts exist (read-only)",
    run: async (c) => visibility(c, "after_writes"),
  },

  // ---- cleanup back to the baseline (this flow is a fixture too)
  {
    id: "cleanup/achievements",
    describe: "Delete achievements created by this script (page's own delete flow)",
    run: async (c) => {
      const base = await baseline<{ achievements: SteamAchievement[] }>("achievements.json");
      const known = new Set(base.achievements.map((a) => `${a.stat_id}/${a.bit_id}`));
      const st = await openAchievements(c.page, c.appId);
      const out: string[] = [];
      for (const a of st.achievements) {
        if (known.has(`${a.stat_id}/${a.bit_id}`)) continue;
        if (!isMarked(a.api_name) && !/^NEW_ACHIEVEMENT_/.test(a.api_name)) {
          out.push(`KEPT unknown ${a.api_name}`);
          continue;
        }
        out.push(`${a.api_name}: ${await deleteAchievement(c, a)}`);
      }
      return out.join(" | ") || "nothing to delete";
    },
  },
  {
    id: "cleanup/cloud",
    describe: "Remove the test rows and restore the quota settings",
    run: async (c) => {
      const base = await baseline<CloudState>("cloud.json");
      const cur = await readCloud(c.page, c.appId);
      const out: string[] = [];
      for (const o of [...cur.overrides].reverse()) {
        if (o.index < base.overrides.length) continue;
        if (!isMarked(o.addPath)) {
          out.push(`KEPT override #${o.index}`);
          continue;
        }
        await deleteOverride(c.page, c.appId, o.index);
        out.push(`override #${o.index} deleted`);
      }
      for (const r of [...cur.roots].reverse()) {
        if (r.index < base.roots.length) continue;
        if (!isMarked(r.path)) {
          out.push(`KEPT root #${r.index}`);
          continue;
        }
        await deleteRoot(c.page, c.appId, r.index);
        out.push(`root #${r.index} deleted`);
      }
      await setUfs(c.page, c.appId, base);
      out.push(`ufs restored to ${base.byteQuota}/${base.fileQuota}`);
      return out.join(", ");
    },
  },
  {
    id: "cleanup/installation",
    describe: "Delete the test launch options (page's own delete flow) and restore the install folder",
    run: async (c) => {
      const base = await baseline<InstallationState>("installation.json");
      const known = new Set(base.launchOptions.map((o) => o.index));
      const cur = await readInstallation(c.page, c.appId);
      const out: string[] = [];
      for (const o of [...cur.launchOptions].reverse()) {
        if (known.has(o.index)) continue;
        if (!isMarked(o.executable, o.arguments)) {
          out.push(`KEPT option #${o.index}`);
          continue;
        }
        await goto(c.page, `${BASE}/apps/config/${c.appId}`);
        out.push(`option #${o.index}: ${await pageDelete(c.page, `DeleteLaunchOption(${c.appId}, ${JSON.stringify(String(o.index))})`, "/apps/setlaunchoption/")}`);
      }
      const now = await readInstallation(c.page, c.appId);
      if (now.installFolder !== base.installFolder) {
        await setInstallFolder(c.page, c.appId, base.installFolder);
        out.push(`install folder restored to "${base.installFolder}"`);
      }
      return out.join(" | ") || "nothing to do";
    },
  },
  {
    id: "cleanup/store",
    describe: "Upload the baseline English texts back (only fields that differ)",
    run: async (c) => {
      const base = await baseline<any>("store-loc.json");
      const cur = await downloadStoreLoc(c);
      const changed: Record<string, Record<string, string>> = {};
      for (const [lang, fields] of Object.entries<any>(base.languages ?? {})) {
        if (Array.isArray(fields)) continue;
        for (const [k, v] of Object.entries<string>(fields)) {
          if ((cur.languages?.[lang] ?? {})[k] !== v) (changed[lang] ??= {})[k] = v;
        }
      }
      if (!Object.keys(changed).length) return "store text already matches the baseline";
      await fs.mkdir(TMP, { recursive: true });
      const file = path.join(TMP, "store_loc_restore.json");
      await fs.writeFile(file, JSON.stringify({ itemid: await storeId(c), languages: changed }, null, 2));
      const msg = await uploadStoreLocalization(c.page, c.appId, file);
      return `restored ${JSON.stringify(Object.fromEntries(Object.entries(changed).map(([l, f]) => [l, Object.keys(f)])))}: ${short(msg)}`;
    },
  },
  {
    id: "api/leaderboard_delete",
    describe: "DeleteLeaderboard (test board) + GetLeaderboardsForGame readback",
    run: async (c) =>
      [
        await webApi(c, "POST", "/ISteamLeaderboards/DeleteLeaderboard/v1/", { appid: c.appId, name: TEST_LEADERBOARD }),
        await webApi(c, "GET", "/ISteamLeaderboards/GetLeaderboardsForGame/v2/", { appid: c.appId }),
      ].join("\n    "),
  },
  {
    id: "cleanup/verify",
    describe: "Read everything again and compare with the baseline",
    run: async (c) => verify(c),
  },
  {
    id: "cleanup/legacy_test_achievements",
    describe: `Delete achievements named ${LEGACY_ACH_PREFIX}* left over from earlier tool tests`,
    run: async (c) => {
      const st = await openAchievements(c.page, c.appId);
      const out: string[] = [];
      for (const a of st.achievements) {
        if (!a.api_name.startsWith(LEGACY_ACH_PREFIX)) continue;
        out.push(`${a.api_name}: ${await deleteAchievement(c, a)}`);
      }
      return out.join(" | ") || "none found";
    },
  },
  {
    id: "visibility/after_cleanup",
    describe: "Public data + Publish page text after cleanup (read-only)",
    run: async (c) => visibility(c, "after_cleanup"),
  },
];

/**
 * KeyValues layout of the achievement localization file, per language:
 *   "lang" { "<language>" { "Tokens" { "NEW_ACHIEVEMENT_<stat>_<bit>_NAME" "…" "…_DESC" "…" } } }
 * (layout taken from Valve's docs; marked unverified until a non-empty export is seen).
 */
function buildAchievementKv(
  list: SteamAchievement[],
  languages: string[],
  edit: (a: SteamAchievement, lang: string, field: "NAME" | "DESC", value: string) => string,
): string {
  const esc = (s: string) => s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const pick = (v: unknown, lang: string) => (typeof v === "string" ? (lang === "english" ? v : "") : String((v as any)?.[lang] ?? ""));
  const out = ['"lang"', "{"];
  for (const lang of languages) {
    out.push(`\t"${lang}"`, "\t{", '\t\t"Tokens"', "\t\t{");
    for (const a of list) {
      for (const [field, value] of [["NAME", pick(a.display_name, lang)], ["DESC", pick(a.description, lang)]] as const) {
        if (!value) continue;
        out.push(`\t\t\t"NEW_ACHIEVEMENT_${a.stat_id}_${a.bit_id}_${field}"\t"${esc(edit(a, lang, field, value))}"`);
      }
    }
    out.push("\t\t}", "\t}");
  }
  out.push("}");
  return out.join("\n") + "\n";
}

async function uploadSingleLanguageKv(
  c: Ctx,
  language: string,
  name: string,
  desc: string,
  token: (stat: string, bit: string, field: "NAME" | "DESC") => string = (s, b, f) => `NEW_ACHIEVEMENT_${s}_${b}_${f}`,
): Promise<string> {
  const st = await openAchievements(c.page, c.appId);
  const a = st.achievements.find((x) => x.api_name === TEST_ACH);
  if (!a) return `${TEST_ACH} missing; run achievements/write first`;
  const [s, b] = [String(a.stat_id), String(a.bit_id)];
  const kv = ['"lang"', "{", `\t"Language"\t"${language}"`, '\t"Tokens"', "\t{", `\t\t"${token(s, b, "NAME")}"\t"${name}"`, `\t\t"${token(s, b, "DESC")}"\t"${desc}"`, "\t}", "}", ""].join("\n");
  await goto(c.page, `${BASE}/apps/loc/${c.appId}`);
  // Same multipart POST as the page's upload form (which submits through a hidden iframe).
  const r = await c.page.evaluate(
    async ({ appId, text, file }) => {
      const fd = new FormData();
      fd.set("sessionid", (window as any).g_sessionID);
      fd.set("MAX_FILE_SIZE", "9000000");
      fd.set("appID", String(appId));
      fd.set("kv", new Blob([text], { type: "application/octet-stream" }), file);
      const res = await fetch("/apps/uploadachievementloc", { method: "POST", body: fd, credentials: "same-origin" });
      return { status: res.status, text: await res.text() };
    },
    { appId: c.appId, text: kv, file: `achievement_loc_${language}.vdf` },
  );
  const after = await openAchievements(c.page, c.appId);
  const now = after.achievements.find((x) => x.api_name === TEST_ACH);
  return `HTTP ${r.status} ${short(r.text, 400)} | name now ${JSON.stringify(now?.display_name)}`;
}

async function deleteAchievement(c: Ctx, a: SteamAchievement): Promise<string> {
  await goto(c.page, `${BASE}/apps/achievements/${c.appId}`);
  await c.page.waitForFunction(() => typeof (window as any).DeleteAchievementClosure === "function", undefined, { timeout: 20_000 });
  return pageDelete(
    c.page,
    `DeleteAchievementClosure(${c.appId}, ${JSON.stringify(String(a.stat_id))}, ${JSON.stringify(String(a.bit_id))}, ${JSON.stringify(a.api_name)})()`,
    "/apps/deleteachievement/",
  );
}

async function visibility(c: Ctx, label: string): Promise<string> {
  const dir = path.join(RAW, "visibility");
  const lines = [
    await pageText(c, `${BASE}/apps/landing/${c.appId}`, path.join(dir, `${label}-landing.txt`)),
    await pageText(c, `${BASE}/apps/history/${c.appId}`, path.join(dir, `${label}-history.txt`)),
    await pageText(c, `${BASE}/apps/publishing/${c.appId}`, path.join(dir, `${label}-publishing.txt`)),
  ];
  // Same request as the Publish page's "View Diffs" button (read-only).
  const diff = await pagePost(c.page, `${BASE}/apps/diff/${c.appId}`, { section: "technical" });
  await fs.writeFile(path.join(dir, `${label}-diff.txt`), diff.text);
  let diffText = diff.text;
  try {
    const j = JSON.parse(diff.text);
    diffText = `${j.opened ?? ""}${j.diff ?? ""}`;
  } catch {}
  lines.push(`/apps/diff: HTTP ${diff.status}, ${diffText.length} chars, mentions test rows: ${/SWMCP/i.test(diffText)}`);
  return [
    ...lines,
    await publicGet(c, `https://store.steampowered.com/api/appdetails?appids=${c.appId}&l=english`),
    await publicGet(c, `https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid=${c.appId}`),
    await webApi(c, "GET", "/ISteamUserStats/GetSchemaForGame/v2/", { appid: c.appId, l: "english" }),
    await steamcmdAppInfo(c, path.join(dir, `${label}-appinfo.txt`)),
  ].join("\n    ");
}

async function verify(c: Ctx): Promise<string> {
  const diffs: string[] = [];
  const ach = await baseline<{ achievements: SteamAchievement[] }>("achievements.json");
  const curAch = await openAchievements(c.page, c.appId);
  const names = (l: SteamAchievement[]) => l.map((a) => a.api_name).sort().join(",");
  if (names(ach.achievements) !== names(curAch.achievements)) diffs.push(`achievements: ${names(ach.achievements)} -> ${names(curAch.achievements)}`);
  const cloud = await baseline<CloudState>("cloud.json");
  const curCloud = await readCloud(c.page, c.appId);
  if (JSON.stringify(cloud) !== JSON.stringify(curCloud)) diffs.push(`cloud: ${short(JSON.stringify(cloud), 300)} -> ${short(JSON.stringify(curCloud), 300)}`);
  const inst = await baseline<InstallationState>("installation.json");
  const curInst = await readInstallation(c.page, c.appId);
  if (JSON.stringify(inst) !== JSON.stringify(curInst)) diffs.push(`installation: ${short(JSON.stringify(inst), 300)} -> ${short(JSON.stringify(curInst), 300)}`);
  const store = await baseline<any>("store-loc.json");
  const curStore = await downloadStoreLoc(c);
  if (JSON.stringify(store.languages) !== JSON.stringify(curStore.languages)) diffs.push("store localization differs");
  const loc = await baseline<{ text: string }>("achievement-loc.json");
  const curLoc = await downloadAchievementLoc(c);
  if (loc.text.trim() !== curLoc.trim()) diffs.push(`achievement loc: ${loc.text.length} -> ${curLoc.length} chars`);
  await fs.writeFile(path.join(REC, "verify.json"), JSON.stringify({ at: new Date().toISOString(), diffs }, null, 2));
  return diffs.length ? `DIFFERENCES:\n    ${diffs.join("\n    ")}` : "matches the baseline";
}

/** Aborts anything that could publish, and any non-GET partner request outside the allowlist. */
async function guard(ctx: import("playwright").BrowserContext): Promise<void> {
  await ctx.route("**/*", async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    if (req.method() !== "GET") {
      const denied = POST_DENYLIST.test(req.url());
      const partner = u.hostname === "partner.steamgames.com";
      if (denied || (partner && !POST_ALLOWLIST.some((re) => re.test(u.pathname)))) {
        console.log(`  BLOCKED ${req.method()} ${u.pathname}`);
        return route.abort("blockedbyclient");
      }
    }
    return route.continue();
  });
}

// ---------------------------------------------------------------- main

async function main(): Promise<void> {
  const appId = Number(process.argv[2]);
  const which = process.argv.slice(3);
  if (!appId || !which.length) {
    console.log("Usage: npx tsx scripts/live/record.ts <appId> list | sprint | <step> [<step> ...]");
    process.exit(1);
  }
  if (which[0] === "list") {
    for (const s of steps) console.log(`${s.id.padEnd(38)} ${s.describe}`);
    return;
  }
  const selected = which[0] === "sprint" ? steps : which.map((id) => steps.find((s) => s.id === id) ?? fail(`Unknown step ${id}`));
  const cfg = loadConfig();
  const page = await getPage(cfg.browserProfileDir, cfg.browser);
  await guard(page.context());
  const rec = new HarRecorder();
  rec.attach(page);
  const state: RunState = (await readJson<RunState>(STATE_FILE)) ?? { notes: [] };
  const ctx: Ctx = { appId, page, rec, key: process.env.STEAMWORKS_PUBLISHER_KEY?.trim() || undefined, state };
  await fs.mkdir(REC, { recursive: true });
  let failed = 0;
  try {
    for (const s of selected) {
      rec.begin();
      let summary: string;
      let ok = true;
      try {
        summary = await s.run(ctx);
      } catch (err: any) {
        ok = false;
        failed++;
        summary = `ERROR ${String(err?.message ?? err).split("\n")[0]}`;
      }
      const file = path.join(RAW, `${s.id}.har`);
      const n = await rec.end(file, { step: s.id, describe: s.describe, recordedAt: new Date().toISOString(), ok, summary });
      const line = `${ok ? "OK  " : "FAIL"} ${s.id} [${n} requests] ${summary}`;
      console.log(line);
      await fs.appendFile(LOG_FILE, `${new Date().toISOString()} ${line}\n`);
      await fs.writeFile(STATE_FILE, JSON.stringify(state, null, 2));
      if (!ok && which[0] === "sprint" && !s.id.startsWith("errors/") && !s.id.startsWith("visibility/") && !s.id.startsWith("api/")) {
        console.log("Stopping the sprint: a non-error step failed. Run the cleanup steps after checking the log.");
        break;
      }
    }
  } finally {
    await closeBrowser();
  }
  if (failed) process.exitCode = 1;
}

function fail(msg: string): never {
  console.log(msg);
  process.exit(1);
}

await main();
