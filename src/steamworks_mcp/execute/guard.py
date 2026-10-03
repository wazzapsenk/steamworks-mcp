"""Hard rules for everything the BROWSER mode sends.

``check`` runs before every request this tool makes:

* Nothing that publishes, prepares to publish or reverts is ever requested, not even the Publish page.
* Pages may only be opened on Steam's own domains.
* Writes (non-GET) only go to partner.steamgames.com, and only to the endpoints this tool uses.

``browser_allows`` is the route filter of the browser window itself, which the user also clicks around in (to log
in, for example): publishing, preparing and reverting stay blocked there too, and so does any partner-site write
this tool does not use, apart from signing in.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

NAVIGABLE = re.compile(r"(^|\.)(steamgames\.com|steampowered\.com|steamcommunity\.com)$")
WRITE_HOST = "partner.steamgames.com"
FORBIDDEN = re.compile(r"publish|/apps/(prepare|revert)/|/admin/game/(submit|release)", re.I)
ALLOWED_WRITES = [
    re.compile(
        r"^/apps/(newachievement|saveachievement|setufsparameters|setautocloudpath|setautocloudoverride|setappinstallfolder|setlaunchoption)/\d+$"
    ),
    re.compile(r"^/apps/deleteachievement/\d+/\d+/\d+$"),
    re.compile(r"^/images/uploadachievement$"),
    re.compile(r"^/admin/game/uploadloc/\d+$"),
    re.compile(r"^/apps/diff/\d+$"),  # the Publish page's read-only "View Diffs"
]


LOGIN_PATHS = ("/login/", "/logout")
"""Partner-site writes the sign-in flow makes (setting the session after logging in)."""


class GuardError(PermissionError):
    pass


def check(method: str, url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not NAVIGABLE.search(parts.hostname or ""):
        raise GuardError(f"Refusing {method} {url}: only https pages on Steam's domains.")
    if FORBIDDEN.search(parts.path):
        raise GuardError(f"Refusing {method} {parts.path}: this tool never publishes, prepares or reverts anything.")
    if method.upper() != "GET" and (
        parts.hostname != WRITE_HOST or not any(p.match(parts.path) for p in ALLOWED_WRITES)
    ):
        raise GuardError(f"Refusing {method} {parts.path}: not an endpoint this tool writes to.")


def allowed(method: str, url: str) -> bool:
    try:
        check(method, url)
    except GuardError:
        return False
    return True


def browser_allows(method: str, url: str, resource_type: str = "document") -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return True  # data:, blob: and similar page-internal URLs
    if FORBIDDEN.search(parts.path) and (method.upper() != "GET" or resource_type in ("document", "xhr", "fetch")):
        return False  # static files whose name contains "publish" cannot publish anything themselves
    if method.upper() != "GET" and parts.hostname == WRITE_HOST:
        return any(p.match(parts.path) for p in ALLOWED_WRITES) or parts.path.startswith(LOGIN_PATHS)
    return True
