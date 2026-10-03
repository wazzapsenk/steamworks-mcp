// WRITE test on an unpublished store page: fills English short description + About, clicks Save (never Publish),
// reloads to verify, then downloads the localization JSON to learn its keys.
import fs from "node:fs/promises";
import path from "node:path";
import { fillFields } from "../../src/browser/forms.js";
import { closeBrowser, getPage } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";
import { steamworksUrls } from "../../src/steam/urls.js";

const appId = Number(process.argv[2]);
const SHORT =
  "[steamworks-mcp test] UPSHOT is a fast arcade shooter where every shot ricochets. Bank shots off walls, chain combos across the arena and climb the daily leaderboards. Placeholder text written by an automated test.";
const ABOUT = "steamworks-mcp test: About This Game placeholder.";
const c = loadConfig();
const outDir = path.resolve(".steamworks-mcp/live/downloads");
const page = await getPage(c.browserProfileDir, c.browser);
const openDescription = async () => {
  await page.goto(steamworksUrls(appId).storePage, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  await page.locator("#tab_description").click();
  await page.waitForTimeout(800);
};
try {
  await openDescription();
  const editor = "#tab_description_content [contenteditable=true]";
  const fields = [
    { selector: "#app_content_short_description__select", value: "english" },
    { selector: "#app_content_short_description__textarea", value: SHORT },
  ];
  console.log("DRY RUN:", JSON.stringify(await fillFields(page, fields, true)));
  console.log("APPLY:", JSON.stringify(await fillFields(page, fields, false)));
  await page.locator(editor).first().click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type(ABOUT);
  const saveBtn = page.locator("#tab_description_content input[type=submit], #tab_description_content input[value=Save]").first();
  console.log("save button:", await saveBtn.getAttribute("value"));
  await Promise.all([page.waitForLoadState("domcontentloaded"), saveBtn.click()]);
  await page.waitForTimeout(3000);
  console.log("after save url:", page.url());
  const msg = await page.locator(".success, .error, .notice, #message, .formRowSuccess").allInnerTexts().catch(() => []);
  console.log("messages:", msg.map((m) => m.trim()).filter(Boolean).slice(0, 5));

  await openDescription();
  const savedShort = await page.locator("#app_content_short_description__textarea").inputValue();
  const savedAbout = await page.locator(editor).first().innerText();
  console.log("VERIFY short persisted:", savedShort === SHORT, `(${savedShort.length} chars)`);
  console.log("VERIFY about persisted:", savedAbout.includes(ABOUT), JSON.stringify(savedAbout.slice(0, 80)));

  await page.locator("#tab_localization").click();
  await page.selectOption("#downloadLocFormat", "json");
  await page.selectOption("#downloadLocLanguage", "all");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.locator("#downloadLocButton").click()]);
  const file = path.join(outDir, `${appId}-store-after-${dl.suggestedFilename()}`);
  await dl.saveAs(file);
  console.log("EXPORT:", (await fs.readFile(file, "utf8")).slice(0, 1500));
} finally {
  await closeBrowser();
}
