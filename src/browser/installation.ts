import type { Page } from "playwright";
import { steamworksUrls } from "../steam/urls.js";
import { ensureNameShim, isLoggedIn, wasRedirectedToSignIn } from "./session.js";

/**
 * Installation → General page (install folder, launch options), via the endpoints the page itself calls
 * (apps/setappinstallfolder, apps/setlaunchoption). Verified on the live site.
 */

export interface LaunchOptionRow {
  index: number;
  executable: string;
  arguments: string;
  workingDir: string;
  /** "", default, config, vr, … */
  type: string;
  /** "", windows, macos, linux, android */
  os: string;
  /** "", 32, 64 */
  arch: string;
  betaKey: string;
  ownsDlc: string;
  descriptions: Record<string, string>;
  /** Fields we don't manage but must send back unchanged. */
  oscpu: string;
  realm: string;
  steamdeck: string;
}

export interface InstallationState {
  installFolder: string;
  launchOptions: LaunchOptionRow[];
}

export async function readInstallation(page: Page, appId: number): Promise<InstallationState> {
  await page.goto(steamworksUrls(appId).installation, { waitUntil: "domcontentloaded" });
  if (!(await isLoggedIn(page)) || wasRedirectedToSignIn(page)) {
    throw new Error("Not logged in to Steamworks. Call steamworks_open and ask the user to sign in.");
  }
  await ensureNameShim(page);
  return page.evaluate(() => {
    const folder = ((document as any).InstallFolderForm?.elements?.installfolder as HTMLInputElement | undefined)?.value ?? "";
    const options: any[] = [];
    for (const f of Array.from(document.forms)) {
      const m = /^LaunchForm(\d+)$/.exec(f.getAttribute("name") ?? "");
      if (!m) continue;
      const v = (n: string) => ((f.elements.namedItem(n) as HTMLInputElement | null)?.value ?? "").trim();
      const descriptions: Record<string, string> = {};
      for (const el of Array.from(document.querySelectorAll<HTMLInputElement>(`input[name^="Launch_${m[1]}_description_loc["]`))) {
        const lang = /\[([a-z]+)\]$/.exec(el.name)?.[1];
        if (lang && el.value) descriptions[lang] = el.value;
      }
      options.push({
        index: Number(m[1]),
        executable: v("executable"),
        arguments: v("argumentsx"),
        workingDir: v("workingdir"),
        type: v("type"),
        os: v("osversion"),
        arch: v("osarch"),
        betaKey: v("betakey"),
        ownsDlc: v("ownsdlc"),
        descriptions,
        oscpu: v("oscpu"),
        realm: v("realm"),
        steamdeck: v("steamdeck"),
      });
    }
    return { installFolder: folder, launchOptions: options.sort((a, b) => a.index - b.index) };
  });
}

async function post(page: Page, url: string, params: Record<string, string>): Promise<void> {
  const res = await page.evaluate(
    async ({ url, params }) => {
      const body = new URLSearchParams({ ...params, sessionid: (window as any).g_sessionID });
      const r = await fetch(url, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
      return { status: r.status, text: await r.text() };
    },
    { url, params },
  );
  let json: any;
  try {
    json = JSON.parse(res.text);
  } catch {
    throw new Error(`${url}: HTTP ${res.status} ${res.text.slice(0, 200)}`);
  }
  if (!json.success) throw new Error(`${url}: ${json.message ?? json.error ?? "failed"}`);
}

export function setInstallFolder(page: Page, appId: number, folder: string) {
  return post(page, `/apps/setappinstallfolder/${appId}`, { installfolder: folder });
}

export function setLaunchOption(page: Page, appId: number, o: LaunchOptionRow) {
  const params: Record<string, string> = {
    index: String(o.index),
    executable: o.executable,
    arguments: o.arguments,
    workingdir: o.workingDir,
    type: o.type,
    // Steamworks keeps a legacy English "description" next to the localized ones.
    description: o.descriptions.english ?? "",
    osversion: o.os,
    osarch: o.arch,
    oscpu: o.oscpu,
    betakey: o.betaKey,
    ownsdlc: o.ownsDlc,
    realm: o.realm,
    steamdeck: o.steamdeck,
  };
  for (const [lang, text] of Object.entries(o.descriptions)) if (text) params[`description_${lang}`] = text;
  return post(page, `/apps/setlaunchoption/${appId}`, params);
}
