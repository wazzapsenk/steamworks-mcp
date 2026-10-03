"""Turn raw Steamworks recordings (.steamworks-mcp/recordings/raw, gitignored) into committable fixtures.

    uv run python scripts/live/sanitize.py <recordedAppId> [--out tests/fixtures/steamworks]

Writes ``<area>/<step>.har`` (sanitized HAR 1.2) and ``<area>/<step>.json`` (the JSON request/response pairs of the
step) for every raw step; other fixtures in the output folder are left alone. What is replaced or removed:

- Cookie / Set-Cookie / Authorization headers (removed)
- steamLoginSecure, access_token, webapi_token and key= values -> REDACTED
- every hex run of 24+ characters (session ids, keys, CDN image hashes) -> "ffff...NNNN" of the same length
- the recorded app id -> 1000000, its store item id -> 2000000, other app ids of the account -> 1000001+,
  the partner id -> 900000
- SteamID64s -> 76561190000000001, the derived 32-bit account id -> 100000001
- GetAppBuilds: the uploader's account id -> 100000002, depot ids -> 3000001+, build ids -> 4000001+, depot
  manifest ids -> 5000001+
- SANITIZE_DENYLIST terms (from .env): app names -> "ExampleGame", everything else -> "Redacted"
- e-mail addresses -> user@example.com; antivirus script injections (removed)

It ends with the same kind of checks as tests/test_fixture_sanitization.py and exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / ".steamworks-mcp" / "recordings" / "raw"
LEGACY_UPLOADS = ROOT / ".steamworks-mcp" / "recordings" / "tmp"
"""Where the first (TypeScript) recorder kept uploaded files; record.py keeps them in <raw>/_uploads."""
APPS_FILE = ROOT / ".steamworks-mcp" / "live" / "apps.json"
BIG_DOC = 300_000
"""Documents bigger than this keep their body only in BIG_DOC_HOME; elsewhere it is omitted."""
BIG_DOC_HOME = "store/read"

DROP_HEADERS = {"cookie", "set-cookie", "authorization"}
A = re.ASCII


def digits(n: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![0-9]){re.escape(n)}(?![0-9])")


def load_dotenv_denylist() -> list[str]:
    value = os.environ.get("SANITIZE_DENYLIST")
    env = ROOT / ".env"
    if value is None and env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SANITIZE_DENYLIST="):
                value = line.split("=", 1)[1].strip().strip("'\"")
    return [t.strip() for t in (value or "").split(",") if t.strip()]


class Replacements:
    def __init__(self, app_id: str, raws: list[str], denylist: list[str]) -> None:
        joined = "\n".join(raws)
        self.numbers: list[tuple[re.Pattern[str], str]] = [(digits(app_id), "1000000")]
        item = re.search(r"/admin/game/edit/(\d+)", joined)
        if item:
            self.numbers.append((digits(item.group(1)), "2000000"))
        apps = json.loads(APPS_FILE.read_text(encoding="utf-8")) if APPS_FILE.is_file() else []
        n = 1000001
        for a in apps:
            if str(a["appId"]) != app_id:
                self.numbers.append((digits(str(a["appId"])), str(n)))
                n += 1
        for m in re.finditer(
            r'(?:partnerid=|g_nPrimaryPublisher = |data-publisherid=\\?"|publisherid=\\?")(\d{3,})', joined
        ):
            if m.group(1) != "0" and not any(m.group(1) in p.pattern for p, _ in self.numbers):
                self.numbers.append((digits(m.group(1)), "900000"))
        for s64 in dict.fromkeys(re.findall(r"7656119\d{10}", joined)):
            self.numbers.append((digits(s64), "76561190000000001"))
            self.numbers.append((digits(str(int(s64) - 76561197960265728)), "100000001"))
        # GetAppBuilds: the uploader's account id, and depot, build and manifest ids that point back at the app
        for pattern, first in (
            (r'AccountIDCreator\\?"\s*:\s*(\d+)', 100000002),
            (r'DepotID\\?"\s*:\s*(\d+)', 3000001),
            (r'BuildID\\?"\s*:\s*(\d+)', 4000001),
            (r'DepotVersionGID\\?"\s*:\s*\\?"(\d+)', 5000001),
        ):
            for i, found in enumerate(dict.fromkeys(re.findall(pattern, joined))):
                if not any(p.search(found) for p, _ in self.numbers):
                    self.numbers.append((digits(found), str(first + i)))

        app_names = {str(a["name"]).lower() for a in apps}
        self.terms: list[tuple[re.Pattern[str], str]] = []
        for t in sorted((t for t in denylist if len(t) >= 3), key=len, reverse=True):
            is_app = any(t.lower() in name for name in app_names)
            variants = dict.fromkeys(
                [t, quote(t, safe="-_.!~*'()"), t.replace(" ", "+"), t.replace("\\", "\\\\"), t.replace("/", "\\/")]
            )
            for v in variants:
                self.terms.append((re.compile(re.escape(v), re.I | A), "ExampleGame" if is_app else "Redacted"))

        secrets: dict[str, None] = {}
        for m in re.finditer(r'steamLoginSecure=([^;"\s\\]+)', joined):
            secrets[m.group(1)] = None
        for m in re.finditer(r'(?:access_token|webapi_token)(?:=|\\?":\\?")([A-Za-z0-9_\-.%]{16,})', joined):
            secrets[m.group(1)] = None
        self.secrets = [s for s in secrets if len(s) >= 8]


class HexMap:
    def __init__(self) -> None:
        self.map: dict[str, str] = {}

    def get(self, hex_run: str) -> str:
        key = hex_run.lower()
        if key not in self.map:
            self.map[key] = "f" * max(0, len(hex_run) - 4) + str(len(self.map) + 1).zfill(4)
        return self.map[key]


def scrub_text(s: str, r: Replacements, hexes: HexMap) -> str:
    out = s
    for secret in r.secrets:
        out = out.replace(secret, "REDACTED")
    out = re.sub(r'(steamLoginSecure=)[^;"\s\\]+', r"\1REDACTED", out)
    out = re.sub(r'((?:access_token|webapi_token)=)[^&"\s\\]+', r"\1REDACTED", out)
    out = re.sub(r'([?&]key=)(?!<redacted>)[^&"\s\\]+', r"\1REDACTED", out)
    out = re.sub(r"<script[^>]*kaspersky[^>]*>\s*</script>", "", out, flags=re.I)  # antivirus injections
    out = re.sub(r"""[a-z]+://[^"'\s;]*kaspersky[^"'\s;]*""", "", out, flags=re.I)
    out = re.sub(r"[\w.-]*kaspersky[\w.-]*", "", out, flags=re.I | A)
    for pattern, to in r.numbers:
        out = pattern.sub(to, out)
    out = re.sub(r"[0-9a-fA-F]{24,}", lambda m: m[0] if re.fullmatch(r"f+\d{4}", m[0]) else hexes.get(m[0]), out)
    for pattern, to in r.terms:
        out = pattern.sub(to, out)
    return re.sub(
        r"[\w.+-]+@[\w-]+(\.[\w-]+)+",
        lambda m: m[0] if re.search(r"@example\.com$", m[0], re.I) else "user@example.com",
        out,
        flags=A,
    )


