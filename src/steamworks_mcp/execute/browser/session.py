"""The visible browser window for BROWSER mode (optional ``[browser]`` extra: ``pip install steamworks-mcp[browser]``).

A separate, persistent profile; the user logs in themselves (password + Steam Guard). The only thing checked is
whether the ``steamLoginSecure`` cookie exists; its value is never read. The guard runs as a route filter too, so
nothing in that window can publish, prepare or revert, whoever clicks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from steamworks_mcp.execute import guard
from steamworks_mcp.execute.browser.transport import BASE, PlaywrightTransport


class BrowserUnavailable(RuntimeError):
    pass


def default_profile_dir() -> Path:
    return Path.home() / ".steamworks-mcp" / "browser-profile"


class BrowserSession:
    def __init__(self, profile_dir: Path | None = None) -> None:
        self.profile_dir = profile_dir or default_profile_dir()
        self._pw: Any = None
        self._context: Any = None
        self.page: Any = None

    async def open(self) -> Any:
        if self.page is not None and not self.page.is_closed():
            return self.page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailable("BROWSER mode needs Playwright: pip install 'steamworks-mcp[browser]'") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        errors = []
        for channel in ("chrome", "msedge", None):
            try:
                kwargs: dict[str, Any] = {"headless": False, "viewport": None}
                if channel:
                    kwargs["channel"] = channel
                self._context = await self._pw.chromium.launch_persistent_context(str(self.profile_dir), **kwargs)
                break
            except Exception as exc:  # try the next browser
                errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0]}")
        if self._context is None:
            raise BrowserUnavailable("Could not start a browser (install Chrome or Edge):\n" + "\n".join(errors))

        async def filter_route(route: Any) -> None:
            req = route.request
            if guard.browser_allows(req.method, req.url, req.resource_type):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        await self._context.route("**/*", filter_route)
        self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self.page

    async def show_partner_site(self) -> None:
        """Bring up the Steamworks site (its sign-in page when logged out) for the user."""
        page = await self.open()
        if not str(page.url).startswith(BASE):
            await page.goto(BASE + "/", wait_until="domcontentloaded")
        await page.bring_to_front()

    async def logged_in(self) -> bool:
        if self._context is None:
            return False
        cookies = await self._context.cookies(BASE)
        return any(c.get("name") == "steamLoginSecure" and c.get("value") for c in cookies)

    async def transport(self) -> PlaywrightTransport:
        page = await self.open()
        return PlaywrightTransport(page)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._pw is not None:
            await self._pw.stop()
        self._context = self._pw = self.page = None
