import os from "node:os";
import path from "node:path";

export interface Config {
  /** Steamworks publisher Web API key. Only needed for tools that talk to partner.steam-api.com. */
  publisherKey: string | undefined;
  /**
   * Every project path a tool receives must live under this folder.
   * Matters most in HTTP mode, where a remote client (e.g. ChatGPT) drives the server.
   */
  workspaceRoot: string;
  /** Bearer token required by the HTTP transport. */
  httpToken: string | undefined;
  /** Where the Playwright browser profile (Steamworks login cookies) is stored. */
  browserProfileDir: string;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return {
    publisherKey: env.STEAMWORKS_PUBLISHER_KEY?.trim() || undefined,
    workspaceRoot: path.resolve(env.STEAMWORKS_MCP_ROOT?.trim() || process.cwd()),
    httpToken: env.STEAMWORKS_MCP_TOKEN?.trim() || undefined,
    browserProfileDir: path.resolve(
      env.STEAMWORKS_MCP_BROWSER_PROFILE?.trim() || path.join(os.homedir(), ".steamworks-mcp", "browser-profile"),
    ),
  };
}
