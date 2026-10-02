import path from "node:path";

export const MANIFEST_FILE = "steamworks.yaml";
export const LOCALIZATION_DIR = "localization";
export const OUTPUT_DIR = "steamworks-out";

export class PathError extends Error {}

/**
 * Resolves a user-supplied path against the workspace root and refuses anything that escapes it.
 */
export function resolveInside(root: string, userPath: string): string {
  const resolved = path.resolve(root, userPath);
  const rel = path.relative(root, resolved);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new PathError(`Path "${userPath}" is outside the workspace root (${root}). Set STEAMWORKS_MCP_ROOT to allow it.`);
  }
  return resolved;
}

export interface ProjectPaths {
  dir: string;
  manifest: string;
  localizationDir: string;
  outputDir: string;
  /** Resolves a path written inside the manifest (relative to the project folder). */
  file(rel: string): string;
}

export function projectPaths(root: string, projectDir: string): ProjectPaths {
  const dir = resolveInside(root, projectDir);
  return {
    dir,
    manifest: path.join(dir, MANIFEST_FILE),
    localizationDir: path.join(dir, LOCALIZATION_DIR),
    outputDir: path.join(dir, OUTPUT_DIR),
    file: (rel: string) => resolveInside(dir, rel),
  };
}
