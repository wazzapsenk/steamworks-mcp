// Live check of the partner Web API tools. Needs STEAMWORKS_PUBLISHER_KEY in .env (never printed).
// Usage: npx tsx scripts/live/webapi.ts <appId>
import { SteamWebApi } from "../../src/steam/webapi.js";

try {
  process.loadEnvFile();
} catch {}
const key = process.env.STEAMWORKS_PUBLISHER_KEY?.trim();
const appId = Number(process.argv[2]);
if (!key) {
  console.log("RESULT: STEAMWORKS_PUBLISHER_KEY is not set in .env");
  process.exit(1);
}
if (!appId) {
  console.log("Usage: npx tsx scripts/live/webapi.ts <appId>");
  process.exit(1);
}
const api = new SteamWebApi(key);

const check = async (label: string, fn: () => Promise<unknown>, summarize: (r: any) => string) => {
  try {
    console.log(`OK   ${label}: ${summarize(await fn())}`);
  } catch (err: any) {
    console.log(`FAIL ${label}: ${String(err?.message ?? err).replaceAll(key, "<key>")}`);
  }
};

await check("GetSchemaForGame", () => api.getSchemaForGame(appId, "english"), (r) => {
  const s = r?.game?.availableGameStats;
  return `game="${r?.game?.gameName ?? "?"}", achievements=${s?.achievements?.length ?? 0}, stats=${s?.stats?.length ?? 0}`;
});
await check("GetAppBuilds", () => api.getAppBuilds(appId, 5), (r) => {
  const builds = r?.response?.builds ?? {};
  return `${Object.keys(builds).length} builds; keys=${Object.keys(r?.response ?? {}).join(",")}`;
});
await check("GetAppBetas", () => api.getAppBetas(appId), (r) => {
  const betas = r?.response?.betas ?? {};
  return `branches=${Object.keys(betas).join(", ") || "(none)"}`;
});
await check("GetLeaderboardsForGame", () => api.getLeaderboardsForGame(appId), (r) => `${r?.response?.leaderboards?.length ?? 0} leaderboards`);
