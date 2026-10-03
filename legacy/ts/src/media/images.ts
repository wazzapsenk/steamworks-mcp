import fs from "node:fs/promises";
import path from "node:path";
import sharp, { type Sharp } from "sharp";
import type { Manifest } from "../project/manifest.js";
import type { ProjectPaths } from "../project/paths.js";
import { ACHIEVEMENT_ICON_SIZE, ASSET_SPECS, type AssetSpec, SCREENSHOT_MIN } from "../steam/assets.js";
import { isApiLanguage } from "../steam/languages.js";

const IMAGE_EXT = new Set([".png", ".jpg", ".jpeg", ".webp"]);

export interface AssetResult {
  id: string;
  label: string;
  status: "generated" | "override" | "skipped";
  file?: string;
  size?: string;
  notes: string[];
}

async function encode(img: Sharp, spec: { format: "png" | "jpg" }, file: string) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  if (spec.format === "jpg") await img.flatten({ background: "#000" }).jpeg({ quality: 92 }).toFile(file);
  else await img.png().toFile(file);
}

async function compose(spec: AssetSpec, keyArt: string | undefined, logo: string | undefined): Promise<Sharp | string> {
  const { width, height } = spec;
  if (spec.composition === "logo-only") {
    if (!logo) return "needs store.art.logo";
    return sharp(logo).resize({ width, height, fit: "inside" });
  }
  if (spec.composition === "icon") {
    if (logo) {
      const inner = Math.round(width * 0.82);
      const logoBuf = await sharp(logo).resize({ width: inner, height: inner, fit: "inside" }).png().toBuffer();
      const base = keyArt
        ? sharp(keyArt).resize(width, height, { fit: "cover", position: "attention" })
        : sharp({ create: { width, height, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } });
      return sharp(await base.png().toBuffer()).composite([{ input: logoBuf, gravity: "center" }]);
    }
    if (!keyArt) return "needs store.art.keyArt or store.art.logo";
    return sharp(keyArt).resize(width, height, { fit: "cover", position: "attention" });
  }
  if (!keyArt) return "needs store.art.keyArt";
  const art = sharp(keyArt).resize(width, height, { fit: "cover", position: "attention" });
  if (spec.composition === "art-only") return art;
  if (!logo) return "needs store.art.logo (capsules must show the game's name)";
  const maxW = Math.round(width * (spec.logoScale ?? 0.6));
  const maxH = Math.round(height * 0.7);
  const logoBuf = await sharp(logo).resize({ width: maxW, height: maxH, fit: "inside" }).png().toBuffer();
  return sharp(await art.png().toBuffer()).composite([{ input: logoBuf, gravity: "center" }]);
}

/** Builds every store/library asset from one key art + logo, honouring hand-made overrides. */
export async function generateStoreAssets(m: Manifest, paths: ProjectPaths, only?: string[]): Promise<AssetResult[]> {
  const keyArt = m.store.art.keyArt ? paths.file(m.store.art.keyArt) : undefined;
  const logo = m.store.art.logo ? paths.file(m.store.art.logo) : undefined;
  const results: AssetResult[] = [];
  const keyMeta = keyArt ? await sharp(keyArt).metadata() : undefined;

  for (const spec of ASSET_SPECS) {
    if (only && !only.includes(spec.id)) continue;
    const notes: string[] = spec.notes ? [spec.notes] : [];
    const out = path.join(paths.outputDir, "assets", `${spec.id}.${spec.format}`);
    const override = m.store.art.overrides[spec.id];

    if (override) {
      const src = paths.file(override);
      const meta = await sharp(src).metadata();
      if (meta.width !== spec.width || meta.height !== spec.height) {
        if (spec.composition !== "logo-only") notes.push(`Override is ${meta.width}x${meta.height}; resized to ${spec.width}x${spec.height}.`);
      }
      const img = spec.composition === "logo-only"
        ? sharp(src).resize({ width: spec.width, height: spec.height, fit: "inside" })
        : sharp(src).resize(spec.width, spec.height, { fit: "cover", position: "attention" });
      await encode(img, spec, out);
      results.push({ id: spec.id, label: spec.label, status: "override", file: out, size: `${spec.width}x${spec.height}`, notes });
      continue;
    }

    const composed = await compose(spec, keyArt, logo);
    if (typeof composed === "string") {
      results.push({ id: spec.id, label: spec.label, status: "skipped", notes: [composed, ...notes] });
      continue;
    }
    if (keyMeta?.width && keyMeta.height && spec.composition !== "logo-only" && (keyMeta.width < spec.width || keyMeta.height < spec.height)) {
      notes.push(`Key art is ${keyMeta.width}x${keyMeta.height}, smaller than ${spec.width}x${spec.height}; the result is upscaled and may look soft.`);
    }
    await encode(composed, spec, out);
    results.push({ id: spec.id, label: spec.label, status: "generated", file: out, size: `${spec.width}x${spec.height}`, notes });
  }
  return results;
}

