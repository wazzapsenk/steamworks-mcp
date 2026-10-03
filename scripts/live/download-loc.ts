// Read-only: downloads the store page localization export and the achievement localization export.
import fs from "node:fs/promises";
import path from "node:path";
import { closeBrowser, getPage } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";
import { steamworksUrls } from "../../src/steam/urls.js";

const appId = Number(process.argv[2]);
const c = loadConfig();
const outDir = path.resolve(".steamworks-mcp/live/downloads");
await fs.mkdir(outDir, { recursive: true });
const page = await getPage(c.browserProfileDir, c.browser);
const save = async (label: string, trigger: () => Promise<void>) => {
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 30000 }), trigger()]);
  const file = path.join(outDir, `${appId}-${label}-${dl.suggestedFilename()}`);
  console.log(`${label} url: ${dl.url().replace(/sessionid=[^&]+/, "sessionid=<redacted>")}`);
  try {
    await dl.saveAs(file);
  } catch (err) {
    console.log(`${label}: saveAs failed (${(err as Error).message.split(/\r?\n/)[0]}); refetching with the session`);
    const res = await page.context().request.get(dl.url());
    await fs.writeFile(file, await res.body());
  }
  const st = await fs.stat(file);
  console.log(`${label}: ${dl.suggestedFilename()} (${st.size} bytes) -> ${file}`);
};
try {
  await page.goto(steamworksUrls(appId).storePage, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  await page.locator("#tab_localization").click();
  await page.waitForTimeout(800);
  for (const sel of ["#downloadLocFormat", "#downloadLocLanguage"]) {
    const opts = await page.locator(`${sel} option`).evaluateAll((os) => os.map((o) => `${(o as HTMLOptionElement).value}=${o.textContent?.trim()}`));
    console.log(sel, "options:", opts.join(", "));
  }
  const help = await page.locator("#tab_localization_content").innerText();
  console.log("TAB TEXT:", help.replace(/\s+/g, " ").slice(0, 900));
  await page.selectOption("#downloadLocFormat", "json");
  await page.selectOption("#downloadLocLanguage", "all");
  await save("store", () => page.locator("#downloadLocButton").click());

  await page.goto(steamworksUrls(appId).achievementLocalization, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  const achText = await page.locator("body").innerText();
  const i = achText.indexOf("Download");
  console.log("ACH PAGE TEXT:", achText.replace(/\s+/g, " ").slice(Math.max(0, i - 600), i + 400));
  await page.selectOption('select[name="download_language"]', "all");
  await save("achievements", () => page.getByText("Download Localization Data", { exact: true }).first().click());
} finally {
  await closeBrowser();
}
