import type { Page } from "playwright";
import { steamworksUrls } from "../steam/urls.js";
import { ensureNameShim, isLoggedIn, wasRedirectedToSignIn } from "./session.js";

/**
 * Steam Cloud page, via the endpoints the page itself calls (apps/setufsparameters, setautocloudpath,
 * setautocloudoverride). Verified on the live site. Values use Steamworks' internal names:
 * roots like "gameinstall"/"WinAppDataLocalLow", OS "" (all) / "Windows" / "MacOS" / "Linux" / "Android".
 */

export interface CloudRootRow {
  index: number;
  root: string;
  path: string;
  pattern: string;
  os: string;
  recursive: boolean;
}

export interface CloudOverrideRow {
  index: number;
  root: string;
  os: string;
  useInstead: string;
  addPath: string;
  replacePath: boolean;
}

export interface CloudState {
  byteQuota: number;
  fileQuota: number;
  sharedAppId: number;
  developersOnly: boolean;
  syncOnSuspend: boolean;
  roots: CloudRootRow[];
  overrides: CloudOverrideRow[];
}

export async function readCloud(page: Page, appId: number): Promise<CloudState> {
  await page.goto(steamworksUrls(appId).cloud, { waitUntil: "domcontentloaded" });
  if (!(await isLoggedIn(page)) || wasRedirectedToSignIn(page)) {
    throw new Error("Not logged in to Steamworks. Call steamworks_open and ask the user to sign in.");
  }
  await ensureNameShim(page);
  return page.evaluate(() => {
    const val = (id: string) => (document.getElementById(id) as HTMLInputElement | null)?.value ?? "0";
    const chk = (id: string) => (document.getElementById(id) as HTMLInputElement | null)?.checked ?? false;
    const roots: any[] = [];
    const overrides: any[] = [];
    for (const f of Array.from(document.forms)) {
      const m = /(\d+)$/.exec(f.getAttribute("name") ?? "");
      if (!m) continue;
      const el = (n: string) => f.elements.namedItem(n) as HTMLInputElement | HTMLSelectElement | null;
      if (el("useinstead")) {
        overrides.push({
          index: Number(m[1]),
          root: el("root")?.value ?? "",
          os: el("os")?.value ?? "",
          useInstead: el("useinstead")?.value ?? "",
          addPath: el("addpath")?.value ?? "",
          replacePath: (el("replacepath") as HTMLInputElement | null)?.checked ?? false,
        });
      } else if (el("pattern")) {
        roots.push({
          index: Number(m[1]),
          root: el("root")?.value ?? "",
          path: el("path")?.value ?? "",
          pattern: el("pattern")?.value ?? "",
          os: el("oslist")?.value ?? "",
          recursive: (el("recursive") as HTMLInputElement | null)?.checked ?? false,
        });
      }
    }
    return {
      byteQuota: Number(val("ufsQuota").replace(/\D/g, "") || 0),
      fileQuota: Number(val("ufsFiles").replace(/\D/g, "") || 0),
      sharedAppId: Number(val("relatedAppID") || 0),
      developersOnly: chk("ufsHideInClient"),
      syncOnSuspend: chk("ufsAllowSyncOnSuspend"),
      roots: roots.sort((a, b) => a.index - b.index),
      overrides: overrides.sort((a, b) => a.index - b.index),
    };
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

export function setUfs(page: Page, appId: number, s: Pick<CloudState, "byteQuota" | "fileQuota" | "sharedAppId" | "developersOnly" | "syncOnSuspend">) {
  return post(page, `/apps/setufsparameters/${appId}`, {
    cb: String(s.byteQuota),
    cfiles: String(s.fileQuota),
    appidRedirect: String(s.sharedAppId),
    hideInClient: s.developersOnly ? "1" : "0",
    syncOnSuspend: s.syncOnSuspend ? "1" : "0",
  });
}

export function setRoot(page: Page, appId: number, r: CloudRootRow) {
  return post(page, `/apps/setautocloudpath/${appId}`, {
    index: String(r.index),
    root: r.root,
    path: r.path,
    pattern: r.pattern,
    oslist: r.os,
    recursive: String(r.recursive),
  });
}

export function deleteRoot(page: Page, appId: number, index: number) {
  return post(page, `/apps/setautocloudpath/${appId}`, { index: String(index), root: "", path: "", pattern: "", oslist: "" });
}

export function setOverride(page: Page, appId: number, o: CloudOverrideRow) {
  return post(page, `/apps/setautocloudoverride/${appId}`, {
    index: String(o.index),
    root: o.root,
    os: o.os,
    useinstead: o.useInstead,
    addpath: o.addPath,
    replacepath: String(o.replacePath),
  });
}

export function deleteOverride(page: Page, appId: number, index: number) {
  return post(page, `/apps/setautocloudoverride/${appId}`, { index: String(index), root: "", os: "", useinstead: "", addpath: "", replacepath: "" });
}
