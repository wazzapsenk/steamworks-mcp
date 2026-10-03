/**
 * Minimal client for the Steamworks partner Web API (publisher key).
 * Docs: https://partner.steamgames.com/doc/webapi
 */
const BASE = "https://partner.steam-api.com";

export class SteamApiError extends Error {}

export class SteamWebApi {
  constructor(private readonly key: string) {}

  private async get<T>(path: string, params: Record<string, string | number | undefined>): Promise<T> {
    const url = new URL(path, BASE);
    url.searchParams.set("key", this.key);
    for (const [k, v] of Object.entries(params)) if (v !== undefined) url.searchParams.set(k, String(v));
    return this.send<T>(path, await fetch(url, { headers: { accept: "application/json" } }));
  }

  private async post<T>(path: string, params: Record<string, string | number | boolean | undefined>): Promise<T> {
    const body = new URLSearchParams({ key: this.key });
    for (const [k, v] of Object.entries(params)) if (v !== undefined) body.set(k, String(v));
    return this.send<T>(path, await fetch(new URL(path, BASE), { method: "POST", body, headers: { accept: "application/json" } }));
  }

  private async send<T>(path: string, res: Response): Promise<T> {
    const text = await res.text();
    if (res.status === 403 || res.status === 401) {
      throw new SteamApiError(`${path}: ${res.status} — the publisher key was rejected or lacks permission for this app.`);
    }
    if (!res.ok) throw new SteamApiError(`${path}: HTTP ${res.status} ${text.slice(0, 300)}`);
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new SteamApiError(`${path}: expected JSON, got: ${text.slice(0, 300)}`);
    }
  }

  getSchemaForGame(appid: number, language?: string) {
    return this.get<SchemaResponse>("/ISteamUserStats/GetSchemaForGame/v2/", { appid, l: language });
  }

  getAppBuilds(appid: number, count = 10) {
    return this.get<unknown>("/ISteamApps/GetAppBuilds/v1/", { appid, count });
  }

  getAppBetas(appid: number) {
    return this.get<unknown>("/ISteamApps/GetAppBetas/v1/", { appid });
  }

  getLeaderboardsForGame(appid: number) {
    return this.get<unknown>("/ISteamLeaderboards/GetLeaderboardsForGame/v2/", { appid });
  }

  findOrCreateLeaderboard(input: {
    appid: number;
    name: string;
    sortmethod: "Ascending" | "Descending";
    displaytype: "Numeric" | "Seconds" | "MilliSeconds";
    onlytrustedwrites?: boolean;
    onlyfriendsreads?: boolean;
  }) {
    return this.post<unknown>("/ISteamLeaderboards/FindOrCreateLeaderboard/v2/", { ...input, createifnotfound: true });
  }
}

export interface SchemaAchievement {
  name: string;
  defaultvalue: number;
  displayName: string;
  hidden: number;
  description?: string;
  icon: string;
  icongray: string;
}

export interface SchemaResponse {
  game?: {
    gameName?: string;
    gameVersion?: string;
    availableGameStats?: {
      achievements?: SchemaAchievement[];
      stats?: { name: string; defaultvalue: number; displayName: string }[];
    };
  };
}
