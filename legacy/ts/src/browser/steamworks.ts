import type { Page } from "playwright";
import { steamworksUrls } from "../steam/urls.js";
import { isLoggedIn, wasRedirectedToSignIn } from "./session.js";

/**
 * Page-level helpers for Steamworks features that have their own import/export, verified against the live site.
 * They use Steam's official "Download/Upload Localization" paths instead of typing into individual fields.
 */

const BASE = "https://partner.steamgames.com";

export class NotLoggedInError extends Error {
  constructor() {
    super("Not logged in to Steamworks. Call steamworks_open and ask the user to sign in in the browser window.");
  }
}

async function open(page: Page, url: string): Promise<void> {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  if (!(await isLoggedIn(page)) || wasRedirectedToSignIn(page)) throw new NotLoggedInError();
}

/** The store page has its own item id (different from the app id); Steamworks reveals it by redirecting. */
export async function storeItemId(page: Page, appId: number): Promise<string> {
  await open(page, steamworksUrls(appId).storePage);
  const id = /\/admin\/game\/edit\/(\d+)/.exec(page.url())?.[1];
  if (!id) throw new Error(`Could not find the store page for app ${appId} (ended at ${page.url()}). The app may not have a store page (e.g. playtests).`);
  return id;
}

export interface StoreLocalizationFile {
  itemid: string;
  languages: Record<string, Record<string, string> | []>;
}

export async function fetchStoreLocalization(page: Page, itemId: string): Promise<StoreLocalizationFile> {
  const res = await page.context().request.get(`${BASE}/admin/game/downloadloc/${itemId}?language=all&format=json`);
  if (!res.ok()) throw new Error(`Store localization download failed: HTTP ${res.status()}`);
  return (await res.json()) as StoreLocalizationFile;
}

/** Uploads a store localization JSON via the store page's Localization tab and returns Steam's status message. */
export async function uploadStoreLocalization(page: Page, appId: number, file: string): Promise<string> {
  await open(page, steamworksUrls(appId).storePage);
  await page.waitForTimeout(1500);
  await page.locator("#tab_localization").click();
  await page.locator('input[name="localization_files[]"]').setInputFiles(file);
  await submitAndWait(page, () => page.locator("#tab_localization_content button[type=submit]").click());
  return statusMessage(page);
}

export async function fetchAchievementLocalization(page: Page, appId: number): Promise<string> {
  const res = await page.context().request.get(`${BASE}/apps/downloadachievementloc/${appId}?download_language=all`);
  if (!res.ok()) throw new Error(`Achievement localization download failed: HTTP ${res.status()}`);
  return res.text();
}

export async function uploadAchievementLocalization(page: Page, appId: number, file: string): Promise<string> {
  await open(page, steamworksUrls(appId).achievementLocalization);
  const form = page.locator('form[action*="uploadachievementloc"]');
  await form.locator('input[name="kv"]').setInputFiles(file);
  await submitAndWait(page, () => form.locator("input[type=submit]").click());
  return statusMessage(page);
}

/** Clicks a submit control and waits for the resulting page load (a full form POST + redirect). */
async function submitAndWait(page: Page, click: () => Promise<void>): Promise<void> {
  const navigated = page.waitForEvent("framenavigated", { predicate: (f) => f === page.mainFrame(), timeout: 90_000 });
  await click();
  await navigated;
  await page.waitForLoadState("domcontentloaded");
}

async function statusMessage(page: Page): Promise<string> {
  const url = new URL(page.url());
  const fromUrl = [...url.searchParams.entries()].filter(([k]) => /msg/i.test(k)).map(([, v]) => v);
  if (fromUrl.length) return fromUrl.join(" ");
  const texts = await page.locator(".success, .error, .errormsg, .notice, .formRowError").allInnerTexts().catch(() => []);
  return texts.map((t) => t.trim()).filter(Boolean).join(" ") || "(no message shown)";
}