export interface AchievementIconResult {
  id: string;
  unlocked?: string;
  locked?: string;
  notes: string[];
}

/** Converts achievement icons to 256x256 JPGs and derives greyscale "locked" icons when none are given. */
export async function prepareAchievementIcons(m: Manifest, paths: ProjectPaths, size = ACHIEVEMENT_ICON_SIZE): Promise<AchievementIconResult[]> {
  const dir = path.join(paths.outputDir, "achievements");
  const results: AchievementIconResult[] = [];
  for (const a of m.achievements) {
    const notes: string[] = [];
    if (!a.icon) {
      results.push({ id: a.id, notes: ["No icon set in steamworks.yaml."] });
      continue;
    }
    const src = paths.file(a.icon);
    const unlocked = path.join(dir, `${a.id}.jpg`);
    const locked = path.join(dir, `${a.id}_locked.jpg`);
    await encode(sharp(src).resize(size, size, { fit: "cover" }), { format: "jpg" }, unlocked);
    if (a.iconLocked) {
      await encode(sharp(paths.file(a.iconLocked)).resize(size, size, { fit: "cover" }), { format: "jpg" }, locked);
    } else {
      await encode(sharp(src).resize(size, size, { fit: "cover" }).greyscale().modulate({ brightness: 0.55 }), { format: "jpg" }, locked);
      notes.push("Locked icon generated (greyscale, darkened).");
    }
    results.push({ id: a.id, unlocked, locked, notes });
  }
  return results;
}

export interface ScreenshotReport {
  dir: string;
  count: number;
  minimumCount: number;
  screenshots: { file: string; size: string; language?: string; problems: string[] }[];
  problems: string[];
}

/** Checks screenshots against Steam's rules. Files named like `shot1_japanese.png` count as localized variants. */
export async function checkScreenshots(m: Manifest, paths: ProjectPaths): Promise<ScreenshotReport> {
  const dir = paths.file(m.store.screenshotsDir);
  let files: string[] = [];
  try {
    files = (await fs.readdir(dir)).filter((f) => IMAGE_EXT.has(path.extname(f).toLowerCase())).sort();
  } catch {
    return { dir, count: 0, minimumCount: SCREENSHOT_MIN.count, screenshots: [], problems: [`Folder not found: ${m.store.screenshotsDir}`] };
  }
  const shots: ScreenshotReport["screenshots"] = [];
  let base = 0;
  for (const f of files) {
    const meta = await sharp(path.join(dir, f)).metadata();
    const w = meta.width ?? 0;
    const h = meta.height ?? 0;
    const problems: string[] = [];
    if (w < SCREENSHOT_MIN.width || h < SCREENSHOT_MIN.height) problems.push(`Below ${SCREENSHOT_MIN.width}x${SCREENSHOT_MIN.height}.`);
    if (h > 0 && Math.abs(w / h - 16 / 9) > 0.01) problems.push("Not 16:9.");
    const suffix = path.basename(f, path.extname(f)).split("_").pop() ?? "";
    const language = isApiLanguage(suffix) ? suffix : undefined;
    if (!language) base++;
    shots.push({ file: f, size: `${w}x${h}`, ...(language ? { language } : {}), problems });
  }
  const problems: string[] = [];
  if (base < SCREENSHOT_MIN.count) problems.push(`Steam requires at least ${SCREENSHOT_MIN.count} screenshots; found ${base}.`);
  return { dir, count: base, minimumCount: SCREENSHOT_MIN.count, screenshots: shots, problems };
}
