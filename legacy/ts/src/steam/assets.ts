/**
 * Store and library graphical asset specs.
 * Sources: https://partner.steamgames.com/doc/store/assets/standard
 *          https://partner.steamgames.com/doc/store/assets/libraryassets
 *          https://partner.steamgames.com/doc/store/assets/community
 */
export type Composition =
  /** Key art cropped to fill, logo centred on top. */
  | "capsule"
  /** Key art cropped to fill, no logo (library hero, page background). */
  | "art-only"
  /** Logo alone on a transparent canvas. */
  | "logo-only"
  /** Logo (or key art) squeezed into a square icon. */
  | "icon";

export interface AssetSpec {
  id: string;
  label: string;
  width: number;
  height: number;
  format: "png" | "jpg";
  composition: Composition;
  /** Logo width as a fraction of the canvas width (for "capsule"). */
  logoScale?: number;
  required: boolean;
  notes?: string;
}

export const ASSET_SPECS: readonly AssetSpec[] = [
  { id: "header_capsule", label: "Header capsule", width: 920, height: 430, format: "png", composition: "capsule", logoScale: 0.6, required: true },
  { id: "small_capsule", label: "Small capsule", width: 462, height: 174, format: "png", composition: "capsule", logoScale: 0.75, required: true, notes: "Logo must stay readable at 120x45, so it fills most of the capsule." },
  { id: "main_capsule", label: "Main capsule", width: 1232, height: 706, format: "png", composition: "capsule", logoScale: 0.55, required: true },
  { id: "vertical_capsule", label: "Vertical capsule", width: 748, height: 896, format: "png", composition: "capsule", logoScale: 0.8, required: true },
  { id: "page_background", label: "Page background", width: 1438, height: 810, format: "png", composition: "art-only", required: false, notes: "Optional. Steam darkens it; keep it low-contrast." },
  { id: "library_capsule", label: "Library capsule", width: 600, height: 900, format: "png", composition: "capsule", logoScale: 0.8, required: true },
  { id: "library_header", label: "Library header", width: 920, height: 430, format: "png", composition: "capsule", logoScale: 0.6, required: false, notes: "Defaults to the header capsule when not uploaded." },
  { id: "library_hero", label: "Library hero", width: 3840, height: 1240, format: "png", composition: "art-only", required: true, notes: "No logo or text; the library logo is placed over it. Keep the centre 860x380 free of important detail." },
  { id: "library_logo", label: "Library logo", width: 1280, height: 720, format: "png", composition: "logo-only", required: true, notes: "Transparent PNG, 1280 wide and/or 720 tall. Position it with Steamworks' preview tool." },
  { id: "shortcut_icon", label: "Shortcut icon", width: 256, height: 256, format: "png", composition: "icon", required: true, notes: "ICO or PNG; macOS needs a separate ICNS." },
  { id: "app_icon", label: "App icon", width: 184, height: 184, format: "jpg", composition: "icon", required: true },
];

export const SCREENSHOT_MIN = { width: 1920, height: 1080, count: 5 } as const;

/** Achievement icon size used by this tool. Valve doesn't document one; 256x256 JPG is accepted and widely used. */
export const ACHIEVEMENT_ICON_SIZE = 256;
