import fs from "node:fs/promises";
import { localizationStatus } from "../localization/store.js";
import { checkScreenshots } from "../media/images.js";
import { LIMITS } from "../steam/limits.js";
import { isApiLanguage } from "../steam/languages.js";
import type { Manifest } from "./manifest.js";
import type { ProjectPaths } from "./paths.js";

export interface Finding {
  level: "error" | "warning";
  area: string;
  message: string;
}

async function exists(file: string): Promise<boolean> {
  return fs.access(file).then(
    () => true,
    () => false,
  );
}

export async function validateProject(m: Manifest, paths: ProjectPaths): Promise<Finding[]> {
  const f: Finding[] = [];
  const err = (area: string, message: string) => f.push({ level: "error", area, message });
  const warn = (area: string, message: string) => f.push({ level: "warning", area, message });

  if (!m.appId) warn("app", "appId is not set; Steamworks links and Web API tools need it.");

  for (const l of [m.sourceLanguage, ...m.targetLanguages]) {
    if (!isApiLanguage(l)) err("languages", `"${l}" is not a Steam API language code (see steam_languages).`);
  }
  if (m.targetLanguages.includes(m.sourceLanguage)) warn("languages", `targetLanguages contains the source language "${m.sourceLanguage}".`);
  if (m.sourceLanguage !== "english" && !m.targetLanguages.includes("english")) {
    err("languages", "Steam falls back to English, so English text is required. Add english to targetLanguages.");
  }
  for (const code of Object.keys(m.store.supportedLanguages)) {
    if (!isApiLanguage(code)) err("store.supportedLanguages", `"${code}" is not a Steam API language code.`);
  }

  const sd = [...m.store.shortDescription].length;
  if (sd === 0) err("store", "shortDescription is empty.");
  else if (sd > LIMITS.shortDescription) warn("store", `shortDescription is ${sd} characters; Steam allows about ${LIMITS.shortDescription}.`);
  if (m.store.about.trim() === "") err("store", "about (About This Game) is empty.");

  for (const [k, rel] of Object.entries({ keyArt: m.store.art.keyArt, logo: m.store.art.logo, ...m.store.art.overrides })) {
    if (!rel) warn("store.art", `${k} is not set; capsules can't be generated without key art and a logo.`);
    else if (!(await exists(paths.file(rel)))) err("store.art", `${k}: file not found: ${rel}`);
  }

  const shots = await checkScreenshots(m, paths);
  for (const p of shots.problems) err("screenshots", p);
  for (const s of shots.screenshots) for (const p of s.problems) warn("screenshots", `${s.file}: ${p}`);

  if (m.achievements.length > LIMITS.achievementsBeforeProfileFeatures) {
    warn("achievements", `${m.achievements.length} achievements; new apps are limited to ${LIMITS.achievementsBeforeProfileFeatures} until they reach the Profile Features threshold.`);
  }
  for (const a of m.achievements) {
    if (!a.icon) warn("achievements", `${a.id}: no icon.`);
    else if (!(await exists(paths.file(a.icon)))) err("achievements", `${a.id}: icon not found: ${a.icon}`);
    if (a.iconLocked && !(await exists(paths.file(a.iconLocked)))) err("achievements", `${a.id}: locked icon not found: ${a.iconLocked}`);
    if (!a.description) warn("achievements", `${a.id}: description is empty.`);
  }

  if (m.cloud) {
    const { byteQuota, fileQuota } = m.cloud;
    if (byteQuota !== undefined && byteQuota > LIMITS.cloudByteQuotaMax) err("cloud", `byteQuota ${byteQuota} exceeds Steam's maximum of ${LIMITS.cloudByteQuotaMax} bytes (10 GB).`);
    if (fileQuota !== undefined && fileQuota > LIMITS.cloudFileQuotaMax) err("cloud", `fileQuota ${fileQuota} exceeds Steam's maximum of ${LIMITS.cloudFileQuotaMax}.`);
    if ((m.cloud.autoCloud?.roots.length ?? 0) > 0 && (!byteQuota || !fileQuota)) {
      err("cloud", "Steamworks only shows Auto-Cloud settings after byteQuota and fileQuota are set and saved.");
    }
  }
  if ((m.cloud?.autoCloud?.rootOverrides.length ?? 0) > 0 && m.cloud?.autoCloud?.roots.some((r) => r.os !== "all")) {
    warn("cloud", "Root overrides only apply to root paths set to all OSes.");
  }

  for (const s of await localizationStatus(m, paths)) {
    if (s.missing.length) warn("localization", `${s.language}: ${s.missing.length}/${s.total} texts not translated.`);
    if (s.stale.length) warn("localization", `${s.language}: ${s.stale.length} translations are out of date (source text changed).`);
  }
  return f;
}