def _js_number(text: str) -> Any:
    v = float(text)
    return int(v) if v.is_integer() and abs(v) < 1e21 else v


def loads(text: str) -> Any:
    """``JSON.parse`` as the browser does it: 1.0 is 1."""
    return json.loads(text, parse_float=_js_number)


def dumps(data: Any) -> str:
    """``JSON.stringify(data, null, 2)``: lone surrogates escaped like the browser does."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return re.sub("[\ud800-\udfff]", lambda m: f"\\u{ord(m[0]):04x}", text) + "\n"


def sanitize_har(har: dict[str, Any], step: str, r: Replacements, hexes: HexMap) -> dict[str, Any]:
    entries = []
    for e in har["log"]["entries"]:
        copy = json.loads(json.dumps(e))
        for part in ("request", "response"):
            copy[part]["headers"] = [h for h in copy[part]["headers"] if h["name"].lower() not in DROP_HEADERS]
        content = copy["response"]["content"]
        text = content.get("text") or ""
        if copy.get("_resourceType") in ("document", "fetch", "xhr") and len(text) > BIG_DOC and step != BIG_DOC_HOME:
            content["comment"] = f"document body omitted ({len(text)} chars); the same page is in {BIG_DOC_HOME}.har"
            del content["text"]
        st = copy["response"]["status"]
        if st > 0 and (st < 300 or st >= 400) and "text" not in content and not content.get("comment"):
            content["comment"] = "body not captured: the page navigated away before it could be read"
        entries.append(copy)

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return scrub_text(v, r, hexes)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    log = dict(har["log"])
    log["entries"] = entries
    return {"log": walk(log)}


def extract_json(har: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in har["log"]["entries"]:
        text = e["response"]["content"].get("text") or ""
        try:
            response = loads(text) if re.match(r"\s*[{\[]", text) else None
        except ValueError:
            response = None
        if response is None:
            continue
        u = urlsplit(e["request"]["url"])
        item: dict[str, Any] = {
            "method": e["request"]["method"],
            "url": f"{u.scheme}://{u.netloc}{u.path}",
            "query": dict(parse_qsl(u.query, keep_blank_values=True)),
            "status": e["response"]["status"],
        }
        post = e["request"].get("postData")
        if post and "application/x-www-form-urlencoded" in post.get("mimeType", ""):
            item["request"] = dict(parse_qsl(post.get("text", ""), keep_blank_values=True))
        elif post:
            text = post.get("text", "")
            item["request"] = {"multipart": text[:4000] + "…" if len(text) > 4000 else text}
        item["response"] = response
        out.append(item)
    return out


def find_leaks(text: str, denylist: list[str]) -> list[str]:
    checks = {
        "cookie header": r'"name":\s*"(?i:cookie|set-cookie)"',
        "steamLoginSecure value": r'steamLoginSecure=(?!REDACTED)[^;"\s\\]',
        "token value": r'(access_token|webapi_token)=(?!REDACTED)[^&"\s\\]',
        "web api key": r'[?&]key=(?!REDACTED|<redacted>)[^&"\s\\]',
        "hex id": r"(?<![0-9a-fA-F])(?!f+\d{4}(?![0-9a-fA-F]))[0-9a-fA-F]{24,}",
        "steamid64": r"7656119(?!0000000001|7960265728)\d{10}",
        "partner id": r'(?:partnerid=|g_nPrimaryPublisher = |data-publisherid=\\?"|publisherid=\\?")(?!900000\b|0\b)\d',
        "e-mail": r"[\w.+-]+@(?!example\.com)[\w-]+\.[\w.-]+",
        "antivirus injection": r"(?i:kaspersky)",
    }
    found = []
    for label, pattern in checks.items():
        m = re.search(pattern, text, A)
        if m:
            found.append(f"{label}: ...{text[max(0, m.start() - 40) : m.start() + 40]!r}...")
    found += [f"denylisted term #{i + 1}" for i, t in enumerate(denylist) if len(t) >= 3 and t.lower() in text.lower()]
    return found


def collation_key(path: str) -> list[int]:
    """Approximates the ICU order the TypeScript sanitizer used (punctuation before digits before letters), which
    decides the numbering of the hex placeholders."""
    punct = "_-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$"
    key = []
    for ch in path:
        if ch in punct:
            key.append(punct.index(ch))
        elif ch.isdigit():
            key.append(100 + int(ch))
        else:
            key.append(200 + ord(ch.lower()))
    return key


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app_id")
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures" / "steamworks")
    args = ap.parse_args()
    if not args.app_id.isdigit():
        ap.error("app_id must be a number")
    files = sorted(args.raw.rglob("*.har"))
    raws = [f.read_text(encoding="utf-8") for f in files]
    denylist = load_dotenv_denylist()
    r = Replacements(args.app_id, raws, denylist)
    hexes = HexMap()
    leaks = total = 0
    # Reads first, so the big store document stays in store/read.
    order = sorted(
        zip(files, raws, strict=True), key=lambda fr: (0 if fr[0].name == "read.har" else 1, collation_key(str(fr[0])))
    )
    for f, raw in order:
        step = f.relative_to(args.raw).as_posix().removesuffix(".har")
        clean = sanitize_har(loads(raw), step, r, hexes)
        har_text = dumps(clean)
        pairs = extract_json(clean)
        json_text = dumps(pairs)
        out = args.out / f"{step}.har"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(har_text, encoding="utf-8", newline="\n")
        if pairs:
            out.with_suffix(".json").write_text(json_text, encoding="utf-8", newline="\n")
        found = find_leaks(har_text, denylist) + (find_leaks(json_text, denylist) if pairs else [])
        leaks += len(found)
        total += len(har_text) + (len(json_text) if pairs else 0)
        print(f"{'LEAK' if found else 'ok  '} {step} ({len(clean['log']['entries'])} entries, {len(pairs)} json)")
        for x in found:
            print(f"      {x}")
    # Files uploaded through page forms: the browser does not expose their bytes to the recorder.
    uploads = args.raw / "_uploads" if (args.raw / "_uploads").is_dir() else LEGACY_UPLOADS
    for f in sorted(uploads.glob("*")) if uploads.is_dir() else []:
        text = scrub_text(f.read_text(encoding="utf-8"), r, hexes)
        found = find_leaks(text, denylist)
        leaks += len(found)
        (args.out / "uploads").mkdir(parents=True, exist_ok=True)
        (args.out / "uploads" / f.name).write_text(text, encoding="utf-8", newline="\n")
        print(f"{'LEAK' if found else 'ok  '} uploads/{f.name}")
    print(f"\n{len(order)} steps, {total / 1024 / 1024:.1f} MB written to {args.out}")
    if leaks:
        print(f"{leaks} finding(s): fix the rules above before committing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
