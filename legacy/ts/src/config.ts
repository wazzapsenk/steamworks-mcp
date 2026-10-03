import os from "node:os";
import path from "node:path";
import type { BrowserChoice } from "./browser/session.js";

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
  /** Which browser drives Steamworks: auto (Chrome → Edge → bundled Chromium), chrome, msedge or chromium. */
  browser: BrowserChoice;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return {
    publisherKey: env.STEAMWORKS_PUBLISHER_KEY?.trim() || undefined,
    workspaceRoot: path.resolve(env.STEAMWORKS_MCP_ROOT?.trim() || process.cwd()),
    httpToken: env.STEAMWORKS_MCP_TOKEN?.trim() || undefined,
    browserProfileDir: path.resolve(
      env.STEAMWORKS_MCP_BROWSER_PROFILE?.trim() || path.join(os.homedir(), ".steamworks-mcp", "browser-profile"),
    ),
    browser: parseBrowser(env.STEAMWORKS_MCP_BROWSER),
  };
}

function parseBrowser(v: string | undefined): BrowserChoice {
  const b = v?.trim().toLowerCase() || "auto";
  if (b === "auto" || b === "chrome" || b === "msedge" || b === "chromium") return b;
  if (b === "edge") return "msedge";
  throw new Error(`STEAMWORKS_MCP_BROWSER must be auto, chrome, msedge or chromium (got "${v}").`);
}
