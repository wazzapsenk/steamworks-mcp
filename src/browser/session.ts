import fs from "node:fs/promises";
import type { BrowserContext, Page } from "playwright";

/**
 * A visible Chromium window with a persistent profile, used to drive the Steamworks partner site.
 * The user logs in themselves (password + Steam Guard); this code never sees or types credentials.
 */
const NAV_HOSTS = [/(^|\.)steamgames\.com$/, /(^|\.)steampowered\.com$/, /(^|\.)steamcommunity\.com$/];
const EDIT_HOST = "partner.steamgames.com";

export class BrowserUnavailableError extends Error {}

let context: BrowserContext | undefined;
let page: Page | undefined;

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    throw new BrowserUnavailableError(
      "Browser tools need Playwright. Install it next to steamworks-mcp:\n  npm i playwright\n  npx playwright install chromium",
    );
  }
}

export async function getPage(profileDir: string): Promise<Page> {
  if (page && !page.isClosed()) return page;
  if (!context) {
    const { chromium } = await loadPlaywright();
    await fs.mkdir(profileDir, { recursive: true });
    context = await chromium.launchPersistentContext(profileDir, { headless: false, viewport: null });
    context.on("close", () => {
      context = undefined;
      page = undefined;
    });
  }
  page = context.pages()[0] ?? (await context.newPage());
  return page;
}

export async function closeBrowser(): Promise<void> {
  await context?.close();
  context = undefined;
  page = undefined;
}

export function assertNavigable(url: string): URL {
  const u = new URL(url);
  if (u.protocol !== "https:" || !NAV_HOSTS.some((re) => re.test(u.hostname))) {
    throw new Error(`Refusing to open ${u.origin}: only Steam/Steamworks pages are allowed.`);
  }
  return u;
}

export function assertEditable(p: Page): void {
  const host = new URL(p.url()).hostname;
  if (host !== EDIT_HOST) throw new Error(`The current page is on ${host}; form tools only work on ${EDIT_HOST}.`);
}

export async function isLoggedIn(p: Page): Promise<boolean> {
  if (new URL(p.url()).hostname !== EDIT_HOST) return false;
  // The partner site redirects anonymous users to a login page that contains a password field.
  const hasPassword = await p.locator('input[type="password"]').count();
  return hasPassword === 0 && !/\/login/i.test(p.url());
}
