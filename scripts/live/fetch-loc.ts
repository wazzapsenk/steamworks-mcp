// Read-only: fetches localization exports over HTTP with the browser session (no download dialog involved).
import fs from "node:fs/promises";
import path from "node:path";
import { closeBrowser, getPage } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";
import { steamworksUrls } from "../../src/steam/urls.js";

const appId = Number(process.argv[2]);
const c = loadConfig();
const outDir = path.resolve(".steamworks-mcp/live/downloads");
const page = await getPage(c.browserProfileDir, c.browser);
try {
  await page.goto(steamworksUrls(appId).storePage, { waitUntil: "domcontentloaded" });
  const itemId = /\/admin\/game\/edit\/(\d+)/.exec(page.url())?.[1];
  console.log("store item id:", itemId);
  const res = await page.context().request.get(`https://partner.steamgames.com/admin/game/downloadloc/${itemId}?language=all&format=json`);
  const body = await res.text();
  await fs.writeFile(path.join(outDir, `${appId}-store-fetched.json`), body);
  console.log("store export", res.status(), body.slice(0, 2000));

  await page.goto(steamworksUrls(appId).achievementLocalization, { waitUntil: "domcontentloaded" });
  const how = await page.evaluate(() => {
    const el = Array.from(document.querySelectorAll("a, button, input")).find((e) => /Download Localization Data/i.test((e as HTMLInputElement).value || e.textContent || ""));
    const form = el?.closest("form");
    return { tag: el?.tagName, href: el?.getAttribute("href"), onclick: el?.getAttribute("onclick"), form: form ? { action: form.getAttribute("action"), method: form.getAttribute("method") } : null };
  });
  console.log("achievement download control:", JSON.stringify(how));
} finally {
  await closeBrowser();
}
