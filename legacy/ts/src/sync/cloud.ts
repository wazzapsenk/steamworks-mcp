import type { CloudOverrideRow, CloudRootRow, CloudState } from "../browser/cloud.js";
import type { Manifest } from "../project/manifest.js";

const OS: Record<string, string> = { all: "", windows: "Windows", macos: "MacOS", linux: "Linux", android: "Android" };

export interface CloudPlan {
  ufs?: { from: Partial<CloudState>; to: Pick<CloudState, "byteQuota" | "fileQuota" | "sharedAppId" | "developersOnly" | "syncOnSuspend"> };
  setRoots: { row: CloudRootRow; before?: CloudRootRow }[];
  deleteRoots: CloudRootRow[];
  setOverrides: { row: CloudOverrideRow; before?: CloudOverrideRow }[];
  deleteOverrides: CloudOverrideRow[];
  /** Rows in Steam beyond the manifest's list, left alone because removeExtra=false. */
  extraRoots: CloudRootRow[];
  extraOverrides: CloudOverrideRow[];
}

const sameRoot = (a: CloudRootRow, b: CloudRootRow) =>
  a.root === b.root && a.path === b.path && a.pattern === b.pattern && a.os === b.os && a.recursive === b.recursive;
const sameOverride = (a: CloudOverrideRow, b: CloudOverrideRow) =>
  a.root === b.root && a.os === b.os && a.useInstead === b.useInstead && a.addPath === b.addPath && a.replacePath === b.replacePath;

/** Rows are matched by position (Steamworks addresses them by index). */
export function planCloud(m: Manifest, cur: CloudState, removeExtra: boolean): CloudPlan {
  const c = m.cloud ?? {};
  const plan: CloudPlan = { setRoots: [], deleteRoots: [], setOverrides: [], deleteOverrides: [], extraRoots: [], extraOverrides: [] };

  const to = {
    byteQuota: c.byteQuota ?? cur.byteQuota,
    fileQuota: c.fileQuota ?? cur.fileQuota,
    sharedAppId: c.sharedAppId ?? cur.sharedAppId,
    developersOnly: c.developersOnly ?? cur.developersOnly,
    syncOnSuspend: c.syncOnSuspend ?? cur.syncOnSuspend,
  };
  if ((Object.keys(to) as (keyof typeof to)[]).some((k) => to[k] !== cur[k])) {
    plan.ufs = { from: Object.fromEntries((Object.keys(to) as (keyof typeof to)[]).map((k) => [k, cur[k]])), to };
  }

  const roots: CloudRootRow[] = (c.autoCloud?.roots ?? []).map((r, index) => ({
    index,
    root: r.root,
    path: r.subdirectory,
    pattern: r.pattern,
    os: OS[r.os] ?? "",
    recursive: r.recursive,
  }));
  roots.forEach((row, i) => {
    const before = cur.roots[i];
    if (!before || !sameRoot(before, row)) plan.setRoots.push({ row, ...(before ? { before } : {}) });
  });
  const extraRoots = cur.roots.slice(roots.length);
  if (removeExtra) plan.deleteRoots = [...extraRoots].reverse();
  else plan.extraRoots = extraRoots;

  const overrides: CloudOverrideRow[] = (c.autoCloud?.rootOverrides ?? []).map((o, index) => ({
    index,
    root: o.originalRoot,
    os: OS[o.os] ?? "",
    useInstead: o.newRoot,
    addPath: o.addOrReplacePath,
    replacePath: o.replace,
  }));
  overrides.forEach((row, i) => {
    const before = cur.overrides[i];
    if (!before || !sameOverride(before, row)) plan.setOverrides.push({ row, ...(before ? { before } : {}) });
  });
  const extraOverrides = cur.overrides.slice(overrides.length);
  if (removeExtra) plan.deleteOverrides = [...extraOverrides].reverse();
  else plan.extraOverrides = extraOverrides;

  return plan;
}

export function planIsEmpty(p: CloudPlan): boolean {
  return !p.ufs && !p.setRoots.length && !p.deleteRoots.length && !p.setOverrides.length && !p.deleteOverrides.length;
}
