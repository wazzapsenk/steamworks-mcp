/**
 * Steamworks partner pages, verified against the live site (2026-10). Valve does not document these URLs and may
 * move them; if one redirects to the partner home page, open the app landing page and navigate from there.
 */
export const steamworksUrls = (appId: number) => ({
  landing: `https://partner.steamgames.com/apps/landing/${appId}`,
  storePage: `https://partner.steamgames.com/admin/game/editbyappid/${appId}`,
  achievements: `https://partner.steamgames.com/apps/achievements/${appId}`,
  achievementLocalization: `https://partner.steamgames.com/apps/loc/${appId}`,
  cloud: `https://partner.steamgames.com/apps/cloud/${appId}`,
  installation: `https://partner.steamgames.com/apps/config/${appId}`,
});

export type SteamworksPage = keyof ReturnType<typeof steamworksUrls>;
