"""Live market numbers from Steam's public store: look a game up, compare games, see what close games charge in each
country, rough sales estimates, and how a released game is doing.

Everything comes from public endpoints without a key (see :mod:`steamworks_mcp.references.fetch`) and is cached on
this machine for a few hours. Numbers only: no store or review text is returned here.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from steamworks_mcp.data import load_yaml
from steamworks_mcp.manifest.io import ProjectFiles
from steamworks_mcp.references import market
from steamworks_mcp.references.fetch import FetchError, ReferenceFetcher

COUNTRIES = ("us", "gb", "de", "pl", "tr", "br", "cn", "jp", "kr", "in")
"""Stores price_brief shows by default: big markets and the ones whose prices differ most from the US."""
COUNTRY_NAMES = {
    "us": "United States",
    "gb": "United Kingdom",
    "de": "Euro area (Germany)",
    "fr": "Euro area (France)",
    "pl": "Poland",
    "tr": "Turkey",
    "br": "Brazil",
    "cn": "China",
    "jp": "Japan",
    "kr": "South Korea",
    "in": "India",
    "ca": "Canada",
    "au": "Australia",
    "mx": "Mexico",
    "ar": "Argentina",
    "ru": "Russia",
    "ua": "Ukraine",
    "id": "Indonesia",
    "ph": "Philippines",
    "za": "South Africa",
}
MODES = (
    "Single-player",
    "Multi-player",
    "Online Co-op",
    "Shared/Split Screen Co-op",
    "LAN Co-op",
    "PvP",
    "Online PvP",
    "Shared/Split Screen PvP",
    "MMO",
    "Cross-Platform Multiplayer",
)
FEATURES = (
    "Steam Achievements",
    "Steam Cloud",
    "Full controller support",
    "Partial Controller Support",
    "Steam Trading Cards",
    "Steam Workshop",
    "Remote Play Together",
    "Steam Leaderboards",
    "In-App Purchases",
)
MAX_GAMES = 15


def _dollars(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def _languages(details: dict[str, Any]) -> int:
    text = re.sub(r"<[^>]+>", ",", str(details.get("supported_languages") or ""))
    text = text.split("languages with full audio")[0]
    return len({p.strip(" *") for p in text.split(",") if p.strip(" *")})


def snapshot(fetcher: ReferenceFetcher, appid: int, *, refresh: bool = False) -> dict[str, Any]:
    """One game's public numbers: price, reviews, players right now, modes, features, platforms."""
    d = fetcher.appdetails(appid).data
    price = fetcher.prices([appid], "us", refresh=refresh)[appid]
    reviews = fetcher.review_summary(appid, refresh=refresh).data
    players = fetcher.current_players(appid, refresh=refresh).data
    genres = [str(g.get("description")) for g in d.get("genres") or []]
    categories = {str(c.get("description")) for c in d.get("categories") or []}
    total = int(reviews.get("total_reviews") or 0)
    release = d.get("release_date") or {}
    return {
        "appid": appid,
        "name": d.get("name"),
        "developers": d.get("developers") or [],
        "publishers": d.get("publishers") or [],
        "released": release.get("date") or None,
        "coming_soon": bool(release.get("coming_soon")),
        "free": bool(d.get("is_free")),
        "price_usd": {
            "full": _dollars(price["full"]),
            "now": _dollars(price["now"]),
            "discount_percent": price["discount_percent"],
        }
        if price
        else None,
        "reviews": {
            "score": reviews.get("review_score_desc") or None,
            "total": total,
            "positive_share": round(int(reviews.get("total_positive") or 0) / total, 3) if total else None,
        },
        "players_now": players,
        "genres": genres,
        "early_access": "Early Access" in genres,
        "modes": [m for m in MODES if m in categories],
        "features": [f for f in FEATURES if f in categories],
        "platforms": [p for p, on in (d.get("platforms") or {}).items() if on],
        "languages": _languages(d),
        "achievements": (d.get("achievements") or {}).get("total"),
        "dlc": len(d.get("dlc") or []),
        "metacritic": (d.get("metacritic") or {}).get("score"),
        "store_page": f"https://store.steampowered.com/app/{appid}/",
    }


def lookup(fetcher: ReferenceFetcher, query: str) -> dict[str, Any]:
    """A game by app id, store URL or name; other name matches are listed."""
    query = query.strip()
    in_url = re.search(r"/app/(\d+)", query)
    others: list[dict[str, Any]] = []
    if query.isdigit() or in_url:
        appid = int(in_url.group(1) if in_url else query)
    else:
        found = fetcher.find(query)
        if not found:
            raise FetchError(f"No game on Steam matches {query!r}. Try the exact name or the app id.")
        exact = [f for f in found if f["name"].lower() == query.lower()]
        best = (exact or found)[0]
        appid = int(best["appid"])
        others = [{"appid": f["appid"], "name": f["name"]} for f in found if f["appid"] != appid][:5]
    return {"game": snapshot(fetcher, appid), "other_matches": others}


