// Starts the server in HTTP mode and checks auth + a tool call with the official MCP client.
// Usage: npm run build && node scripts/smoke-http.mjs
import { spawn } from "node:child_process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const port = 8799;
const token = "smoke-test-token";
const child = spawn(process.execPath, ["dist/index.js", "--http", "--port", String(port)], {
  env: { ...process.env, STEAMWORKS_MCP_TOKEN: token },
  stdio: ["ignore", "inherit", "inherit"],
});
await new Promise((r) => setTimeout(r, 1500));
const url = new URL(`http://127.0.0.1:${port}/mcp`);

try {
  const unauth = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
  console.log("without token:", unauth.status);

  const client = new Client({ name: "smoke", version: "0" });
  await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers: { authorization: `Bearer ${token}` } } }));
  const { tools } = await client.listTools();
  console.log("with token: tools =", tools.length);
  const res = await client.callTool({ name: "steam_languages", arguments: {} });
  console.log("steam_languages ->", JSON.parse(res.content[0].text).length, "languages");
  await client.close();
} finally {
  child.kill();
}
