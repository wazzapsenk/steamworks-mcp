import { readLanguage } from "../localization/store.js";
import type { Achievement, Manifest } from "../project/manifest.js";
import type { ProjectPaths } from "../project/paths.js";
import type { LocalizedValue, SteamAchievement } from "../browser/achievements.js";

export type LangMap = Record<string, string>;

export function toLangMap(v: LocalizedValue | undefined): LangMap {
  if (v === undefined) return {};
  if (typeof v === "string") return v === "" ? {} : { english: v };
  return Object.fromEntries(Object.entries(v).filter(([k, s]) => k !== "token" && s !== ""));
}

export function isHidden(v: SteamAchievement["hidden"]): boolean {
  return v === true || v === 1 || v === "1" || v === "true";
}

export interface DesiredAchievement {
  id: string;
  name: LangMap;
  description: LangMap;
  hidden: boolean;
  icon?: string;
  iconLocked?: string;
  source: Achievement;
}

/** Texts for every language we manage: source language from steamworks.yaml plus each translation that exists. */
export async function desiredAchievements(m: Manifest, paths: ProjectPaths): Promise<DesiredAchievement[]> {
  const translations: Record<string, Record<string, string>> = {};
  for (const lang of m.targetLanguages) translations[lang] = await readLanguage(paths, lang);
  return m.achievements.map((a) => {
    const name: LangMap = { [m.sourceLanguage]: a.name };
    const description: LangMap = a.description ? { [m.sourceLanguage]: a.description } : {};
    for (const [lang, t] of Object.entries(translations)) {
      const n = t[`achievements.${a.id}.name`]?.trim();
      const d = t[`achievements.${a.id}.description`]?.trim();
      if (n) name[lang] = n;
      if (d) description[lang] = d;
    }
    return { id: a.id, name, description, hidden: a.hidden, icon: a.icon, iconLocked: a.iconLocked, source: a };
  });
}

export interface AchievementChange {
  id: string;
  action: "create" | "update" | "skip";
  changes: string[];
  reason?: string;
  steam?: SteamAchievement;
  desired: DesiredAchievement;
  /** Merged localized values to save (desired languages override; Steam's other languages are kept). */
  next: { name: LangMap; description: LangMap; hidden: boolean };
  uploadIcons: boolean;
}

export interface AchievementPlan {
  changes: AchievementChange[];
  unchanged: string[];
  onlyInSteam: string[];
}

export function planAchievements(
  desired: DesiredAchievement[],
  steam: SteamAchievement[],
  icons: "missing" | "all" | "none",
): AchievementPlan {
  const byName = new Map(steam.map((s) => [s.api_name, s]));
  const plan: AchievementPlan = { changes: [], unchanged: [], onlyInSteam: [] };

  for (const d of desired) {
    const s = byName.get(d.id);
    if (!s) {
      plan.changes.push({
        id: d.id,
        action: "create",
        changes: ["new achievement", ...Object.keys(d.name).map((l) => `name[${l}]`), ...Object.keys(d.description).map((l) => `description[${l}]`)],
        desired: d,
        next: { name: d.name, description: d.description, hidden: d.hidden },
        uploadIcons: icons !== "none" && !!d.icon,
      });
      continue;
    }
    if (s.progress && typeof s.progress === "object") {
      plan.changes.push({
        id: d.id,
        action: "skip",
        changes: [],
        reason: "Has a progress stat in Steamworks; edit it there so the stat binding isn't lost.",
        steam: s,
        desired: d,
        next: { name: toLangMap(s.display_name), description: toLangMap(s.description), hidden: isHidden(s.hidden) },
        uploadIcons: false,
      });
      continue;
    }
    const curName = toLangMap(s.display_name);
    const curDesc = toLangMap(s.description);
    const changes: string[] = [];
    for (const [l, v] of Object.entries(d.name)) if (curName[l] !== v) changes.push(`name[${l}]: "${curName[l] ?? ""}" → "${v}"`);
    for (const [l, v] of Object.entries(d.description)) if (curDesc[l] !== v) changes.push(`description[${l}]: "${curDesc[l] ?? ""}" → "${v}"`);
    if (isHidden(s.hidden) !== d.hidden) changes.push(`hidden: ${isHidden(s.hidden)} → ${d.hidden}`);
    const iconsNeeded = !!d.icon && (icons === "all" || (icons === "missing" && (!s.icon || !s.icon_gray)));
    if (iconsNeeded) changes.push(icons === "all" ? "icons (re-upload)" : "icons (missing in Steam)");
    if (changes.length === 0) {
      plan.unchanged.push(d.id);
      continue;
    }
    plan.changes.push({
      id: d.id,
      action: "update",
      changes,
      steam: s,
      desired: d,
      next: { name: { ...curName, ...d.name }, description: { ...curDesc, ...d.description }, hidden: d.hidden },
      uploadIcons: iconsNeeded,
    });
  }
  const ids = new Set(desired.map((d) => d.id));
  plan.onlyInSteam = steam.map((s) => s.api_name).filter((n) => !ids.has(n));
  return plan;
}
