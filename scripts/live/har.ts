// Minimal HAR 1.2 recorder for Playwright pages: keeps documents and XHR/fetch to Steam hosts, drops static assets.
// Binary upload parts (images) are replaced by a size note; text parts (JSON/VDF/CSV uploads) are kept.
import fs from "node:fs/promises";
import path from "node:path";
import type { Page, Request } from "playwright";

const KEEP_TYPES = new Set(["document", "xhr", "fetch"]);
const STEAM_HOST = /(^|\.)(steamgames\.com|steampowered\.com|steam-api\.com|steamcommunity\.com)$/;

export interface HarHeader {
  name: string;
  value: string;
}

export interface HarEntry {
  startedDateTime: string;
  time: number;
  request: {
    method: string;
    url: string;
    httpVersion: string;
    headers: HarHeader[];
    queryString: HarHeader[];
    cookies: [];
    headersSize: -1;
    bodySize: number;
    postData?: { mimeType: string; text: string };
  };
  response: {
    status: number;
    statusText: string;
    httpVersion: string;
    headers: HarHeader[];
    cookies: [];
    content: { size: number; mimeType: string; text?: string; comment?: string };
    redirectURL: string;
    headersSize: -1;
    bodySize: number;
  };
  cache: Record<string, never>;
  timings: { send: number; wait: number; receive: number };
  _resourceType?: string;
  _failureText?: string;
}

export interface HarFile {
  log: {
    version: "1.2";
    creator: { name: string; version: string };
    entries: HarEntry[];
    _meta: Record<string, unknown>;
  };
}

const bodies = new WeakMap<Request, Promise<Buffer | undefined>>();

const isText = (mime: string) => /^(text\/|application\/(json|javascript|x-www-form-urlencoded|xml|vnd\.valve))|charset=/i.test(mime);

function queryOf(url: string): HarHeader[] {
  return [...new URL(url).searchParams.entries()].map(([name, value]) => ({ name, value }));
}

/** Replaces binary file parts of a multipart body with a size note; text parts stay readable (UTF-8). */
export function describeMultipart(body: Buffer, contentType: string): string {
  const boundary = /boundary=("?)([^";]+)\1/i.exec(contentType)?.[2];
  if (!boundary) return `[multipart body, ${body.length} bytes]`;
  const raw = body.toString("latin1");
  const parts = raw.split(`--${boundary}`);
  return parts
    .map((part) => {
      const split = part.indexOf("\r\n\r\n");
      if (split < 0) return part;
      const head = part.slice(0, split);
      const content = part.slice(split + 4);
      const fileName = /filename="([^"]*)"/i.exec(head)?.[1];
      const partType = /content-type:\s*([^\r\n]+)/i.exec(head)?.[1] ?? "";
      const textual = !fileName || isText(partType) || /\.(json|csv|vdf|txt)$/i.test(fileName);
      const decoded = textual
        ? Buffer.from(content, "latin1").toString("utf8")
        : `[binary file omitted: ${Buffer.byteLength(content, "latin1") - 2} bytes]\r\n`;
      return `${head}\r\n\r\n${decoded}`;
    })
    .join(`--${boundary}`);
}

export class HarRecorder {
  private entries: HarEntry[] = [];
  private pending = new Set<Promise<void>>();
  private seen = new WeakSet<Request>();
  private active = false;

  attach(page: Page): void {
    // Start reading bodies as soon as headers arrive: once the page navigates away they can no longer be fetched.
    page.on("response", (res) => {
      if (this.active) bodies.set(res.request(), res.body().catch(() => undefined));
    });
    page.on("requestfinished", (r) => this.track(r));
    page.on("requestfailed", (r) => this.track(r));
  }

  begin(): void {
    this.entries = [];
    this.active = true;
  }

  /** For requests made outside the browser (Node fetch to the Web API). */
  addManual(entry: HarEntry): void {
    if (this.active) this.entries.push(entry);
  }

  private track(req: Request): void {
    if (!this.active) return;
    const p = this.add(req).catch((err) => console.log(`  (recorder: ${String(err?.message ?? err).split("\n")[0]})`));
    this.pending.add(p);
    void p.finally(() => this.pending.delete(p));
  }

  private async add(req: Request): Promise<void> {
    const chain: Request[] = [];
    for (let r: Request | null = req; r; r = r.redirectedFrom()) chain.unshift(r);
    for (const r of chain) {
      if (this.seen.has(r)) continue;
      this.seen.add(r);
      if (!KEEP_TYPES.has(r.resourceType()) || !STEAM_HOST.test(new URL(r.url()).hostname)) continue;
      this.entries.push(await toEntry(r));
    }
  }

