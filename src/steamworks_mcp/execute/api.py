"""Official tooling: the partner Web API (publisher key) and steamcmd / SteamPipe.

The key comes from the environment and never appears in results, errors or logs. steamcmd is run with the builder
account's name only: its password and Steam Guard code are never handled here (log in once interactively; steamcmd
keeps the session).

Parameters follow partner.steamgames.com/doc/webapi/ISteamLeaderboards and /ISteamApps. Not verified against a real
key yet (see docs/STEAMWORKS_INTERNALS.md): the display-type strings and the exact response shapes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

PARTNER_API = "https://partner.steam-api.com"
DISPLAY_TYPES = {"numeric": "Numeric", "seconds": "TimeSeconds", "milliseconds": "TimeMilliSeconds"}
"""steamworks.yaml display types -> the names the SDK uses (k_ELeaderboardDisplayType*); unverified for the Web API."""


class SteamApiError(RuntimeError):
    pass


class PartnerApi:
    def __init__(self, key: str, client: httpx.Client | None = None) -> None:
        if not key:
            raise SteamApiError(
                "STEAMWORKS_PUBLISHER_KEY is not set (Users & Permissions > Manage Groups > Web API key)."
            )
        self._key = key
        self.client = client or httpx.Client(timeout=30)

    def __repr__(self) -> str:
        return "PartnerApi(key=<hidden>)"

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "<key>")

    def _request(self, method: str, path: str, params: dict[str, Any]) -> tuple[int, Any]:
        data = {"key": self._key, **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items()}}
        try:
            if method == "GET":
                res = self.client.get(PARTNER_API + path, params=data)
            else:
                res = self.client.post(PARTNER_API + path, data=data)
        except httpx.HTTPError as exc:
            raise SteamApiError(self._scrub(f"{path}: {exc}")) from None
        if res.status_code in (401, 403):
            raise SteamApiError(
                f"{path}: HTTP {res.status_code} - the publisher key was rejected or has no access to this app."
            )
        if res.status_code >= 400:
            raise SteamApiError(self._scrub(f"{path}: HTTP {res.status_code} {res.text[:200]}"))
        if not res.content:
            return res.status_code, {}
        try:
            return res.status_code, res.json()
        except ValueError:
            raise SteamApiError(self._scrub(f"{path}: expected JSON, got {res.text[:200]}")) from None

    def _call(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        data = self._request(method, path, params)[1]
        return data if isinstance(data, dict) else {}

    # reads
    def schema(self, appid: int) -> dict[str, Any]:
        return dict(
            self._call("GET", "/ISteamUserStats/GetSchemaForGame/v2/", {"appid": appid, "l": "english"}).get("game")
            or {}
        )

    def builds(self, appid: int, count: int = 10) -> dict[str, Any]:
        return dict(
            self._call("GET", "/ISteamApps/GetAppBuilds/v1/", {"appid": appid, "count": count}).get("response") or {}
        )

    def betas(self, appid: int) -> dict[str, Any]:
        return dict(self._call("GET", "/ISteamApps/GetAppBetas/v1/", {"appid": appid}).get("response") or {})

    def leaderboards(self, appid: int) -> list[dict[str, Any]]:
        res = (
            self._call("GET", "/ISteamLeaderboards/GetLeaderboardsForGame/v2/", {"appid": appid}).get("response") or {}
        )
        return list(res.get("leaderboards") or [])

    # writes
    def find_or_create_leaderboard(self, appid: int, board: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST",
            "/ISteamLeaderboards/FindOrCreateLeaderboard/v2/",
            {
                "appid": appid,
                "name": board["name"],
                "sortmethod": "Ascending" if board.get("sort_method") == "ascending" else "Descending",
                "displaytype": DISPLAY_TYPES[board.get("display_type") or "numeric"],
                "createifnotfound": True,
                "onlytrustedwrites": bool(board.get("only_trusted_writes")),
                "onlyfriendsreads": bool(board.get("only_friends_reads")),
            },
        )

    def delete_leaderboard(self, appid: int, name: str) -> dict[str, Any]:
        return self._call("POST", "/ISteamLeaderboards/DeleteLeaderboard/v1/", {"appid": appid, "name": name})

    def set_build_live(self, appid: int, build_id: int, beta_key: str, description: str = "") -> dict[str, Any]:
        """Beta branches only: "public" (the default branch, what every player gets) is refused here; the user sets
        it live in App Admin. Steam answers HTTP 201 when a change waits for a Steam Mobile confirmation."""
        if beta_key.strip().lower() in ("public", "default", ""):
            raise SteamApiError(
                "This tool never sets the default branch live; do it in Steamworks (SteamPipe > Builds)."
            )
        params = {"appid": appid, "buildid": build_id, "betakey": beta_key, "description": description}
        status, data = self._request("POST", "/ISteamApps/SetAppBuildLive/v2/", params)
        return {
            "needs_mobile_confirmation": status == 201,
            "response": data.get("response", data) if isinstance(data, dict) else data,
        }


def _board_settings(b: dict[str, Any]) -> dict[str, Any]:
    """Settings of a leaderboard as GetLeaderboardsForGame reports them, in steamworks.yaml vocabulary."""
    display = str(b.get("displaytype") or b.get("display_type") or "Numeric").lower()
    return {
        "sort_method": "ascending"
        if str(b.get("sortmethod") or b.get("sort_method") or "").lower().startswith("asc")
        else "descending",
        "display_type": "milliseconds" if "milli" in display else "seconds" if "second" in display else "numeric",
        "only_trusted_writes": bool(b.get("onlytrustedwrites") or b.get("only_trusted_writes")),
        "only_friends_reads": bool(b.get("onlyfriendsreads") or b.get("only_friends_reads")),
    }


SETTINGS = ("sort_method", "display_type", "only_trusted_writes", "only_friends_reads")


def plan_leaderboards(
    desired: list[dict[str, Any]], current: list[dict[str, Any]], remove_extra: bool
) -> dict[str, Any]:
    """Boards to create (and delete, with ``remove_extra``). Settings of an existing board cannot be changed through
    the API without deleting it and its scores, so differences are only reported."""
    by_name = {str(b.get("name")): b for b in current if b.get("name")}
    create, differs = [], []
    for d in desired:
        want = {k: d.get(k) for k in SETTINGS}
        want = {**_board_settings({}), **{k: v for k, v in want.items() if v is not None}}
        cur = by_name.get(d["name"])
        if cur is None:
            create.append({"name": d["name"], **want})
        elif _board_settings(cur) != want:
            differs.append({"name": d["name"], "steam": _board_settings(cur), "steamworks_yaml": want})
    wanted = {d["name"] for d in desired}
    delete = sorted(n for n in by_name if n not in wanted) if remove_extra else []
    return {"create": create, "delete": delete, "settings_differ": differs}


# ---------------------------------------------------------------------------------------------------- steamcmd

SECRET_LINES = re.compile(r"(?im)^.*(password|passwd|auth code|two-factor|guard code|token|ssfn).*$")


def run_steamcmd(steamcmd: str, username: str, script: Path, timeout: int = 3600) -> dict[str, Any]:
    """``steamcmd +login <user> +run_app_build <script> +quit`` without a password (a cached login is required)."""
    if not Path(steamcmd).is_file():
        raise SteamApiError(f"steamcmd not found at {steamcmd} (set STEAMCMD_PATH).")
    if not username or not re.fullmatch(r"[A-Za-z0-9_.@-]+", username):
        raise SteamApiError("STEAMCMD_USERNAME is not set to a valid account name (the restricted builder account).")
    if not script.is_file():
        raise SteamApiError(f"Build script not found: {script}")
    cmd = [steamcmd, "+login", username, "+run_app_build", str(script), "+quit"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False
        )
    except subprocess.TimeoutExpired:
        raise SteamApiError(f"steamcmd did not finish within {timeout} s.") from None
    out = proc.stdout + proc.stderr
    needs_login = proc.returncode != 0 and bool(re.search(r"password|Steam Guard|two-factor|Login Failure", out, re.I))
    out = SECRET_LINES.sub("<line hidden>", out).replace(username, "<builder account>")
    build = re.search(r"BuildID\s+(\d+)", out)
    return {
        "exit_code": proc.returncode,
        "success": proc.returncode == 0 and "Successfully finished AppID" in out,
        "build_id": int(build.group(1)) if build else None,
        "needs_interactive_login": needs_login,
        "log_tail": out[-4000:],
    }
