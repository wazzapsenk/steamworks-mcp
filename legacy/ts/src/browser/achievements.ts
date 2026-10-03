import fs from "node:fs/promises";
import path from "node:path";
import type { Page } from "playwright";
import { steamworksUrls } from "../steam/urls.js";
import { ensureNameShim, isLoggedIn, wasRedirectedToSignIn } from "./session.js";

/**
 * Steamworks "Stats & Achievements" page actions, using the same AJAX endpoints the page itself calls
 * (apps/fetchachievements, newachievement, saveachievement, images/uploadachievement). Verified on the live site.
 * Everything runs inside the logged-in page so the request carries the page's own session and CSRF id.
 */

/** A localized field is either a plain (English) string or { english, turkish, …, token }. */
export type LocalizedValue = string | Record<string, string>;

export interface SteamAchievement {
  stat_id: number | string;
  bit_id: number | string;
  api_name: string;
  display_name: LocalizedValue;
  description: LocalizedValue;
  hidden: number | string | boolean;
  permission: number | string;
  icon?: string;
  icon_gray?: string;
  [key: string]: unknown;
}

export interface AchievementPageState {
  achievements: SteamAchievement[];
  languages: Record<string, unknown>;
  maxStatId: string;
  maxBitId: string;
}

export async function openAchievements(page: Page, appId: number): Promise<AchievementPageState> {
  await page.goto(steamworksUrls(appId).achievements, { waitUntil: "domcontentloaded" });
  if (!(await isLoggedIn(page)) || wasRedirectedToSignIn(page)) {
    throw new Error("Not logged in to Steamworks. Call steamworks_open and ask the user to sign in.");
  }
  await ensureNameShim(page);
  await page.waitForFunction(() => document.getElementById("max_statid_used") !== null, undefined, { timeout: 20_000 });
  const data = await ajax(page, `/apps/fetchachievements/${appId}`, {}, "get");
  const ids = await page.evaluate(() => ({
    maxStatId: document.getElementById("max_statid_used")?.textContent?.trim() ?? "0",
    maxBitId: document.getElementById("max_bitid_used")?.textContent?.trim() ?? "-1",
  }));
  return { achievements: data.achievements ?? [], languages: data.languages ?? {}, ...ids };
}

async function ajax(page: Page, url: string, params: Record<string, string>, method: "get" | "post" = "post"): Promise<any> {
  const res = await page.evaluate(
    async ({ url, params, method }) => {
      const body = new URLSearchParams(params);
      if (method === "post") body.set("sessionid", (window as any).g_sessionID);
      const r = await fetch(method === "get" ? `${url}?${body}` : url, {
        method: method.toUpperCase(),
        credentials: "same-origin",
        headers: { Accept: "application/json", ...(method === "post" ? { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8" } : {}) },
        ...(method === "post" ? { body: body.toString() } : {}),
      });
      return { status: r.status, text: await r.text() };
    },
    { url, params, method },
  );
  if (res.status !== 200) throw new Error(`${url}: HTTP ${res.status}`);
  try {
    return JSON.parse(res.text);
  } catch {
    throw new Error(`${url}: unexpected response ${res.text.slice(0, 200)}`);
  }
}

export async function createAchievement(page: Page, appId: number, state: AchievementPageState): Promise<SteamAchievement> {
  const r = await ajax(page, `/apps/newachievement/${appId}`, { maxstatid: state.maxStatId, maxbitid: state.maxBitId });
  if (r.success !== 1) throw new Error(`newachievement failed: ${r.error ?? JSON.stringify(r).slice(0, 200)}`);
  state.maxStatId = String(r.maxstatid);
  state.maxBitId = String(r.maxbitid);
  return r.achievement as SteamAchievement;
}

export interface AchievementSave {
  statId: string;
  bitId: string;
  apiName: string;
  displayName: LocalizedValue;
  description: LocalizedValue;
  hidden: boolean;
  /** Index in Steamworks' "Set By" select (0 = Client). */
  permission: number;
  progressStat?: string;
  progressMin?: string;
  progressMax?: string;
}

/** Mirrors the page's FetchLocalizedForm: drop empty languages; English-only collapses to a plain string. */
export function marshalLocalized(v: LocalizedValue): string {
  if (typeof v === "string") return JSON.stringify(v);
  const clean = Object.fromEntries(Object.entries(v).filter(([, s]) => s !== ""));
  const keys = Object.keys(clean);
  return JSON.stringify(keys.length === 1 && keys[0] === "english" ? clean.english : clean);
}

export async function saveAchievement(page: Page, appId: number, a: AchievementSave): Promise<SteamAchievement> {
  const r = await ajax(page, `/apps/saveachievement/${appId}`, {
    statid: a.statId,
    bitid: a.bitId,
    apiname: a.apiName,
    displayname: marshalLocalized(a.displayName),
    description: marshalLocalized(a.description),
    permission: String(a.permission),
    hidden: String(a.hidden),
    progressStat: a.progressStat ?? "-1",
    progressMin: a.progressMin ?? "",
    progressMax: a.progressMax ?? "",
  });
  if (r.success !== 1) throw new Error(`saveachievement ${a.apiName} failed: ${r.error ?? JSON.stringify(r).slice(0, 200)}`);
  if (!r.saved) throw new Error(`saveachievement ${a.apiName}: Steam did not save (response ${JSON.stringify(r).slice(0, 200)})`);
  return r.achievement as SteamAchievement;
}

export async function uploadAchievementIcon(page: Page, appId: number, statId: string, bitId: string, file: string, gray: boolean): Promise<void> {
  const b64 = (await fs.readFile(file)).toString("base64");
  const mime = /\.png$/i.test(file) ? "image/png" : "image/jpeg";
  const res = await page.evaluate(
    async ({ appId, statId, bitId, gray, b64, mime, name }) => {
      const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const fd = new FormData();
      fd.set("sessionid", (window as any).g_sessionID);
      fd.set("MAX_FILE_SIZE", "3000000");
      fd.set("appID", String(appId));
      fd.set("statID", statId);
      fd.set("bit", bitId);
      fd.set("requestType", gray ? "achievement_gray" : "achievement");
      fd.set("image", new Blob([bin], { type: mime }), name);
      const r = await fetch("/images/uploadachievement", { method: "POST", body: fd, credentials: "same-origin" });
      return { status: r.status, text: await r.text() };
    },
    { appId, statId, bitId, gray, b64, mime, name: path.basename(file) },
  );
  if (res.status !== 200) throw new Error(`Icon upload failed: HTTP ${res.status}`);
  let parsed: any;
  try {
    parsed = JSON.parse(res.text.replace(/^[^{]*/, "").replace(/[^}]*$/, ""));
  } catch {
    parsed = undefined;
  }
  if (parsed && parsed.success !== undefined && parsed.success !== 1 && parsed.success !== true) {
    throw new Error(`Icon upload rejected: ${parsed.message ?? parsed.error ?? res.text.slice(0, 200)}`);
  }
}
