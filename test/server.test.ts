import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import sharp from "sharp";
import { beforeAll, describe, expect, it } from "vitest";
import { createServer } from "../src/server.js";

let client: Client;
let root: string;

const call = async (name: string, args: Record<string, unknown> = {}) => {
  const res = (await client.callTool({ name, arguments: args })) as { isError?: boolean; content: { type: string; text?: string }[] };
  const text = res.content[0]?.text ?? "";
  if (res.isError) throw new Error(text);
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
};

beforeAll(async () => {
  root = await fs.mkdtemp(path.join(os.tmpdir(), "swmcp-e2e-"));
  await fs.cp(path.resolve("examples/demo-game"), path.join(root, "demo"), { recursive: true });
  const server = createServer({ publisherKey: undefined, workspaceRoot: root, httpToken: undefined, browserProfileDir: path.join(root, ".profile") });
  const [a, b] = InMemoryTransport.createLinkedPair();
  client = new Client({ name: "test", version: "0" });
  await Promise.all([server.connect(a), client.connect(b)]);
});

describe("steamworks-mcp over MCP", () => {
  it("exposes the tools", async () => {
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name)).toEqual(
      expect.arrayContaining(["project_validate", "localization_pending", "localization_set", "assets_generate", "export_bundle", "steamworks_open"]),
    );
  });

  it("validates the demo project (only translation warnings)", async () => {
    const res = await call("project_validate", { projectDir: "demo" });
    expect(res.errors).toEqual([]);
    expect(res.warnings.every((w: { area: string }) => w.area === "localization")).toBe(true);
  });

  it("runs the translate loop", async () => {
    const pending = await call("localization_pending", { projectDir: "demo", language: "turkish" });
    expect(pending.count).toBe(6);
    const res = await call("localization_set", {
      projectDir: "demo",
      language: "turkish",
      translations: pending.entries.map((e: { key: string; text: string }) => ({ key: e.key, text: `TR ${e.text}` })),
    });
    expect(res.saved).toHaveLength(6);
    const status = await call("localization_status", { projectDir: "demo", languages: ["turkish"] });
    expect(status[0]).toMatchObject({ translated: 6, missing: 0, stale: 0 });
  });

  it("generates every asset at the exact size", async () => {
    const results = await call("assets_generate", { projectDir: "demo" });
    for (const r of results) {
      expect(r.status).toBe("generated");
      const meta = await sharp(r.file).metadata();
      if (r.id === "library_logo") expect(meta.width === 1280 || meta.height === 720).toBe(true);
      else expect(`${meta.width}x${meta.height}`).toBe(r.size);
    }
  });

  it("prepares achievement icons and exports the bundle", async () => {
    const icons = await call("achievement_icons_prepare", { projectDir: "demo" });
    expect(icons).toHaveLength(2);
    expect((await sharp(icons[0].locked).metadata()).width).toBe(256);

    const { files } = await call("export_bundle", { projectDir: "demo" });
    const checklist = await fs.readFile(files.find((f: string) => f.endsWith("STEAMWORKS_CHECKLIST.md")), "utf8");
    expect(checklist).toContain("ACH_SPEEDRUN");
    expect(checklist).toContain("WinAppDataLocalLow");
    const csv = await fs.readFile(files.find((f: string) => f.endsWith(".csv")), "utf8");
    expect(csv).toContain("TR First Blood");
  });

  it("refuses Web API calls without a key and paths outside the root", async () => {
    await expect(call("steam_achievements_diff", { projectDir: "demo" })).rejects.toThrow(/STEAMWORKS_PUBLISHER_KEY/);
    await expect(call("project_read", { projectDir: "../" })).rejects.toThrow(/outside the workspace root/);
  });
});
