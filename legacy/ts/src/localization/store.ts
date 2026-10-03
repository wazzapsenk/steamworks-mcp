import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import YAML from "yaml";
import type { Manifest } from "../project/manifest.js";
import type { ProjectPaths } from "../project/paths.js";
import { LIMITS } from "../steam/limits.js";

export type TextFormat = "plain" | "bbcode";

export interface TranslatableEntry {
  key: string;
  text: string;
  format: TextFormat;
  maxLength?: number;
  /** Short hint for the translator (the LLM). */
  context: string;
}

/** Every piece of text in the manifest that is shown to players and must be localized. */
export function translatableEntries(m: Manifest): TranslatableEntry[] {
  const out: TranslatableEntry[] = [];
  const push = (e: TranslatableEntry) => {
    if (e.text.trim() !== "") out.push(e);
  };
  push({
    key: "store.shortDescription",
    text: m.store.shortDescription,
    format: "plain",
    maxLength: LIMITS.shortDescription,
    context: `Steam store short description for the game "${m.name}". Shown next to the header capsule. One or two punchy sentences.`,
  });
  push({
    key: "store.about",
    text: m.store.about,
    format: "bbcode",
    context: `"About This Game" section of the Steam store page for "${m.name}". Keep every Steam BBCode tag ([h2], [b], [list], [*], [img], [url=...]) exactly as-is; translate only the human-readable text. Do not translate image/URL paths.`,
  });
  for (const a of m.achievements) {
    push({
      key: `achievements.${a.id}.name`,
      text: a.name,
      format: "plain",
      maxLength: LIMITS.achievementName,
      context: `Name of a Steam achievement in "${m.name}". Short title.${a.hidden ? " This is a hidden achievement." : ""}`,
    });
    push({
      key: `achievements.${a.id}.description`,
      text: a.description,
      format: "plain",
      maxLength: LIMITS.achievementDescription,
      context: `Description of the Steam achievement "${a.name}" in "${m.name}". Tells the player how to unlock it.`,
    });
  }
  for (const [i, o] of (m.app?.launchOptions ?? []).entries()) {
    push({
      key: `app.launchOptions.${i}.description`,
      text: o.description,
      format: "plain",
      maxLength: 64,
      context: `Label of a launch option for "${m.name}" shown in the Steam client's Play menu (e.g. "Play in DirectX 11"). Short.`,
    });
  }
  return out;
}

export function hashText(text: string): string {
  return createHash("sha256").update(text.replace(/\r\n/g, "\n")).digest("hex").slice(0, 16);
}

type Lock = Record<string, Record<string, string>>;

const LOCK_FILE = ".lock.json";

function languageFile(paths: ProjectPaths, lang: string): string {
  if (!/^[a-z]+$/.test(lang)) throw new Error(`"${lang}" is not a Steam API language code (lowercase letters only).`);
  return path.join(paths.localizationDir, `${lang}.yaml`);
}

export async function readLanguage(paths: ProjectPaths, lang: string): Promise<Record<string, string>> {
  try {
    const data = YAML.parse(await fs.readFile(languageFile(paths, lang), "utf8")) ?? {};
    if (typeof data !== "object" || Array.isArray(data)) throw new Error(`localization/${lang}.yaml must be a flat key: text map.`);
    return Object.fromEntries(Object.entries(data).map(([k, v]) => [k, String(v ?? "")]));
  } catch (err: any) {
    if (err?.code === "ENOENT") return {};
    throw err;
  }
}

async function writeLanguage(paths: ProjectPaths, lang: string, data: Record<string, string>): Promise<void> {
  await fs.mkdir(paths.localizationDir, { recursive: true });
  const doc = new YAML.Document(data);
  await fs.writeFile(languageFile(paths, lang), doc.toString({ lineWidth: 0, blockQuote: "literal" }), "utf8");
}

async function readLock(paths: ProjectPaths): Promise<Lock> {
  try {
    return JSON.parse(await fs.readFile(path.join(paths.localizationDir, LOCK_FILE), "utf8"));
  } catch (err: any) {
    if (err?.code === "ENOENT") return {};
    throw err;
  }
}

async function writeLock(paths: ProjectPaths, lock: Lock): Promise<void> {
  await fs.mkdir(paths.localizationDir, { recursive: true });
  await fs.writeFile(path.join(paths.localizationDir, LOCK_FILE), JSON.stringify(lock, null, 2) + "\n", "utf8");
}

export interface LanguageStatus {
  language: string;
  total: number;
  translated: number;
  missing: string[];
  /** Translated, but the source text changed afterwards. */
  stale: string[];
  /** Keys in the language file that no longer exist in the source. */
  orphaned: string[];
}

