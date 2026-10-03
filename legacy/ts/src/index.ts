#!/usr/bin/env node
import { randomUUID } from "node:crypto";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { NextFunction, Request, Response } from "express";
import { loadConfig } from "./config.js";
import { createServer } from "./server.js";

const HELP = `steamworks-mcp — Steam store page & Steamworks settings as code

Usage:
  steamworks-mcp                 Run over stdio (Claude Desktop, Claude Code, Cursor, ...)
  steamworks-mcp --http [--port 8787] [--host 127.0.0.1]
                                 Run the Streamable HTTP transport at /mcp (ChatGPT, remote clients)

Environment:
  STEAMWORKS_PUBLISHER_KEY       Steamworks publisher Web API key (optional; enables steam_api_* tools)
  STEAMWORKS_MCP_ROOT            Folder that all project paths must live in (default: current directory)
  STEAMWORKS_MCP_TOKEN           Bearer token required in HTTP mode (strongly recommended)
  STEAMWORKS_MCP_BROWSER_PROFILE Where the Steamworks browser session is kept (default: ~/.steamworks-mcp/browser-profile)
  STEAMWORKS_MCP_BROWSER         auto (default: Chrome, then Edge, then Playwright Chromium) | chrome | msedge | chromium
`;

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main() {
  if (process.argv.includes("--help") || process.argv.includes("-h")) {
    process.stdout.write(HELP);
    return;
  }
  try {
    process.loadEnvFile(); // optional ./.env
  } catch {}
  const config = loadConfig();

  if (!process.argv.includes("--http")) {
    await createServer(config).connect(new StdioServerTransport());
    return;
  }

  const port = Number(arg("--port") ?? process.env.PORT ?? 8787);
  const host = arg("--host") ?? "127.0.0.1";
  const allowedHosts = process.env.STEAMWORKS_MCP_ALLOWED_HOSTS?.split(",").map((h) => h.trim()).filter(Boolean);
  const app = createMcpExpressApp({ host, ...(allowedHosts?.length ? { allowedHosts } : {}) });

  if (!config.httpToken) {
    console.error(
      "[steamworks-mcp] WARNING: STEAMWORKS_MCP_TOKEN is not set. Anyone who can reach this port can read and write files under " +
        config.workspaceRoot,
    );
  }

  const requireToken = (req: Request, res: Response, next: NextFunction) => {
    if (!config.httpToken) return next();
    const header = req.headers.authorization ?? "";
    const queryToken = typeof req.query.token === "string" ? req.query.token : undefined;
    if (header === `Bearer ${config.httpToken}` || queryToken === config.httpToken) return next();
    res.status(401).json({ jsonrpc: "2.0", error: { code: -32001, message: "Unauthorized" }, id: null });
  };

  const transports = new Map<string, StreamableHTTPServerTransport>();

  app.all("/mcp", requireToken, async (req: Request, res: Response) => {
    const sessionId = req.headers["mcp-session-id"];
    let transport = typeof sessionId === "string" ? transports.get(sessionId) : undefined;

    if (!transport) {
      if (req.method !== "POST") {
        res.status(400).json({ jsonrpc: "2.0", error: { code: -32000, message: "No valid session" }, id: null });
        return;
      }
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (id) => {
          transports.set(id, transport!);
        },
      });
      transport.onclose = () => {
        if (transport!.sessionId) transports.delete(transport!.sessionId);
      };
      await createServer(config).connect(transport);
    }
    await transport.handleRequest(req, res, req.body);
  });

  app.get("/healthz", (_req: Request, res: Response) => {
    res.json({ ok: true });
  });

  app.listen(port, host, () => {
    console.error(`[steamworks-mcp] HTTP transport listening on http://${host}:${port}/mcp (workspace: ${config.workspaceRoot})`);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
