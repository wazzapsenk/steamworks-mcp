/**
 * Text limits. Valve does not publish exact numbers for every field, so these are
 * reported as warnings, never hard errors. Adjust if Steamworks tells you otherwise.
 */
export const LIMITS = {
  /** Steamworks' short description editor stops at ~300 characters ("a few hundred" in the docs). */
  shortDescription: 300,
  /** Practical limits; long achievement texts get truncated in the Steam overlay. */
  achievementName: 64,
  achievementDescription: 160,
  /** New apps are capped at 100 achievements until they reach the Profile Features threshold. */
  achievementsBeforeProfileFeatures: 100,
  minScreenshots: 5,
  /** Shown on the Steam Cloud settings page. */
  cloudByteQuotaMax: 10_000_000_000,
  cloudFileQuotaMax: 10_000,
} as const;
