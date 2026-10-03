import type { InstallationState, LaunchOptionRow } from "../browser/installation.js";
import { readLanguage } from "../localization/store.js";
import type { Manifest } from "../project/manifest.js";
import type { ProjectPaths } from "../project/paths.js";

export interface InstallationPlan {
  installFolder?: { from: string; to: string };
  setLaunchOptions: { row: LaunchOptionRow; changes: string[] }[];
  /** Launch options in Steam beyond the manifest's list; left alone (delete them in Steamworks if needed). */
  extraLaunchOptions: LaunchOptionRow[];
}

const FIELDS = ["executable", "arguments", "workingDir", "type", "os", "arch", "betaKey", "ownsDlc"] as const;

export async function planInstallation(m: Manifest, p: ProjectPaths, cur: InstallationState): Promise<InstallationPlan> {
  const plan: InstallationPlan = { setLaunchOptions: [], extraLaunchOptions: [] };
  const app = m.app;
  if (!app) return plan;
  if (app.installFolder !== undefined && app.installFolder !== cur.installFolder) {
    plan.installFolder = { from: cur.installFolder, to: app.installFolder };
  }
  const translations: Record<string, Record<string, string>> = {};
  for (const lang of m.targetLanguages) translations[lang] = await readLanguage(p, lang);

  app.launchOptions.forEach((o, index) => {
    const before = cur.launchOptions[index];
    const descriptions: Record<string, string> = { ...(before?.descriptions ?? {}) };
    if (o.description) descriptions[m.sourceLanguage] = o.description;
    for (const [lang, t] of Object.entries(translations)) {
      const d = t[`app.launchOptions.${index}.description`]?.trim();
      if (d) descriptions[lang] = d;
    }
    const row: LaunchOptionRow = {
      index,
      executable: o.executable,
      arguments: o.arguments,
      workingDir: o.workingDir,
      type: o.type,
      os: o.os === "all" ? "" : o.os,
      arch: o.arch === "all" ? "" : o.arch,
      betaKey: o.betaKey,
      ownsDlc: o.ownsDlc,
      descriptions,
      oscpu: before?.oscpu ?? "",
      realm: before?.realm ?? "",
      steamdeck: before?.steamdeck ?? "",
    };
    const changes: string[] = [];
    if (!before) changes.push("new launch option");
    else {
      for (const f of FIELDS) if (before[f] !== row[f]) changes.push(`${f}: "${before[f]}" → "${row[f]}"`);
      for (const [lang, d] of Object.entries(descriptions)) if (before.descriptions[lang] !== d) changes.push(`description[${lang}]: "${before.descriptions[lang] ?? ""}" → "${d}"`);
    }
    if (changes.length) plan.setLaunchOptions.push({ row, changes });
  });
  plan.extraLaunchOptions = cur.launchOptions.slice(app.launchOptions.length);
  return plan;
}

export function installationPlanIsEmpty(p: InstallationPlan): boolean {
  return !p.installFolder && p.setLaunchOptions.length === 0;
}