  async end(file: string, meta: Record<string, unknown>): Promise<number> {
    await Promise.all([...this.pending]);
    this.active = false;
    const entries = [...this.entries].sort((a, b) => a.startedDateTime.localeCompare(b.startedDateTime));
    const har: HarFile = { log: { version: "1.2", creator: { name: "steamworks-mcp/record", version: "0.1" }, entries, _meta: meta } };
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(file, JSON.stringify(har, null, 2));
    this.entries = [];
    return entries.length;
  }
}

async function toEntry(r: Request): Promise<HarEntry> {
  const res = await r.response().catch(() => null);
  const reqHeaders = await r.headersArray().catch(() => [] as HarHeader[]);
  const reqType = reqHeaders.find((h) => h.name.toLowerCase() === "content-type")?.value ?? "";
  const post = r.postDataBuffer();
  const timing = r.timing();
  const entry: HarEntry = {
    startedDateTime: new Date(timing.startTime > 0 ? timing.startTime : Date.now()).toISOString(),
    time: Math.max(0, timing.responseEnd),
    request: {
      method: r.method(),
      url: r.url(),
      httpVersion: "HTTP/1.1",
      headers: reqHeaders,
      queryString: queryOf(r.url()),
      cookies: [],
      headersSize: -1,
      bodySize: post?.length ?? 0,
      ...(post
        ? { postData: { mimeType: reqType, text: /multipart\//i.test(reqType) ? describeMultipart(post, reqType) : post.toString("utf8") } }
        : {}),
    },
    response: {
      status: res?.status() ?? 0,
      statusText: res?.statusText() ?? "",
      httpVersion: "HTTP/1.1",
      headers: res ? await res.headersArray().catch(() => []) : [],
      cookies: [],
      content: { size: 0, mimeType: "" },
      redirectURL: "",
      headersSize: -1,
      bodySize: -1,
    },
    cache: {},
    timings: { send: 0, wait: Math.max(0, timing.responseStart), receive: Math.max(0, timing.responseEnd - timing.responseStart) },
    _resourceType: r.resourceType(),
  };
  const failure = r.failure();
  if (failure) entry._failureText = failure.errorText;
  if (res) {
    const mime = (await res.headerValue("content-type").catch(() => null)) ?? "";
    entry.response.content.mimeType = mime;
    entry.response.redirectURL = (await res.headerValue("location").catch(() => null)) ?? "";
    if (res.status() < 300 || res.status() >= 400) {
      const body = (await bodies.get(r)) ?? (await res.body().catch(() => undefined));
      if (body) {
        entry.response.content.size = body.length;
        entry.response.bodySize = body.length;
        if (isText(mime) || mime === "") entry.response.content.text = body.toString("utf8");
        else entry.response.content.comment = `binary body omitted (${body.length} bytes)`;
      }
    }
  }
  return entry;
}

/** HAR entry for a request made with Node's fetch (no browser involved). `redact` strings never reach the file. */
export async function fetchEntry(
  method: "GET" | "POST",
  url: string,
  init: { body?: URLSearchParams; redact?: string[] } = {},
): Promise<{ entry: HarEntry; status: number; text: string }> {
  const scrub = (s: string) => (init.redact ?? []).filter(Boolean).reduce((acc, secret) => acc.split(secret).join("<redacted>"), s);
  const started = Date.now();
  const res = await fetch(url, {
    method,
    headers: { accept: "application/json", ...(init.body ? { "content-type": "application/x-www-form-urlencoded" } : {}) },
    ...(init.body ? { body: init.body } : {}),
  });
  const text = await res.text();
  const headers: HarHeader[] = [];
  res.headers.forEach((value, name) => headers.push({ name, value }));
  const entry: HarEntry = {
    startedDateTime: new Date(started).toISOString(),
    time: Date.now() - started,
    request: {
      method,
      url: scrub(url),
      httpVersion: "HTTP/1.1",
      headers: [{ name: "accept", value: "application/json" }],
      queryString: queryOf(scrub(url)),
      cookies: [],
      headersSize: -1,
      bodySize: init.body ? init.body.toString().length : 0,
      ...(init.body ? { postData: { mimeType: "application/x-www-form-urlencoded", text: scrub(init.body.toString()) } } : {}),
    },
    response: {
      status: res.status,
      statusText: res.statusText,
      httpVersion: "HTTP/1.1",
      headers,
      cookies: [],
      content: { size: text.length, mimeType: res.headers.get("content-type") ?? "", text: scrub(text) },
      redirectURL: res.headers.get("location") ?? "",
      headersSize: -1,
      bodySize: text.length,
    },
    cache: {},
    timings: { send: 0, wait: Date.now() - started, receive: 0 },
    _resourceType: "fetch",
  };
  return { entry, status: res.status, text: scrub(text) };
}