export async function localizationStatus(m: Manifest, paths: ProjectPaths, languages = m.targetLanguages): Promise<LanguageStatus[]> {
  const entries = translatableEntries(m);
  const lock = await readLock(paths);
  const result: LanguageStatus[] = [];
  for (const lang of languages) {
    const data = await readLanguage(paths, lang);
    const hashes = lock[lang] ?? {};
    const missing: string[] = [];
    const stale: string[] = [];
    for (const e of entries) {
      const t = data[e.key];
      if (t === undefined || t.trim() === "") missing.push(e.key);
      else if (hashes[e.key] !== hashText(e.text)) stale.push(e.key);
    }
    const known = new Set(entries.map((e) => e.key));
    result.push({
      language: lang,
      total: entries.length,
      translated: entries.length - missing.length - stale.length,
      missing,
      stale,
      orphaned: Object.keys(data).filter((k) => !known.has(k)),
    });
  }
  return result;
}

export interface PendingEntry extends TranslatableEntry {
  reason: "missing" | "stale";
  previousTranslation?: string;
}

export async function pendingTranslations(m: Manifest, paths: ProjectPaths, lang: string, limit = 50): Promise<PendingEntry[]> {
  const [status] = await localizationStatus(m, paths, [lang]);
  const data = await readLanguage(paths, lang);
  const missing = new Set(status!.missing);
  const stale = new Set(status!.stale);
  return translatableEntries(m)
    .filter((e) => missing.has(e.key) || stale.has(e.key))
    .slice(0, limit)
    .map((e) => ({
      ...e,
      reason: missing.has(e.key) ? "missing" : "stale",
      ...(stale.has(e.key) ? { previousTranslation: data[e.key] } : {}),
    }));
}

export interface SetResult {
  saved: string[];
  rejected: { key: string; reason: string }[];
  warnings: { key: string; warning: string }[];
}

export async function setTranslations(
  m: Manifest,
  paths: ProjectPaths,
  lang: string,
  translations: { key: string; text: string }[],
): Promise<SetResult> {
  if (lang === m.sourceLanguage) throw new Error(`"${lang}" is the source language; edit steamworks.yaml instead.`);
  const entries = new Map(translatableEntries(m).map((e) => [e.key, e]));
  const data = await readLanguage(paths, lang);
  const lock = await readLock(paths);
  const hashes = (lock[lang] ??= {});
  const result: SetResult = { saved: [], rejected: [], warnings: [] };

  for (const { key, text } of translations) {
    const entry = entries.get(key);
    if (!entry) {
      result.rejected.push({ key, reason: "Unknown key. Use localization_pending to see valid keys." });
      continue;
    }
    const check = checkTranslation(entry, text);
    if (check.error) {
      result.rejected.push({ key, reason: check.error });
      continue;
    }
    if (check.warning) result.warnings.push({ key, warning: check.warning });
    data[key] = text;
    hashes[key] = hashText(entry.text);
    result.saved.push(key);
  }

  if (result.saved.length > 0) {
    // Keep the file in source order so diffs stay readable.
    const ordered: Record<string, string> = {};
    for (const k of entries.keys()) if (data[k] !== undefined) ordered[k] = data[k]!;
    for (const [k, v] of Object.entries(data)) if (!(k in ordered)) ordered[k] = v;
    await writeLanguage(paths, lang, ordered);
    await writeLock(paths, lock);
  }
  return result;
}

export function checkTranslation(entry: TranslatableEntry, text: string): { error?: string; warning?: string } {
  if (text.trim() === "") return { error: "Translation is empty." };
  if (entry.format === "bbcode") {
    const want = bbcodeTagSignature(entry.text);
    const got = bbcodeTagSignature(text);
    if (want !== got) {
      return { error: `BBCode tags differ from the source. Expected tags in order: ${want || "(none)"}; got: ${got || "(none)"}.` };
    }
  }
  const length = [...text].length;
  if (entry.maxLength !== undefined && length > entry.maxLength) {
    return { warning: `${length} characters; Steam shows about ${entry.maxLength}. Consider shortening.` };
  }
  return {};
}

/** Ordered list of BBCode tag names, ignoring attributes, e.g. "h2,/h2,list,*,/list". */
export function bbcodeTagSignature(text: string): string {
  return [...text.matchAll(/\[(\/?)([a-z0-9*]+)(?:[= ][^\]]*)?\]/gi)].map((m) => `${m[1]}${m[2]!.toLowerCase()}`).join(",");
}

/** Text for `key` in `lang`, falling back to the source text. */
export function textFor(m: Manifest, translations: Record<string, Record<string, string>>, lang: string, key: string): string | undefined {
  if (lang === m.sourceLanguage) return translatableEntries(m).find((e) => e.key === key)?.text;
  return translations[lang]?.[key];
}
