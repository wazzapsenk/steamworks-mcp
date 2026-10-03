// Read-only: opens the store page, switches tabs by visible text and dumps what each tab shows.
import fs from "node:fs/promises";
import path from "node:path";
import { inspectPage } from "../../src/browser/forms.js";
import { closeBrowser, getPage } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";
import { steamworksUrls } from "../../src/steam/urls.js";

const appId = Number(process.argv[2]);
const tabs = process.argv.slice(3);
const c = loadConfig();
const outDir = path.resolve(".steamworks-mcp/live/inspect");
const page = await getPage(c.browserProfileDir, c.browser);
try {
  await page.goto(steamworksUrls(appId).storePage, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const tabInfo = await page.evaluate(() => {
    const els = Array.from(document.querySelectorAll("a, div, span, li")).filter((e) => {
      const t = (e.textContent ?? "").trim();
      return ["Basic Info", "Description", "Localization", "Graphical Assets", "Publish"].includes(t) && e.children.length === 0;
    });
    return els.map((e) => ({ tag: e.tagName, id: (e as HTMLElement).id, cls: (e as HTMLElement).className, text: e.textContent?.trim(), onclick: e.getAttribute("onclick"), href: e.getAttribute("href") }));
  });
  console.log("TABS:", JSON.stringify(tabInfo));
  for (const t of tabs) {
    await page.getByText(t, { exact: true }).first().click();
    await page.waitForTimeout(1500);
    const info = await inspectPage(page, 150);
    const vis = info.controls.filter((x) => x.visible);
    console.log(`\n=== TAB ${t}: url=${page.url()} visible controls=${vis.length}`);
    for (const x of vis) console.log(`  [${x.type}] ${x.selector} | ${x.name} | "${x.label.slice(0, 60)}" | val="${x.value.slice(0, 50)}"`);
    console.log("  buttons:", info.buttons.filter((b) => b.visible).map((b) => `${b.text} <${b.selector}>`).slice(9).join(" | "));
    const iframes = await page.locator("iframe:visible").count();
    const editors = await page.locator("[contenteditable=true]:visible, .ProseMirror:visible, .ql-editor:visible").count();
    console.log(`  iframes=${iframes} richEditors=${editors}`);
    await page.screenshot({ path: path.join(outDir, `${appId}-tab-${t.replace(/\s/g, "")}.jpg`), type: "jpeg", quality: 60, fullPage: true });
  }
} finally {
  await closeBrowser();
}
