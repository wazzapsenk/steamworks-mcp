// Calls one steamworks-mcp tool in-process, exactly as an MCP client would.
// Usage: npx tsx scripts/live/mcp-call.ts <tool> '<json args>'   (or @file.json)
import "./root.js";
import fs from "node:fs";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { closeBrowser } from "../../src/browser/session.js";
import { loadConfig } from "../../src/config.js";
import { createServer } from "../../src/server.js";

const [tool, json] = process.argv.slice(2);
const server = createServer(loadConfig());
const [a, b] = InMemoryTransport.createLinkedPair();
const client = new Client({ name: "live", version: "0" });
await Promise.all([server.connect(a), client.connect(b)]);
try {
  const res = (await client.callTool({ name: tool!, arguments: JSON.parse(json?.startsWith("@") ? fs.readFileSync(json.slice(1), "utf8") : (json ?? "{}")) }, undefined, { timeout: 300_000 })) as any;
  console.log(res.isError ? "ERROR:" : "OK:", res.content.map((c: any) => c.text ?? `[${c.type}]`).join("\n"));
} finally {
  await closeBrowser();
  await client.close();
}
