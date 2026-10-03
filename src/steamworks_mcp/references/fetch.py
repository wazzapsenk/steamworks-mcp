"""Fetch public data about reference games, on the user's machine, with a local cache.

Sources (no key needed unless noted):

* ``store.steampowered.com/api/appdetails`` - store data (descriptions, media, categories, languages, price in USD).
* ``ISteamUserStats/GetGlobalAchievementPercentagesForApp`` - achievement API names and global unlock rates.
* ``steamcommunity.com/stats/<appid>/achievements`` - achievement display names and descriptions (public page).
* ``ISteamUserStats/GetSchemaForGame`` - needs a Steam Web API key; adds the hidden flags.
* ``store.steampowered.com/search/results`` - the "Popular New Releases" and "Top Sellers" lists, optionally filtered
  by store tags, for the store patterns and the market study.
* ``store.steampowered.com/tagdata/populartags`` - the store tags with their ids.
* ``store.steampowered.com/api/storesearch`` - games by name.
* ``ISteamUserStats/GetNumberOfCurrentPlayers`` - how many people play a game right now.
* ``store.steampowered.com/appreviews/<appid>`` - the review score and totals, and review texts. Only the text, the
  vote, the playtime at review and the date are kept; nothing about the reviewer.
* ``appdetails?filters=price_overview&cc=<country>`` - prices in a country's store, for many games at once.

Live numbers (players, reviews, prices) are cached for hours, not days: see :data:`FRESH`.

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
from typing import Any, Literal

import httpx

STORE_API = "https://store.steampowered.com/api/appdetails"
STORE_SEARCH = "https://store.steampowered.com/search/results/"
STORE_TAGS = "https://store.steampowered.com/tagdata/populartags/english"
GAMES_ONLY = 998
"""The search's "Games" type filter (``category1``): no DLC, software, soundtracks or videos."""
WEB_API = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com/stats/{appid}/achievements/"
USER_AGENT = "steamworks-mcp (reference research; https://github.com/wazzapsenk/steamworks-mcp)"
STORE_FIND = "https://store.steampowered.com/api/storesearch/"
REVIEWS = "https://store.steampowered.com/appreviews/{appid}"
FRESH = {
    "players": dt.timedelta(hours=1),
    "review_summary": dt.timedelta(hours=12),
    "reviews": dt.timedelta(days=7),
    "price": dt.timedelta(hours=12),
}
"""How long live numbers stay fresh in the cache (everything else: ``max_age_days``)."""
REVIEW_KEYS = ("recommendationid", "voted_up", "votes_up", "timestamp_created", "language", "review")


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

    def _cached(self, appid: int, kind: str, refresh: bool, max_age: dt.timedelta | None = None) -> Cached | None:
        p = self._path(appid, kind)
        if refresh or not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        fetched = dt.datetime.fromisoformat(raw["fetched_at"])
        if dt.datetime.now(dt.UTC) - fetched > (max_age or self.max_age):
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

    def popular_new_releases(self, count: int = 100) -> list[int]:
        """App ids on the store's "Popular New Releases" list, newest first (not cached: it changes every day)."""
        return self.search("popularnew", count=count)

    def search(
        self, list_filter: Literal["popularnew", "topsellers"], *, tags: list[int] | None = None, count: int = 100
    ) -> list[int]:
        """App ids of a store list: "popularnew" (Popular New Releases, newest first) or "topsellers" (Top Sellers,
        best first). With ``tags`` only games that have all of them (store tag ids, see :meth:`store_tags`). Not
        cached: the lists change every day."""
        params: dict[str, Any] = {"filter": list_filter, "json": 1, "count": count, "cc": "us", "l": "english"}
        if list_filter == "popularnew":
            params["sort_by"] = "Released_DESC"
        if tags:
            params["tags"] = ",".join(str(t) for t in tags)
            params["category1"] = GAMES_ONLY
        res = self._get(STORE_SEARCH, params)
        if res.status_code != 200:
            raise FetchError(f"store search ({list_filter}): HTTP {res.status_code}")
        ids = [_APP_IN_URL.search(str(item.get("logo", ""))) for item in res.json().get("items", [])]
        return list(dict.fromkeys(int(m.group(1)) for m in ids if m))  # bundles and packages have no /apps/ url

    def store_tags(self, *, refresh: bool = False) -> dict[str, int]:
        """Store tag names (lowercase) to their ids, e.g. {"co-op": 1685}. Cached like the store data."""
        p = self.cache_dir / "_store" / "tags.json"
        if not refresh and p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(raw["fetched_at"]) <= self.max_age:
                return {str(k): int(v) for k, v in raw["data"].items()}
        res = self._get(STORE_TAGS, {})
        if res.status_code != 200:
            raise FetchError(f"store tags: HTTP {res.status_code}")
        data = {str(t["name"]).lower(): int(t["tagid"]) for t in res.json() if t.get("name") and t.get("tagid")}
        now = dt.datetime.now(dt.UTC).replace(microsecond=0)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetched_at": now.isoformat(), "source": STORE_TAGS, "data": data}), "utf-8")
        return data

    # ------------------------------------------------------------------ live numbers

    def find(self, term: str) -> list[dict[str, Any]]:
        """Games whose name matches ``term`` (the store's own search box): app id, name and USD price."""
        res = self._get(STORE_FIND, {"term": term, "l": "english", "cc": "US"})
        if res.status_code != 200:
            raise FetchError(f"store search {term!r}: HTTP {res.status_code}")
        return [
            {"appid": int(i["id"]), "name": str(i.get("name") or ""), "price": i.get("price")}
            for i in res.json().get("items") or []
            if i.get("type") == "app" and i.get("id")
        ]

    def current_players(self, appid: int, *, refresh: bool = False) -> Cached:
        if hit := self._cached(appid, "players", refresh, FRESH["players"]):
            return hit
        url = f"{WEB_API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
        res = self._get(url, {"appid": appid})
        if res.status_code == 404:
            return self._store(appid, "players", None, url)  # unreleased, or no player data
        if res.status_code != 200:
            raise FetchError(f"current players {appid}: HTTP {res.status_code}")
        response = res.json().get("response") or {}
        count = response.get("player_count") if response.get("result") == 1 else None
        return self._store(appid, "players", count, url)

    def review_summary(self, appid: int, *, refresh: bool = False) -> Cached:
        """Review score and totals over every language: review_score_desc, total_positive, total_negative,
        total_reviews."""
        if hit := self._cached(appid, "review_summary", refresh, FRESH["review_summary"]):
            return hit
        url = REVIEWS.format(appid=appid)
        params = {"json": 1, "language": "all", "purchase_type": "all", "num_per_page": 0, "filter": "all"}
        res = self._get(url, params)
        if res.status_code != 200:
            raise FetchError(f"review summary {appid}: HTTP {res.status_code}")
        summary = res.json().get("query_summary") or {}
        keep = ("review_score", "review_score_desc", "total_positive", "total_negative", "total_reviews")
        return self._store(appid, "review_summary", {k: summary.get(k) for k in keep}, url)

    def reviews(
        self, appid: int, kind: Literal["positive", "negative"], *, count: int | None = 20, refresh: bool = False
    ) -> Cached:
        """The most helpful English reviews of one kind. Kept per review: id, vote, helpful votes, date, language,
        playtime at review (hours) and the text (cut at 1500 characters). Nothing about the reviewer is kept.
        ``count=None``: whatever is cached (20 when nothing is)."""
        kind_key = f"reviews_{kind}"
        hit = self._cached(appid, kind_key, refresh, FRESH["reviews"])
        if hit and (count is None or len(hit.data) >= count):
            return Cached(hit.data[:count], hit.fetched_at, hit.source)
        count = count or 20
        url = REVIEWS.format(appid=appid)
        params = {
            "json": 1,
            "language": "english",
            "purchase_type": "all",
            "filter": "all",
            "review_type": kind,
            "num_per_page": min(100, max(1, count)),
            "cursor": "*",
        }
        res = self._get(url, params)
        if res.status_code != 200:
            raise FetchError(f"reviews {appid}: HTTP {res.status_code}")
        rows = []
        for r in res.json().get("reviews") or []:
            row = {k: r.get(k) for k in REVIEW_KEYS}
            row["review"] = " ".join(str(row["review"] or "").split())[:1500]
            row["hours_at_review"] = round(((r.get("author") or {}).get("playtime_at_review") or 0) / 60, 1)
            rows.append(row)
        return self._store(appid, kind_key, rows, url)

    def prices(self, appids: list[int], country: str, *, refresh: bool = False) -> dict[int, dict[str, Any] | None]:
        """Each game's price in a country's store: currency, full and current price (in cents) and the discount.
        None for free or unavailable games. One request covers up to 50 games that are not cached yet."""
        cc = country.lower()
        out: dict[int, dict[str, Any] | None] = {}
        todo = []
        for appid in appids:
            if hit := self._cached(appid, f"price_{cc}", refresh, FRESH["price"]):
                out[appid] = hit.data
            else:
                todo.append(appid)
        for start in range(0, len(todo), 50):
            batch = todo[start : start + 50]
            params = {"appids": ",".join(str(a) for a in batch), "cc": cc, "filters": "price_overview"}
            res = self._get(STORE_API, params)
            if res.status_code != 200:
                raise FetchError(f"prices ({cc}): HTTP {res.status_code}")
            body = res.json() or {}
            for appid in batch:
                entry = body.get(str(appid)) or {}
                overview = (entry.get("data") or {}).get("price_overview") if entry.get("success") else None
                price = (
                    {
                        "currency": overview["currency"],
                        "full": overview["initial"],
                        "now": overview["final"],
                        "discount_percent": overview.get("discount_percent", 0),
                    }
                    if isinstance(overview, dict)
                    else None
                )
                out[appid] = self._store(appid, f"price_{cc}", price, str(res.url)).data
        return {a: out[a] for a in appids}


_APP_IN_URL = re.compile(r"/apps/(\d+)/")
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
