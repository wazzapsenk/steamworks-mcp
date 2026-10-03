import { readLanguage } from "../localization/store.js";
import type { Manifest } from "../project/manifest.js";
import type { ProjectPaths } from "../project/paths.js";
import type { StoreLocalizationFile } from "../browser/steamworks.js";

/** Manifest key → field name in Steamworks' store localization JSON. */
export const STORE_FIELDS = {
  "store.shortDescription": "app[content][short_description]",
  "store.about": "app[content][about]",
} as const;

/**
 * Builds the JSON that Steamworks' store page "Upload Localization" accepts:
 *   {"itemid":"…","languages":{"english":{"app[content][short_description]":"…","app[content][about]":"…"}, …}}
 * Only languages and fields that have text are included, so nothing in Steamworks gets blanked.
 */
export async function buildStoreLocalization(
  m: Manifest,
  paths: ProjectPaths,
  itemId: string,
  languages = [m.sourceLanguage, ...m.targetLanguages],
): Promise<StoreLocalizationFile> {
  const out: StoreLocalizationFile = { itemid: itemId, languages: {} };
  for (const lang of languages) {
    const text: Record<string, string> =
      lang === m.sourceLanguage
        ? { "store.shortDescription": m.store.shortDescription, "store.about": m.store.about }
        : await readLanguage(paths, lang);
    const fields: Record<string, string> = {};
    for (const [key, field] of Object.entries(STORE_FIELDS)) {
      const v = text[key]?.trim();
      if (v) fields[field] = v;
    }
    if (Object.keys(fields).length) out.languages[lang] = fields;
  }
  return out;
}

export interface FieldChange {
  language: string;
  field: string;
  before: string;
  after: string;
}

/** Steam's editor wraps paragraphs in [p]…[/p] and escapes slashes; compare without that noise. */
export function normalizeStoreText(s: string): string {
  return s
    .replace(/\r\n/g, "\n")
    .replace(/\[\/?p\]/g, "\n")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

export function diffStoreLocalization(current: StoreLocalizationFile, next: StoreLocalizationFile): FieldChange[] {
  const changes: FieldChange[] = [];
  for (const [lang, fields] of Object.entries(next.languages)) {
    const cur = current.languages[lang];
    const curFields = Array.isArray(cur) || !cur ? {} : cur;
    for (const [field, after] of Object.entries(fields)) {
      const before = curFields[field] ?? "";
      if (normalizeStoreText(before) !== normalizeStoreText(after)) changes.push({ language: lang, field, before, after });
    }
  }
  return changes;
}
