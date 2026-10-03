"""Recorded Steamworks fixtures and the rest of the repository must not leak secrets or personal data.

Two kinds of checks:

* Generic patterns that work on any machine: cookie headers, session ids / keys / hashes that were not replaced
  by placeholders, SteamID64s, e-mail addresses, token values, antivirus script injections.
* A local denylist: ``SANITIZE_DENYLIST`` in ``.env`` (account name, persona, e-mail, studio and app names, local
  paths). The list never leaves the developer's machine; without it only the generic checks run.

The fixtures are produced by ``scripts/live/sanitize.py`` from raw recordings that never enter the repository.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "steamworks"

# Files that legitimately carry the author's name (license, package and plugin metadata).
AUTHOR_METADATA = {
    "LICENSE",
    "pyproject.toml",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/plugin.json",
}

FIXTURE_PATTERNS: dict[str, re.Pattern[str]] = {
    "cookie header": re.compile(r'"name":\s*"(cookie|set-cookie)"', re.IGNORECASE),
    "steamLoginSecure value": re.compile(r'steamLoginSecure=(?!REDACTED)[^;"\s\\]'),
    "token value": re.compile(r'(access_token|webapi_token)=(?!REDACTED)[^&"\s\\]'),
    "web api key": re.compile(r'[?&]key=(?!REDACTED|<redacted>)[^&"\s\\]'),
    "unreplaced hex id": re.compile(r"(?<![0-9a-fA-F])(?!f+\d{4}(?![0-9a-fA-F]))[0-9a-fA-F]{24,}"),
    "steamid64": re.compile(r"7656119(?!0000000001|7960265728)\d{10}"),
    "e-mail": re.compile(r"[\w.+-]+@(?!example\.com)[\w-]+\.[\w.-]+"),
    "partner id": re.compile(
        r'(?:partnerid=|g_nPrimaryPublisher = |data-publisherid=\\?"|publisherid=\\?")(?!900000\b|0\b)\d'
    ),
    "antivirus injection": re.compile(r"kaspersky", re.IGNORECASE),
}

# Repo-wide: secrets in any shape that should never be committed anywhere.
REPO_PATTERNS: dict[str, re.Pattern[str]] = {
    "steamLoginSecure value": re.compile(r"steamLoginSecure=(?!REDACTED)[0-9]{17}"),
    "session id assignment": re.compile(r"sessionid=(?!f+\d{4})[0-9a-f]{24}\b"),
    "web api key": re.compile(r"(?i)(?:STEAMWORKS_PUBLISHER_KEY|key)=[0-9A-F]{32}\b"),
    "steamid64": re.compile(r"7656119(?!0000000001|7960265728)\d{10}"),
}


def read_denylist() -> list[str]:
    """``SANITIZE_DENYLIST`` from the local .env (comma-separated); empty when there is no .env."""
    env = ROOT / ".env"
    if not env.exists():
        return []
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "SANITIZE_DENYLIST":
            return [t.strip() for t in value.split(",") if len(t.strip()) >= 3]
    return []


def committable_files() -> list[Path]:
    """Tracked files plus untracked files that are not ignored (what a commit could include)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [ROOT / p for p in out.split("\0") if p and (ROOT / p).is_file()]


def repo_owner() -> str | None:
    """Owner of the origin remote: public by definition (repo URLs), so it may contain a denylisted handle."""
    res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"github\.com[:/]([^/]+)/", res.stdout)
    return m.group(1).lower() if m else None


def text_of(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        return None  # binary (images)
    return data.decode("utf-8", errors="replace")


def fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.rglob("*") if p.is_file())


def snippet(text: str, start: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - 30) : start + 30])


def test_fixtures_exist() -> None:
    assert FIXTURES.is_dir(), "tests/fixtures/steamworks is missing"
    assert any(p.suffix == ".har" for p in fixture_files())


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.relative_to(FIXTURES).as_posix())
def test_fixture_has_no_generic_leaks(path: Path) -> None:
    text = text_of(path)
    assert text is not None
    problems = [
        f"{label}: …{snippet(text, m.start())}…" for label, rx in FIXTURE_PATTERNS.items() if (m := rx.search(text))
    ]
    assert not problems, "\n".join(problems)


def test_fixtures_have_no_denylisted_terms() -> None:
    terms = read_denylist()
    if not terms:
        pytest.skip("SANITIZE_DENYLIST is not set in .env; only the generic checks ran")
    hits = []
    for path in fixture_files():
        lower = (text_of(path) or "").lower()
        hits += [
            f"{path.relative_to(ROOT).as_posix()}: term #{i + 1}" for i, t in enumerate(terms) if t.lower() in lower
        ]
    assert not hits, "\n".join(hits)


def test_repository_has_no_secrets() -> None:
    problems = []
    for path in committable_files():
        text = text_of(path)
        if text is None:
            continue
        for label, rx in REPO_PATTERNS.items():
            if m := rx.search(text):
                problems.append(f"{path.relative_to(ROOT).as_posix()}: {label}: …{snippet(text, m.start())}…")
    assert not problems, "\n".join(problems)


def test_repository_has_no_denylisted_terms() -> None:
    terms = read_denylist()
    if not terms:
        pytest.skip("SANITIZE_DENYLIST is not set in .env; only the generic checks ran")
    owner = repo_owner()
    hits = []
    for path in committable_files():
        if path.relative_to(ROOT).as_posix() in AUTHOR_METADATA:
            continue
        text = text_of(path)
        if text is None:
            continue
        lower = text.lower()
        if owner:
            for host in ("github.com/", "githubusercontent.com/"):
                lower = lower.replace(host + owner, host + "<owner>")
        hits += [
            f"{path.relative_to(ROOT).as_posix()}: term #{i + 1}" for i, t in enumerate(terms) if t.lower() in lower
        ]
    assert not hits, "\n".join(hits)
