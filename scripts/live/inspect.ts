// Live, read-only check of steamworks_inspect on real Steamworks pages. Nothing is filled, clicked or saved.
// Usage: npx tsx scripts/live/inspect.ts <appId> [page ...]   (pages: storePage achievements achievementLocalization cloud installation landing)
import fs from "node:fs/promises";
import path from "node:path";
import { inspectPage } from "../../src/browser/forms.js";
import { closeBrowser, getPage, isLoggedIn, wasRedirectedToSignIn } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";
import { steamworksUrls, type SteamworksPage } from "../../src/steam/urls.js";

const appId = Number(process.argv[2]);
const pages = (process.argv.slice(3).length ? process.argv.slice(3) : ["storePage", "achievements", "achievementLocalization", "cloud", "installation"]) as SteamworksPage[];
if (!appId) {
  console.log("Usage: npx tsx scripts/live/inspect.ts <appId> [page ...]");
  process.exit(1);
}
const outDir = path.resolve(".steamworks-mcp/live/inspect");
await fs.mkdir(outDir, { recursive: true });
const page = await getPage(loadConfig().browserProfileDir, loadConfig().browser);

try {
  for (const name of pages) {
    const url = steamworksUrls(appId)[name];
    const res = await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    if (!(await isLoggedIn(page)) || wasRedirectedToSignIn(page)) {
      console.log(`${name}: NOT LOGGED IN (${page.url()})`);
      break;
    }
    const info = await inspectPage(page, 120);
    const file = path.join(outDir, `${appId}-${name}.json`);
    await fs.writeFile(file, JSON.stringify(info, null, 2));
    await page.screenshot({ path: path.join(outDir, `${appId}-${name}.jpg`), type: "jpeg", quality: 60, fullPage: true });
    const visible = info.controls.filter((c) => c.visible);
    const types = visible.reduce<Record<string, number>>((acc, c) => ((acc[c.type] = (acc[c.type] ?? 0) + 1), acc), {});
    console.log(
      `${name}: HTTP ${res?.status()} final=${page.url()} title="${info.title}" controls=${info.controls.length} (visible ${visible.length}: ${JSON.stringify(types)}) buttons=${info.buttons.filter((b) => b.visible).length}`,
    );
  }
} finally {
  await closeBrowser();
}
