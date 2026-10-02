import fs from "node:fs/promises";
import YAML from "yaml";
import { z } from "zod";
import type { ProjectPaths } from "./paths.js";

const RequirementsSchema = z
  .object({
    os: z.string().optional(),
    processor: z.string().optional(),
    memory: z.string().optional(),
    graphics: z.string().optional(),
    directx: z.string().optional(),
    network: z.string().optional(),
    storage: z.string().optional(),
    soundCard: z.string().optional(),
    vrSupport: z.string().optional(),
    additionalNotes: z.string().optional(),
  })
  .strict();

const PlatformRequirementsSchema = z
  .object({ minimum: RequirementsSchema.optional(), recommended: RequirementsSchema.optional() })
  .strict();

const LanguageSupportSchema = z
  .object({
    interface: z.boolean().default(true),
    fullAudio: z.boolean().default(false),
    subtitles: z.boolean().default(false),
  })
  .strict();

export const AchievementSchema = z
  .object({
    /** API name used in code, e.g. ACH_FIRST_WIN. */
    id: z.string().regex(/^[A-Za-z0-9_]+$/, "Achievement ids may only contain letters, digits and underscores"),
    name: z.string().min(1),
    description: z.string().default(""),
    hidden: z.boolean().default(false),
    /** Unlocked icon, relative to the project folder. Any size/format; it is converted to 256x256 JPG. */
    icon: z.string().optional(),
    /** Locked icon. When omitted, a greyscale version of `icon` is generated. */
    iconLocked: z.string().optional(),
    /** Optional stat that drives a progress bar, e.g. { stat: "WINS", min: 0, max: 10 }. */
    progress: z.object({ stat: z.string(), min: z.number().default(0), max: z.number() }).strict().optional(),
  })
  .strict();

export const AutoCloudRootSchema = z
  .object({
    root: z.string(),
    subdirectory: z.string().default(""),
    pattern: z.string().default("*"),
    os: z.enum(["all", "windows", "macos", "linux"]).default("all"),
    recursive: z.boolean().default(false),
  })
  .strict();

export const RootOverrideSchema = z
  .object({
    originalRoot: z.string(),
    os: z.enum(["windows", "macos", "linux"]),
    newRoot: z.string(),
    addOrReplacePath: z.string().default(""),
    replace: z.boolean().default(false),
  })
  .strict();

export const LaunchOptionSchema = z
  .object({
    executable: z.string(),
    arguments: z.string().default(""),
    workingDir: z.string().default(""),
    description: z.string().default(""),
    os: z.enum(["all", "windows", "macos", "linux"]).default("windows"),
    arch: z.enum(["all", "32", "64"]).default("64"),
    betaKey: z.string().default(""),
  })
  .strict();

export const ManifestSchema = z
  .object({
    appId: z.number().int().positive().optional(),
    name: z.string().min(1),
    /** Steam API language code of the text written in this file (e.g. "english"). */
    sourceLanguage: z.string().default("english"),
    /** Steam API language codes to localize into (e.g. ["turkish", "german", "schinese"]). */
    targetLanguages: z.array(z.string()).default([]),
    store: z
      .object({
        shortDescription: z.string().default(""),
        /** "About This Game" section, Steam BBCode. */
        about: z.string().default(""),
        tags: z.array(z.string()).default([]),
        /** Languages the game itself supports (shown in the store's language table). */
        supportedLanguages: z.record(z.string(), LanguageSupportSchema).default({}),
        systemRequirements: z
          .object({
            windows: PlatformRequirementsSchema.optional(),
            macos: PlatformRequirementsSchema.optional(),
            linux: PlatformRequirementsSchema.optional(),
          })
          .strict()
          .default({}),
        /** Folder with screenshots, relative to the project folder. */
        screenshotsDir: z.string().default("store/screenshots"),
        art: z
          .object({
            /** Large textless key art used to compose capsules, library hero and page background. */
            keyArt: z.string().optional(),
            /** Transparent logo PNG placed on capsules and used as the library logo. */
            logo: z.string().optional(),
            /** Optional hand-made capsules that override generated ones, keyed by asset id (see `assets_list_specs`). */
            overrides: z.record(z.string(), z.string()).default({}),
          })
          .strict()
          .default({ overrides: {} }),
      })
      .strict()
      .default({
        shortDescription: "",
        about: "",
        tags: [],
        supportedLanguages: {},
        systemRequirements: {},
        screenshotsDir: "store/screenshots",
        art: { overrides: {} },
      }),
    achievements: z.array(AchievementSchema).default([]),
    cloud: z
      .object({
        byteQuota: z.number().int().nonnegative().optional(),
        fileQuota: z.number().int().nonnegative().optional(),
        autoCloud: z
          .object({
            roots: z.array(AutoCloudRootSchema).default([]),
            rootOverrides: z.array(RootOverrideSchema).default([]),
          })
          .strict()
          .optional(),
      })
      .strict()
      .optional(),
    app: z
      .object({
        installFolder: z.string().optional(),
        launchOptions: z.array(LaunchOptionSchema).default([]),
      })
      .strict()
      .optional(),
  })
  .strict();

export type Manifest = z.infer<typeof ManifestSchema>;
export type Achievement = z.infer<typeof AchievementSchema>;

export class ManifestError extends Error {}

export async function readManifest(paths: ProjectPaths): Promise<Manifest> {
  let raw: string;
  try {
    raw = await fs.readFile(paths.manifest, "utf8");
  } catch {
    throw new ManifestError(`No steamworks.yaml found in ${paths.dir}. Run project_init first.`);
  }
  const parsed = ManifestSchema.safeParse(YAML.parse(raw) ?? {});
  if (!parsed.success) {
    const issues = parsed.error.issues.map((i) => `  - ${i.path.join(".") || "(root)"}: ${i.message}`).join("\n");
    throw new ManifestError(`steamworks.yaml is invalid:\n${issues}`);
  }
  const ids = new Set<string>();
  for (const a of parsed.data.achievements) {
    if (ids.has(a.id)) throw new ManifestError(`Duplicate achievement id "${a.id}" in steamworks.yaml`);
    ids.add(a.id);
  }
  return parsed.data;
}
