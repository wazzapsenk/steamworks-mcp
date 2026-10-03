// Turns raw recordings (.steamworks-mcp/recordings/raw, gitignored) into committable fixtures:
//   tests/fixtures/steamworks/<area>/<step>.har   sanitized HAR 1.2
//   tests/fixtures/steamworks/<area>/<step>.json  the JSON request/response pairs of that step, easy to read in tests
//
// Usage: npx tsx scripts/live/sanitize.ts <recordedAppId>
//
// What is replaced or removed:
//   - Cookie / Set-Cookie / Authorization headers (removed)
//   - steamLoginSecure, access_token, webapi_token and key= values -> REDACTED
//   - every hex run of 24+ chars (session ids, keys, CDN image hashes) -> "ffff…NNNN" of the same length
//   - the recorded app id -> 1000000, its store item id -> 2000000, other app ids of the account -> 1000001+
//   - SteamID64s -> 76561190000000001, the derived 32-bit account id -> 100000001
//   - SANITIZE_DENYLIST terms (from .env): app names -> "ExampleGame", everything else -> "Redacted"
//   - e-mail addresses -> user@example.com; antivirus script injections (removed)
//   - Turkish-language sample text (the public repo is English-only) -> "[turkish text removed]"
// The script ends with the same checks as tests/test_fixture_sanitization.py and exits 1 on any finding.
import fs from "node:fs/promises";
import path from "node:path";
import type { HarEntry, HarFile } from "./har.js";

try {
  process.loadEnvFile();
} catch {}

const RAW = path.resolve(".steamworks-mcp/recordings/raw");
const OUT = path.resolve("tests/fixtures/steamworks");
const APPS_FILE = path.resolve(".steamworks-mcp/live/apps.json");
/** Documents bigger than this keep their body only in the step listed in BIG_DOC_HOME; elsewhere it is omitted. */
const BIG_DOC = 300_000;
const BIG_DOC_HOME = "store/read";

const TURKISH_CHARS = /[ıİşŞğĞ]|\\u0(?:131|130|15[fF]|15[eE]|11[fF]|11[eE])|&#(?:305|304|351|350|287|286);/;
const TR_REMOVED = "[turkish text removed]";

interface Replacements {
  numbers: [RegExp, string][];
  terms: [RegExp, string][];
  secrets: string[];
}

async function listHars(dir: string): Promise<string[]> {
  const out: string[] = [];
  for (const e of await fs.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...(await listHars(p)));
    else if (e.name.endsWith(".har")) out.push(p);
  }
  return out.sort();
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const digits = (n: string) => new RegExp(`(?<![0-9])${escapeRe(n)}(?![0-9])`, "g");

