import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { bbcodeTagSignature, localizationStatus, pendingTranslations, setTranslations } from "../src/localization/store.js";
import { ManifestSchema } from "../src/project/manifest.js";
import { projectPaths } from "../src/project/paths.js";

const manifest = ManifestSchema.parse({
  name: "Test",
  targetLanguages: ["turkish"],
  store: { shortDescription: "Short.", about: "[h2]Title[/h2]\n[list][*] One[/list]" },
  achievements: [{ id: "ACH_A", name: "A", description: "Do A." }],
});

let root: string;
beforeEach(async () => {
  root = await fs.mkdtemp(path.join(os.tmpdir(), "swmcp-"));
});

describe("localization", () => {
  it("lists every translatable text as pending for a new language", async () => {
    const p = projectPaths(root, ".");
    const pending = await pendingTranslations(manifest, p, "turkish");
    expect(pending.map((e) => e.key)).toEqual(["store.shortDescription", "store.about", "achievements.ACH_A.name", "achievements.ACH_A.description"]);
  });

  it("saves translations, rejects broken BBCode and detects stale text", async () => {
    const p = projectPaths(root, ".");
    const res = await setTranslations(manifest, p, "turkish", [
      { key: "store.shortDescription", text: "Kısa." },
      { key: "store.about", text: "[h2]Başlık[/h2] eksik liste" },
      { key: "nope", text: "x" },
    ]);
    expect(res.saved).toEqual(["store.shortDescription"]);
    expect(res.rejected.map((r) => r.key)).toEqual(["store.about", "nope"]);

    const changed = ManifestSchema.parse({ ...manifest, store: { ...manifest.store, shortDescription: "Changed." } });
    const [status] = await localizationStatus(changed, p);
    expect(status!.stale).toEqual(["store.shortDescription"]);
    expect(status!.missing).toHaveLength(3);
  });

  it("refuses paths outside the workspace root", () => {
    expect(() => projectPaths(root, "../elsewhere")).toThrow(/outside the workspace root/);
  });

  it("builds BBCode signatures ignoring attributes", () => {
    expect(bbcodeTagSignature("[url=https://x]a[/url] [b]b[/b]")).toBe("url,/url,b,/b");
  });
});
