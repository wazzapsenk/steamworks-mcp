import { readLanguage } from "../localization/store.js";
import type { Manifest } from "../project/manifest.js";
import type { ProjectPaths } from "../project/paths.js";
import type { SchemaResponse } from "./webapi.js";

export interface AchievementDiff {
  language: string;
  onlyInManifest: string[];
  onlyInSteam: string[];
  changed: { id: string; field: "name" | "description" | "hidden"; manifest: string; steam: string }[];
  inSync: number;
}

export async function diffAchievements(m: Manifest, paths: ProjectPaths, schema: SchemaResponse, language: string): Promise<AchievementDiff> {
  const steam = new Map((schema.game?.availableGameStats?.achievements ?? []).map((a) => [a.name, a]));
  const t = language === m.sourceLanguage ? undefined : await readLanguage(paths, language);
  const diff: AchievementDiff = { language, onlyInManifest: [], onlyInSteam: [], changed: [], inSync: 0 };

  for (const a of m.achievements) {
    const s = steam.get(a.id);
    if (!s) {
      diff.onlyInManifest.push(a.id);
      continue;
    }
    const name = t ? t[`achievements.${a.id}.name`] : a.name;
    const description = t ? t[`achievements.${a.id}.description`] : a.description;
    let same = true;
    if (name !== undefined && name !== s.displayName) {
      diff.changed.push({ id: a.id, field: "name", manifest: name, steam: s.displayName });
      same = false;
    }
    if (description !== undefined && description !== (s.description ?? "")) {
      diff.changed.push({ id: a.id, field: "description", manifest: description, steam: s.description ?? "" });
      same = false;
    }
    if (a.hidden !== (s.hidden === 1)) {
      diff.changed.push({ id: a.id, field: "hidden", manifest: String(a.hidden), steam: String(s.hidden === 1) });
      same = false;
    }
    if (same) diff.inSync++;
  }
  const ids = new Set(m.achievements.map((a) => a.id));
  for (const id of steam.keys()) if (!ids.has(id)) diff.onlyInSteam.push(id);
  return diff;
}