def peers(files: ProjectFiles | None, appids: list[int] | None) -> list[int]:
    """The games to compare with: the ones given, else the market study's (saved or still pending)."""
    if appids:
        return list(dict.fromkeys(int(a) for a in appids))[:MAX_GAMES]
    if files is not None:
        study = market.load_study(files)
        if study is not None:
            return [p.appid for p in study.peers][:MAX_GAMES]
        pending = market.load_pending(files)
        if pending is not None:
            return [p.appid for p in pending.peers][:MAX_GAMES]
    raise ValueError(
        "Which games? Pass appids=[...] (store_lookup finds them by name), or run study_market first: it finds the "
        "game's closest popular games."
    )


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def _quartiles(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 4:
        return None
    q = statistics.quantiles(values, n=4)
    return round(q[0], 2), round(q[2], 2)


def compare(fetcher: ReferenceFetcher, appids: list[int], own: int | None = None) -> dict[str, Any]:
    games = []
    skipped = []
    for appid in appids:
        try:
            games.append(snapshot(fetcher, appid))
        except FetchError as exc:
            skipped.append(f"{appid}: {exc}")
    paid = [g["price_usd"]["full"] for g in games if g["price_usd"] and g["price_usd"]["full"]]
    reviewed = [g["reviews"]["total"] for g in games if g["reviews"]["total"]]
    positive = [g["reviews"]["positive_share"] for g in games if g["reviews"]["positive_share"] is not None]
    modes = Counter(m for g in games for m in g["modes"])
    features = Counter(f for g in games for f in g["features"])
    n = len(games) or 1
    out: dict[str, Any] = {
        "games": games,
        "overview": {
            "games": len(games),
            "median_price_usd": _median(paid),
            "free_games": sum(1 for g in games if g["free"]),
            "median_reviews": _median([float(r) for r in reviewed]),
            "median_positive_share": _median(positive),
            "modes": {k: round(v / n, 2) for k, v in modes.most_common()},
            "features": {k: round(v / n, 2) for k, v in features.most_common()},
            "early_access": sum(1 for g in games if g["early_access"]),
        },
    }
    if own is not None:
        out["own_appid"] = own
    if skipped:
        out["skipped"] = skipped
    return out


# ---------------------------------------------------------------------------------------------------- prices


def price_brief(
    fetcher: ReferenceFetcher,
    values: dict[str, Any],
    appids: list[int],
    countries: list[str] | None = None,
) -> dict[str, Any]:
    """What the close games charge: their US full prices, and per country how much they charge per US dollar, so a
    base price can be turned into regional prices the way these games did (most follow Valve's recommendations)."""
    countries = [c.lower() for c in (countries or COUNTRIES)]
    if "us" not in countries:
        countries.insert(0, "us")
    us = fetcher.prices(appids, "us")
    paid = {a: p for a, p in us.items() if p and p["full"]}
    full = sorted(p["full"] / 100 for p in paid.values())
    names = {}
    for a in appids:
        try:
            names[a] = fetcher.appdetails(a).data.get("name")
        except FetchError:
            names[a] = str(a)
    own_price = (values.get("pricing") or {}).get("base_price_usd")
    base = float(own_price) if own_price is not None else _median(full)
    regions = []
    for cc in countries:
        local = paid if cc == "us" else fetcher.prices(list(paid), cc)
        rates = []
        currencies: Counter[str] = Counter()
        for a, p in local.items():
            if p and p["full"] and paid[a]["full"]:
                rates.append(p["full"] / paid[a]["full"])
                currencies[p["currency"]] += 1
        if not rates:
            continue
        rate = statistics.median(rates)
        regions.append(
            {
                "country": cc,
                "name": COUNTRY_NAMES.get(cc, cc.upper()),
                "currency": currencies.most_common(1)[0][0],
                "per_usd": round(rate, 3),
                "games": len(rates),
                "for_base_price": round(base * rate, 2) if base is not None else None,
            }
        )
    out: dict[str, Any] = {
        "games": [
            {"appid": a, "name": names.get(a), "full_price_usd": _dollars(p["full"]) if p else None}
            for a, p in us.items()
        ],
        "us_full_prices": {
            "median": _median(full),
            "middle_half": _quartiles(full),
            "lowest": full[0] if full else None,
            "highest": full[-1] if full else None,
            "free_or_unavailable": len(appids) - len(paid),
        },
        "base_price_usd": base,
        "base_price_from": "pricing.base_price_usd" if own_price is not None else "the median of these games",
        "regions": regions,
        "how_to_read": "per_usd: what these games charge in that store per US dollar of their US price (median). "
        "for_base_price: the base price turned into that store's currency the same way. Steamworks suggests "
        "regional prices from the base price too; these numbers show what close games actually charge.",
    }
    return out


# ---------------------------------------------------------------------------------------------------- estimates


def rules_of_thumb() -> dict[str, Any]:
    data: dict[str, Any] = load_yaml("estimates.yaml")
    return data


def estimate_sales(
    fetcher: ReferenceFetcher,
    values: dict[str, Any],
    appids: list[int],
    wishlists: int | None = None,
) -> dict[str, Any]:
    """Rough copies sold for close games from their review counts, and a first-week range for this game from its
    wishlists. Ranges from rules of thumb (data/estimates.yaml), never forecasts."""
    rot = rules_of_thumb()
    mult, conv, cut = rot["review_multiplier"], rot["wishlist_first_week"], float(rot["steam_cut"])
    games = []
    for appid in appids:
        try:
            s = snapshot(fetcher, appid)
        except FetchError:
            continue
        total = s["reviews"]["total"]
        price = (s["price_usd"] or {}).get("full") or 0
        copies = {k: int(total * mult[k]) for k in ("low", "typical", "high")}
        games.append(
            {
                "appid": appid,
                "name": s["name"],
                "released": s["released"],
                "reviews": total,
                "full_price_usd": price or None,
                "copies": copies,
                "gross_usd_at_full_price": {k: int(v * price) for k, v in copies.items()} if price else None,
            }
        )
    out: dict[str, Any] = {
        "games": games,
        "assumptions": {
            "copies_per_review": {k: mult[k] for k in ("low", "typical", "high")},
            "copies_per_review_note": mult["note"],
            "first_week_share_of_wishlists": {k: conv[k] for k in ("low", "typical", "high")},
            "first_week_note": conv["note"],
            "steam_cut": cut,
            "steam_cut_note": rot["steam_cut_note"],
            "gross_note": "Gross at the full US price: discounts, regional prices, refunds and taxes make the real "
            "revenue lower.",
        },
    }
    if wishlists is not None:
        price = (values.get("pricing") or {}).get("base_price_usd")
        discount = (values.get("pricing") or {}).get("launch_discount_percent") or 0
        first_week = {k: int(wishlists * float(conv[k])) for k in ("low", "typical", "high")}
        own: dict[str, Any] = {"wishlists": wishlists, "first_week_copies": first_week}
        if price is not None:
            each = float(price) * (1 - discount / 100)
            own["first_week_gross_usd"] = {k: int(v * each) for k, v in first_week.items()}
            own["first_week_after_steam_cut_usd"] = {k: int(v * each * (1 - cut)) for k, v in first_week.items()}
            own["price_used"] = {"base_price_usd": float(price), "launch_discount_percent": discount}
        out["this_game"] = own
    return out


# ---------------------------------------------------------------------------------------------------- after launch


def _history(files: ProjectFiles) -> Path:
    return files.state_dir / "market" / "launch.jsonl"


def launch_watch(fetcher: ReferenceFetcher, values: dict[str, Any], files: ProjectFiles) -> dict[str, Any]:
    """The released game's reviews, players and price now, against the last check (kept in
    .steam-mcp/market/launch.jsonl)."""
    appid = ((values.get("apps") or {}).get("main") or {}).get("appid")
    if not appid:
        raise ValueError("The main game has no app id yet (apps.main.appid).")
    now = snapshot(fetcher, int(appid), refresh=True)
    entry = {
        "checked_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "reviews": now["reviews"]["total"],
        "positive_share": now["reviews"]["positive_share"],
        "score": now["reviews"]["score"],
        "players_now": now["players_now"],
        "price_usd": (now["price_usd"] or {}).get("now"),
    }
    path = _history(files)
    history = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.is_file()
        else []
    )
    previous = history[-1] if history else None
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    out: dict[str, Any] = {"game": now, "checked_at": entry["checked_at"], "checks_so_far": len(history) + 1}
    if previous:
        out["since_last_check"] = {
            "last_check": previous["checked_at"],
            "new_reviews": (entry["reviews"] or 0) - (previous.get("reviews") or 0),
            "positive_share_change": round((entry["positive_share"] or 0) - (previous.get("positive_share") or 0), 3)
            if entry["positive_share"] is not None and previous.get("positive_share") is not None
            else None,
            "players_then": previous.get("players_now"),
        }
    if peak := max((h.get("players_now") or 0 for h in [*history, entry]), default=0):
        out["highest_players_seen"] = peak
    return out