async function buildReplacements(appId: string, raws: string[]): Promise<Replacements> {
  const all = raws.join("\n");
  const numbers: [RegExp, string][] = [];
  numbers.push([digits(appId), "1000000"]);
  const itemId = /\/admin\/game\/edit\/(\d+)/.exec(all)?.[1];
  if (itemId) numbers.push([digits(itemId), "2000000"]);

  const apps: { appId: number; name: string }[] = JSON.parse(await fs.readFile(APPS_FILE, "utf8").catch(() => "[]"));
  let next = 1000001;
  for (const a of apps) if (String(a.appId) !== appId) numbers.push([digits(String(a.appId)), String(next++)]);

  // Steamworks partner (publisher) id, shown in links, data attributes and page globals.
  for (const m of all.matchAll(/(?:partnerid=|g_nPrimaryPublisher = |data-publisherid=\\?"|publisherid=\\?")(\d{3,})/g)) {
    if (m[1] !== "0" && !numbers.some(([re]) => re.source.includes(m[1]!))) numbers.push([digits(m[1]!), "900000"]);
  }
  for (const s64 of new Set(all.match(/7656119\d{10}/g) ?? [])) {
    numbers.push([digits(s64), "76561190000000001"]);
    numbers.push([digits(String(BigInt(s64) - 76561197960265728n)), "100000001"]);
  }

  const appNames = new Set(apps.map((a) => a.name.toLowerCase()));
  const deny = (process.env.SANITIZE_DENYLIST ?? "")
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t.length >= 3)
    .sort((a, b) => b.length - a.length);
  const terms: [RegExp, string][] = [];
  for (const t of deny) {
    const isApp = [...appNames].some((n) => n.includes(t.toLowerCase()));
    const variants = new Set([t, encodeURIComponent(t), t.replace(/ /g, "+"), t.replace(/\\/g, "\\\\"), t.replace(/\//g, "\\/")]);
    for (const v of variants) terms.push([new RegExp(escapeRe(v), "gi"), isApp ? "ExampleGame" : "Redacted"]);
  }

  // Values that must never survive, whatever their shape.
  const secrets = new Set<string>();
  for (const m of all.matchAll(/steamLoginSecure=([^;"\s\\]+)/g)) secrets.add(m[1]!);
  for (const m of all.matchAll(/(?:access_token|webapi_token)(?:=|\\?":\\?")([A-Za-z0-9_\-.%]{16,})/g)) secrets.add(m[1]!);
  return { numbers, terms, secrets: [...secrets].filter((s) => s.length >= 8) };
}

class HexMap {
  private map = new Map<string, string>();
  get(hex: string): string {
    const key = hex.toLowerCase();
    let v = this.map.get(key);
    if (!v) {
      const n = String(this.map.size + 1).padStart(4, "0");
      v = "f".repeat(Math.max(0, hex.length - 4)) + n;
      this.map.set(key, v);
    }
    return v;
  }
}

/** Replaces Turkish sample text: language-keyed values first, then any remaining run containing Turkish letters. */
function removeTurkish(s: string): string {
  if (!TURKISH_CHARS.test(s) && !/turkish/i.test(s)) return s;
  let out = s;
  // JSON-ish: "turkish":"…"  (also inside HTML-escaped JSON)
  out = out.replace(/("turkish"\s*:\s*)"((?:[^"\\]|\\.)*)"/g, (_m, k) => `${k}"${TR_REMOVED}"`);
  out = out.replace(/(&quot;turkish&quot;\s*:\s*)&quot;(.*?)&quot;/g, (_m, k) => `${k}&quot;${TR_REMOVED}&quot;`);
  // KeyValues / VDF: "turkish"<tab>"…"
  out = out.replace(/("turkish"[ \t]+)"((?:[^"\\\n]|\\.)*)"/g, (_m, k) => `${k}"${TR_REMOVED}"`);
  out = out.replace(/(&quot;turkish&quot;[ \t]+)&quot;(.*?)&quot;/g, (_m, k) => `${k}&quot;${TR_REMOVED}&quot;`);
  // HTML inputs named …[turkish] (either attribute order)
  out = out.replace(/(name="[^"]*\[turkish\][^"]*"[^>]*?value=")([^"]*)(")/g, (_m, a, _v, c) => `${a}${TR_REMOVED}${c}`);
  out = out.replace(/(value=")([^"]*)("[^>]*?name="[^"]*\[turkish\][^"]*")/g, (_m, a, v, c) => (TURKISH_CHARS.test(v) ? `${a}${TR_REMOVED}${c}` : _m));
  // Fallback: any remaining text run with Turkish-specific letters.
  if (TURKISH_CHARS.test(out)) {
    out = out.replace(/(?:[^"<>\n\\&]|\\u[0-9a-fA-F]{4}|&#\d+;)*(?:[ıİşŞğĞ]|\\u0(?:131|130|15f|15e|11f|11e)|&#(?:305|304|351|350|287|286);)(?:[^"<>\n\\&]|\\u[0-9a-fA-F]{4}|&#\d+;)*/gi, TR_REMOVED);
  }
  return out;
}

function scrubText(s: string, r: Replacements, hex: HexMap): string {
  let out = s;
  for (const secret of r.secrets) out = out.split(secret).join("REDACTED");
  out = out.replace(/(steamLoginSecure=)[^;"\s\\]+/g, "$1REDACTED");
  out = out.replace(/((?:access_token|webapi_token)=)[^&"\s\\]+/g, "$1REDACTED");
  out = out.replace(/([?&]key=)(?!<redacted>)[^&"\s\\]+/g, "$1REDACTED");
  // Antivirus/browser-extension script injections (not part of Steamworks pages)
  out = out.replace(/<script[^>]*kaspersky[^>]*>\s*<\/script>/gi, "");
  out = out.replace(/[a-z]+:\/\/[^"'\s;]*kaspersky[^"'\s;]*/gi, "");
  out = out.replace(/[\w.-]*kaspersky[\w.-]*/gi, "");
  out = removeTurkish(out);
  for (const [re, to] of r.numbers) out = out.replace(re, to);
  out = out.replace(/[0-9a-fA-F]{24,}/g, (m) => (/^f+\d{4}$/.test(m) ? m : hex.get(m)));
  for (const [re, to] of r.terms) out = out.replace(re, to);
  out = out.replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, (m) => (/@example\.com$/i.test(m) ? m : "user@example.com"));
  return out;
}

/** Store localization JSON: replace every Turkish value but keep the structure. */
function scrubJsonBody(text: string): string {
  if (!/^\s*[{[]/.test(text) || !/turkish/.test(text)) return text;
  try {
    const walk = (v: any, inTurkish: boolean): any => {
      if (typeof v === "string") return inTurkish && v !== "" ? TR_REMOVED : v;
      if (Array.isArray(v)) return v.map((x) => walk(x, inTurkish));
      if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x, inTurkish || k === "turkish")]));
      return v;
    };
    return JSON.stringify(walk(JSON.parse(text), false));
  } catch {
    return text;
  }
}

const DROP_HEADERS = new Set(["cookie", "set-cookie", "authorization"]);

function sanitizeHar(har: HarFile, step: string, r: Replacements, hex: HexMap): HarFile {
  const entries = har.log.entries.map((e): HarEntry => {
    const copy: HarEntry = JSON.parse(JSON.stringify(e));
    copy.request.headers = copy.request.headers.filter((h) => !DROP_HEADERS.has(h.name.toLowerCase()));
    copy.response.headers = copy.response.headers.filter((h) => !DROP_HEADERS.has(h.name.toLowerCase()));
    if (copy.response.content.text) {
      copy.response.content.text = scrubJsonBody(copy.response.content.text);
      if (copy._resourceType === "document" && copy.response.content.text.length > BIG_DOC && step !== BIG_DOC_HOME) {
        copy.response.content.comment = `document body omitted (${copy.response.content.text.length} chars); the same page is in ${BIG_DOC_HOME}.har`;
        delete copy.response.content.text;
      }
    }
    const st = copy.response.status;
    if (st > 0 && (st < 300 || st >= 400) && copy.response.content.text === undefined && !copy.response.content.comment) {
      copy.response.content.comment = "body not captured: the page navigated away before it could be read";
    }
    if (copy.request.postData?.text) copy.request.postData.text = scrubJsonBody(copy.request.postData.text);
    return copy;
  });
  const walk = (v: any): any => {
    if (typeof v === "string") return scrubText(v, r, hex);
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  return walk({ log: { ...har.log, entries } }) as HarFile;
}

/** JSON exchanges of a step: what Python tests mostly need. */
function extractJson(har: HarFile): unknown[] {
  const out: unknown[] = [];
  for (const e of har.log.entries) {
    const text = e.response.content.text ?? "";
    let response: unknown;
    try {
      response = /^\s*[{[]/.test(text) ? JSON.parse(text) : undefined;
    } catch {
      response = undefined;
    }
    if (response === undefined) continue;
    const u = new URL(e.request.url);
    const post = e.request.postData;
    let request: unknown;
    if (post?.mimeType.includes("application/x-www-form-urlencoded")) request = Object.fromEntries(new URLSearchParams(post.text));
    else if (post) request = { multipart: post.text.length > 4000 ? `${post.text.slice(0, 4000)}…` : post.text };
    out.push({ method: e.request.method, url: `${u.origin}${u.pathname}`, query: Object.fromEntries(u.searchParams), status: e.response.status, request, response });
  }
  return out;
}

/** The checks tests/test_fixture_sanitization.py runs, so problems show up before committing. */
export function findLeaks(text: string, denylist: string[]): string[] {
  const found: string[] = [];
  const check = (label: string, re: RegExp) => {
    const m = re.exec(text);
    if (m) found.push(`${label}: …${text.slice(Math.max(0, m.index - 40), m.index + 40).replace(/\s+/g, " ")}…`);
  };
  check("cookie header", /"name":\s*"(cookie|set-cookie)"/i);
  check("steamLoginSecure value", /steamLoginSecure=(?!REDACTED)[^;"\s\\]/);
  check("token value", /(access_token|webapi_token)=(?!REDACTED)[^&"\s\\]/);
  check("web api key", /[?&]key=(?!REDACTED|<redacted>)[^&"\s\\]/);
  check("hex id", /(?<![0-9a-fA-F])(?!f+\d{4}(?![0-9a-fA-F]))[0-9a-fA-F]{24,}/);
  check("steamid64", /7656119(?!0000000001|7960265728)\d{10}/);
  check("partner id", /(?:partnerid=|g_nPrimaryPublisher = |data-publisherid=\\?"|publisherid=\\?")(?!900000\b|0\b)\d/);
  check("e-mail", /[\w.+-]+@(?!example\.com)[\w-]+\.[\w.-]+/);
  check("turkish text", TURKISH_CHARS);
  check("antivirus injection", /kaspersky/i);
  for (const t of denylist) if (t.length >= 3 && text.toLowerCase().includes(t.toLowerCase())) found.push(`denylisted term #${denylist.indexOf(t) + 1}`);
  return found;
}

async function main(): Promise<void> {
  const appId = process.argv[2];
  if (!appId || !/^\d+$/.test(appId)) {
    console.log("Usage: npx tsx scripts/live/sanitize.ts <recordedAppId>");
    process.exit(1);
  }
  const files = await listHars(RAW);
  const raws = await Promise.all(files.map((f) => fs.readFile(f, "utf8")));
  const r = await buildReplacements(appId, raws);
  const hex = new HexMap();
  const denylist = (process.env.SANITIZE_DENYLIST ?? "").split(",").map((t) => t.trim()).filter(Boolean);
  await fs.mkdir(OUT, { recursive: true });
  for (const e of await fs.readdir(OUT)) await fs.rm(path.join(OUT, e), { recursive: true, force: true });
  let leaks = 0;
  let bytes = 0;
  // Reads first so the big store document stays in store/read.
  const order = (f: string) => (/[\\/]read\.har$/.test(f) ? 0 : 1);
  const sorted = files.map((f, i) => ({ f, raw: raws[i]! })).sort((a, b) => order(a.f) - order(b.f) || a.f.localeCompare(b.f));
  for (const { f, raw } of sorted) {
    const step = path.relative(RAW, f).replace(/\\/g, "/").replace(/\.har$/, "");
    const clean = sanitizeHar(JSON.parse(raw) as HarFile, step, r, hex);
    const harText = JSON.stringify(clean, null, 2) + "\n";
    const json = extractJson(clean);
    const jsonText = JSON.stringify(json, null, 2) + "\n";
    const out = path.join(OUT, `${step}.har`);
    await fs.mkdir(path.dirname(out), { recursive: true });
    await fs.writeFile(out, harText);
    if (json.length) await fs.writeFile(path.join(OUT, `${step}.json`), jsonText);
    bytes += harText.length + (json.length ? jsonText.length : 0);
    const found = [...findLeaks(harText, denylist), ...(json.length ? findLeaks(jsonText, denylist) : [])];
    leaks += found.length;
    console.log(`${found.length ? "LEAK" : "ok  "} ${step} (${clean.log.entries.length} entries, ${json.length} json)${found.length ? "\n      " + found.join("\n      ") : ""}`);
  }
  // Files uploaded through page forms: the browser does not expose their bytes to the HAR recorder.
  const tmp = path.resolve(".steamworks-mcp/recordings/tmp");
  for (const name of await fs.readdir(tmp).catch(() => [] as string[])) {
    const text = scrubText(scrubJsonBody(await fs.readFile(path.join(tmp, name), "utf8")), r, hex);
    const found = findLeaks(text, denylist);
    leaks += found.length;
    await fs.mkdir(path.join(OUT, "uploads"), { recursive: true });
    await fs.writeFile(path.join(OUT, "uploads", name), text);
    console.log(`${found.length ? "LEAK" : "ok  "} uploads/${name}${found.length ? "\n      " + found.join("\n      ") : ""}`);
  }
  console.log(`\n${sorted.length} steps, ${(bytes / 1024 / 1024).toFixed(1)} MB written to ${path.relative(process.cwd(), OUT)}`);
  if (leaks) {
    console.log(`${leaks} finding(s): fix the rules above before committing.`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && /sanitize\.ts$/.test(process.argv[1])) await main();
