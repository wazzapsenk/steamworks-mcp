import { describe, expect, it } from "vitest";
import { marshalLocalized } from "../src/browser/achievements.js";
import { diffStoreLocalization } from "../src/export/storeLocalization.js";
import { ManifestSchema } from "../src/project/manifest.js";
import { planAchievements, type DesiredAchievement } from "../src/sync/achievements.js";
import { planCloud, planIsEmpty } from "../src/sync/cloud.js";

const desired = (id: string, extra: Partial<DesiredAchievement> = {}): DesiredAchievement => ({
  id,
  name: { english: `${id} name`, turkish: `${id} ad` },
  description: { english: `${id} desc` },
  hidden: false,
  icon: "a.png",
  source: {} as any,
  ...extra,
});

describe("achievement planning", () => {
  it("creates missing, updates changed languages, keeps Steam's other languages, never deletes", () => {
    const steam = [
      { stat_id: 1, bit_id: 0, api_name: "A", display_name: { english: "A name", german: "A Name" }, description: "A desc", hidden: "0", permission: 0, icon: "x", icon_gray: "y", progress: false },
      { stat_id: 1, bit_id: 1, api_name: "OLD", display_name: "Old", description: "", hidden: "0", permission: 0, progress: false },
    ];
    const plan = planAchievements([desired("A"), desired("B")], steam as any, "missing");
    const a = plan.changes.find((c) => c.id === "A")!;
    expect(a.action).toBe("update");
    expect(a.changes).toEqual(['name[turkish]: "" → "A ad"']);
    expect(a.next.name).toEqual({ english: "A name", german: "A Name", turkish: "A ad" });
    expect(plan.changes.find((c) => c.id === "B")!.action).toBe("create");
    expect(plan.onlyInSteam).toEqual(["OLD"]);
  });

  it("skips achievements bound to a progress stat", () => {
    const steam = [{ stat_id: 1, bit_id: 0, api_name: "A", display_name: "x", description: "", hidden: "0", permission: 0, progress: { min: 0 } }];
    expect(planAchievements([desired("A")], steam as any, "none").changes[0]!.action).toBe("skip");
  });

  it("marshals localized fields like the Steamworks page does", () => {
    expect(marshalLocalized({ english: "Hi", turkish: "" })).toBe('"Hi"');
    expect(marshalLocalized({ english: "Hi", turkish: "Selam" })).toBe('{"english":"Hi","turkish":"Selam"}');
  });
});

describe("cloud planning", () => {
  const m = ManifestSchema.parse({
    name: "T",
    cloud: {
      byteQuota: 100,
      fileQuota: 10,
      autoCloud: { roots: [{ root: "App Install Directory", subdirectory: "saves", pattern: "*.sav", os: "all" }] },
    },
  });
  const cur = { byteQuota: 100, fileQuota: 10, sharedAppId: 0, developersOnly: true, syncOnSuspend: false, roots: [], overrides: [] };

  it("maps manifest names to Steamworks values and leaves unmanaged flags alone", () => {
    const plan = planCloud(m, cur, false);
    expect(plan.ufs).toBeUndefined();
    expect(plan.setRoots[0]!.row).toMatchObject({ root: "gameinstall", path: "saves", os: "", recursive: false });
  });

  it("is empty when Steam matches, and reports extra rows unless removeExtra", () => {
    const synced = { ...cur, roots: [{ index: 0, root: "gameinstall", path: "saves", pattern: "*.sav", os: "", recursive: false }, { index: 1, root: "WinMyDocuments", path: "x", pattern: "*", os: "", recursive: false }] };
    const keep = planCloud(m, synced, false);
    expect(planIsEmpty(keep)).toBe(true);
    expect(keep.extraRoots).toHaveLength(1);
    expect(planCloud(m, synced, true).deleteRoots.map((r) => r.index)).toEqual([1]);
  });
});

describe("store localization diff", () => {
  it("ignores Steam's [p] wrapping", () => {
    const cur = { itemid: "1", languages: { english: { "app[content][about]": "[p]Hello[/p]" } } };
    const next = { itemid: "1", languages: { english: { "app[content][about]": "Hello" }, turkish: { "app[content][about]": "Merhaba" } } };
    expect(diffStoreLocalization(cur, next).map((c) => c.language)).toEqual(["turkish"]);
  });
});
