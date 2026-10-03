"""Fetch public data about reference games, on the user's machine, with a local cache.

Sources (no key needed unless noted):

* ``store.steampowered.com/api/appdetails`` - store data (descriptions, media, categories, languages, price in USD).
* ``ISteamUserStats/GetGlobalAchievementPercentagesForApp`` - achievement API names and global unlock rates.
* ``steamcommunity.com/stats/<appid>/achievements`` - achievement display names and descriptions (public page).
* ``ISteamUserStats/GetSchemaForGame`` - needs a Steam Web API key; adds the hidden flags.

Raw responses (other games' texts) only ever live in the cache, never in the repository. Each cache entry records
when and from where it was fetched. Requests are spaced out and retried with backoff.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

STORE_API = "https://store.steampowered.com/api/appdetails"
WEB_API = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com/stats/{appid}/achievements/"
USER_AGENT = "steamworks-mcp (reference research; https://github.com/wazzapsenk/steamworks-mcp)"


class FetchError(RuntimeError):
    pass


@dataclass
class Cached:
    data: Any
    fetched_at: dt.datetime
    source: str


class ReferenceFetcher:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: httpx.Client | None = None,
        web_api_key: str | None = None,
        max_age_days: int = 30,
        min_interval: float = 1.5,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.web_api_key = web_api_key
        self.max_age = dt.timedelta(days=max_age_days)
        self.min_interval = min_interval
        self.retries = retries
        self._sleep = sleep
        self._last_request: dict[str, float] = {}

    # ------------------------------------------------------------------ cache

    def _path(self, appid: int, kind: str) -> Path:
        return self.cache_dir / str(appid) / f"{kind}.json"

    def _cached(self, appid: int, kind: str, refresh: bool) -> Cached | None:
        p = self._path(appid, kind)
        if refresh or not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        fetched = dt.datetime.fromisoformat(raw["fetched_at"])
        if dt.datetime.now(dt.UTC) - fetched > self.max_age:
            return None
        return Cached(raw["data"], fetched, raw["source"])

    def _store(self, appid: int, kind: str, data: Any, source: str) -> Cached:
        now = dt.datetime.now(dt.UTC).replace(microsecond=0)
        p = self._path(appid, kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"fetched_at": now.isoformat(), "source": source, "data": data}, ensure_ascii=False), "utf-8"
        )
        return Cached(data, now, source)

    # ------------------------------------------------------------------ http

    def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        host = httpx.URL(url).host
        wait = self.min_interval - (time.monotonic() - self._last_request.get(host, -1e9))
        if wait > 0:
            self._sleep(wait)
        delay = 2.0
        for attempt in range(self.retries + 1):
            self._last_request[host] = time.monotonic()
            try:
                res = self.client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise FetchError(f"{url}: {exc}") from exc
                self._sleep(delay)
                delay *= 2
                continue
            if res.status_code == 429 or res.status_code >= 500:
                if attempt == self.retries:
                    raise FetchError(f"{url}: HTTP {res.status_code} after {self.retries + 1} attempts")
                retry_after = res.headers.get("retry-after", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else delay)
                delay *= 2
                continue
            return res
        raise AssertionError("unreachable")

    def _redact(self, url: str) -> str:
        return url.replace(self.web_api_key, "<key>") if self.web_api_key else url

    # ------------------------------------------------------------------ sources

    def appdetails(self, appid: int, *, refresh: bool = False) -> Cached:
        if hit := self._cached(appid, "appdetails", refresh):
            return hit
        params = {"appids": appid, "l": "english", "cc": "us"}
        res = self._get(STORE_API, params)
        if res.status_code != 200:
            raise FetchError(f"appdetails {appid}: HTTP {res.status_code}")
        entry = res.json().get(str(appid)) or {}
        if not entry.get("success"):
            raise FetchError(f"appdetails {appid}: not available (unreleased, region-locked or wrong app id)")
        return self._store(appid, "appdetails", entry["data"], str(res.url))

    def achievement_percentages(self, appid: int, *, refresh: bool = False) -> Cached:
        if hit := self._cached(appid, "achievement_percentages", refresh):
            return hit
        url = f"{WEB_API}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/"
        res = self._get(url, {"gameid": appid})
        if res.status_code == 403:
            return self._store(appid, "achievement_percentages", [], url)  # no public achievements
        if res.status_code != 200:
            raise FetchError(f"achievement percentages {appid}: HTTP {res.status_code}")
        rows = res.json().get("achievementpercentages", {}).get("achievements", [])
        data = [{"name": r["name"], "percent": float(r["percent"])} for r in rows]
        return self._store(appid, "achievement_percentages", data, url)

    def achievement_texts(self, appid: int, *, refresh: bool = False) -> Cached:
        """Display names and descriptions from the public community stats page."""
        if hit := self._cached(appid, "achievement_texts", refresh):
            return hit
        url = COMMUNITY.format(appid=appid)
        res = self._get(url, {"l": "english"})
        if res.status_code != 200:
            raise FetchError(f"community achievements {appid}: HTTP {res.status_code}")
        return self._store(appid, "achievement_texts", parse_community_achievements(res.text), url)

    def schema(self, appid: int, *, refresh: bool = False) -> Cached | None:
        """Full achievement/stat schema (with hidden flags); ``None`` without a Steam Web API key."""
        if not self.web_api_key:
            return None
        if hit := self._cached(appid, "schema", refresh):
            return hit
        url = f"{WEB_API}/ISteamUserStats/GetSchemaForGame/v2/"
        res = self._get(url, {"appid": appid, "key": self.web_api_key, "l": "english"})
        if res.status_code != 200:
            raise FetchError(f"schema {appid}: HTTP {res.status_code}")
        return self._store(appid, "schema", res.json().get("game", {}), self._redact(str(res.url)))


_ROW = re.compile(
    r'<div class="achieveRow[^"]*">.*?<div class="achievePercent">([\d.]+)%</div>.*?<h3>(.*?)</h3>\s*<h5>(.*?)</h5>',
    re.S,
)


def parse_community_achievements(page: str) -> list[dict[str, Any]]:
    """Rows of the community "global achievements" page: name, description, percent (page order)."""
    out = []
    for percent, name, desc in _ROW.findall(page):
        out.append(
            {"name": html.unescape(name).strip(), "description": html.unescape(desc).strip(), "percent": float(percent)}
        )
    return out
