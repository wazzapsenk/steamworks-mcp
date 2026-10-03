// Import first: makes the repository root the working directory, so `.env`, `.steamworks-mcp/` and
// `tests/fixtures/` resolve the same way wherever the live scripts are started from.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

let dir = path.dirname(fileURLToPath(import.meta.url));
while (!fs.existsSync(path.join(dir, ".git"))) {
  const parent = path.dirname(dir);
  if (parent === dir) throw new Error("Could not find the repository root (.git) above scripts/live.");
  dir = parent;
}
process.chdir(dir);
