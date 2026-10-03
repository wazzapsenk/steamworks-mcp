// Live check: opens Steamworks in the persistent browser profile, waits for you to log in, then lists your apps.
// Usage: npx tsx scripts/live/login.ts [minutes=20]
import fs from "node:fs/promises";
import path from "node:path";
import { closeBrowser, getPage, isLoggedIn } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";

const config = loadConfig();
const outDir = path.resolve(".steamworks-mcp/live");
const page = await getPage(config.browserProfileDir, config.browser);
await page.goto("https://partner.steamgames.com/", { waitUntil: "domcontentloaded" });

const minutes = Number(process.argv[2] ?? 20);
const deadline = Date.now() + minutes * 60_000;
let logged = await isLoggedIn(page);
if (!logged) console.log(`Waiting for you to click "Sign in" and log in in the browser window (${minutes} min)...`);
while (!logged && Date.now() < deadline) {
  await page.waitForTimeout(3000);
  logged = await isLoggedIn(page).catch(() => false);
}
if (!logged) {
  console.log("RESULT: not logged in (timed out).");
  await closeBrowser();
  process.exit(1);
}
console.log("RESULT: logged in at", page.url());

// Collect apps from "View all applications" (rendered after load, hence networkidle).
const apps = new Map<number, string>();
await page.goto("https://partner.steamgames.com/apps/", { waitUntil: "networkidle" }).catch(() => {});
const found = await page.$$eval("a[href*='/apps/landing/']", (as) =>
  as.map((a) => ({ href: (a as HTMLAnchorElement).href, text: (a.textContent ?? "").replace(/\s+/g, " ").trim() })),
);
for (const f of found) {
  const id = Number(/\/apps\/landing\/(\d+)/.exec(f.href)?.[1]);
  if (id && f.text && !apps.has(id)) apps.set(id, f.text);
}
await fs.mkdir(outDir, { recursive: true });
const list = [...apps].map(([appId, name]) => ({ appId, name }));
await fs.writeFile(path.join(outDir, "apps.json"), JSON.stringify(list, null, 2));
console.log(`APPS (${list.length}):`);
for (const a of list) console.log(`  ${a.appId}  ${a.name}`);
await closeBrowser();
