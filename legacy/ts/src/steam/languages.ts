/**
 * Steam-supported languages.
 * Source: https://partner.steamgames.com/doc/store/localization/languages
 */
export interface SteamLanguage {
  /** API language code used by Steamworks and the client API (e.g. "schinese"). */
  api: string;
  /** Web API language code (e.g. "zh-CN"). */
  web: string;
  name: string;
}

export const STEAM_LANGUAGES: readonly SteamLanguage[] = [
  { api: "arabic", web: "ar", name: "Arabic" },
  { api: "bulgarian", web: "bg", name: "Bulgarian" },
  { api: "schinese", web: "zh-CN", name: "Chinese (Simplified)" },
  { api: "tchinese", web: "zh-TW", name: "Chinese (Traditional)" },
  { api: "czech", web: "cs", name: "Czech" },
  { api: "danish", web: "da", name: "Danish" },
  { api: "dutch", web: "nl", name: "Dutch" },
  { api: "english", web: "en", name: "English" },
  { api: "finnish", web: "fi", name: "Finnish" },
  { api: "french", web: "fr", name: "French" },
  { api: "german", web: "de", name: "German" },
  { api: "greek", web: "el", name: "Greek" },
  { api: "hungarian", web: "hu", name: "Hungarian" },
  { api: "indonesian", web: "id", name: "Indonesian" },
  { api: "italian", web: "it", name: "Italian" },
  { api: "japanese", web: "ja", name: "Japanese" },
  { api: "koreana", web: "ko", name: "Korean" },
  { api: "malay", web: "ms", name: "Malay" },
  { api: "norwegian", web: "no", name: "Norwegian" },
  { api: "polish", web: "pl", name: "Polish" },
  { api: "portuguese", web: "pt", name: "Portuguese (Portugal)" },
  { api: "brazilian", web: "pt-BR", name: "Portuguese (Brazil)" },
  { api: "romanian", web: "ro", name: "Romanian" },
  { api: "russian", web: "ru", name: "Russian" },
  { api: "spanish", web: "es", name: "Spanish (Spain)" },
  { api: "latam", web: "es-419", name: "Spanish (Latin America)" },
  { api: "swedish", web: "sv", name: "Swedish" },
  { api: "thai", web: "th", name: "Thai" },
  { api: "turkish", web: "tr", name: "Turkish" },
  { api: "ukrainian", web: "uk", name: "Ukrainian" },
  { api: "vietnamese", web: "vi", name: "Vietnamese" },
];

const BY_ANY = new Map<string, SteamLanguage>();
for (const l of STEAM_LANGUAGES) {
  BY_ANY.set(l.api, l);
  BY_ANY.set(l.web.toLowerCase(), l);
  BY_ANY.set(l.name.toLowerCase(), l);
}
// Common mistakes people (and models) make.
BY_ANY.set("korean", BY_ANY.get("koreana")!);
BY_ANY.set("chinese", BY_ANY.get("schinese")!);
BY_ANY.set("portuguese-brazil", BY_ANY.get("brazilian")!);

/** Accepts an API code, web code or English name; returns the Steam language or undefined. */
export function findLanguage(input: string): SteamLanguage | undefined {
  return BY_ANY.get(input.trim().toLowerCase());
}

export function isApiLanguage(code: string): boolean {
  return STEAM_LANGUAGES.some((l) => l.api === code);
}
