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
      "Browser tools need Playwright. Install it next to steamworks-mcp:\n  npm i playwright",
    );
  }
}

/** "auto" tries installed Google Chrome, then Microsoft Edge, then Playwright's bundled Chromium. */
export type BrowserChoice = "auto" | "chrome" | "msedge" | "chromium";

export async function getPage(profileDir: string, browser: BrowserChoice = "auto"): Promise<Page> {
  if (page && !page.isClosed()) return page;
  if (!context) {
    const { chromium } = await loadPlaywright();
    await fs.mkdir(profileDir, { recursive: true });
    const candidates: BrowserChoice[] = browser === "auto" ? ["chrome", "msedge", "chromium"] : [browser];
    const errors: string[] = [];
    for (const choice of candidates) {
      try {
        context = await chromium.launchPersistentContext(profileDir, {
          headless: false,
          viewport: null,
          ...(choice === "chromium" ? {} : { channel: choice }),
        });
        break;
      } catch (err: any) {
        errors.push(`${choice}: ${String(err?.message ?? err).split("\n")[0]}`);
      }
    }
    if (!context) {
      throw new BrowserUnavailableError(
        `Could not start a browser.\n${errors.join("\n")}\nInstall Google Chrome or Microsoft Edge, or run: npx playwright install chromium`,
      );
    }
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

/**
 * Steam sets the httpOnly `steamLoginSecure` cookie once you're signed in. Only its presence is checked;
 * the value is never read out. Anonymous visits to protected pages are redirected to `/?goto=...`.
 */
export async function isLoggedIn(p: Page): Promise<boolean> {
  const cookies = await p.context().cookies(`https://${EDIT_HOST}`);
  return cookies.some((c) => c.name === "steamLoginSecure" && c.value !== "");
}

/** True when Steamworks bounced a protected page back to its sign-in landing page. */
export function wasRedirectedToSignIn(p: Page): boolean {
  const u = new URL(p.url());
  return u.hostname === EDIT_HOST && u.searchParams.has("goto");
}
