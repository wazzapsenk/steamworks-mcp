"""Read Steamworks admin pages (Steam Cloud, Installation) from their HTML: named forms and inputs by id."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

SESSION = re.compile(r'g_sessionID\s*=\s*"([^"]+)"')


@dataclass
class Page:
    forms: dict[str, dict[str, Any]] = field(default_factory=dict)
    """form name -> {field name: value}; checkboxes are booleans, selects their selected value."""
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    """element id -> {"name", "value", "checked", "type"} for inputs."""
    named: dict[str, str] = field(default_factory=dict)
    """input name -> value for inputs outside forms or with bracketed names (e.g. Launch_0_description_loc[english])."""
    session_id: str | None = None


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self.form: str | None = None
        self.select: tuple[str, list[tuple[str, bool]]] | None = None
        self.textarea: tuple[str, list[str]] | None = None

    def _put(self, name: str, value: Any) -> None:
        if self.form is not None:
            self.page.forms[self.form].setdefault(name, value)
        if "[" in name or self.form is None:
            self.page.named.setdefault(name, value if isinstance(value, str) else str(value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            self.form = a.get("name") or a.get("id") or f"form{len(self.page.forms)}"
            self.page.forms.setdefault(self.form, {})
        elif tag == "input":
            kind = a.get("type", "text").lower()
            name, value = a.get("name", ""), a.get("value", "")
            checked = "checked" in a
            if a.get("id"):
                self.page.by_id[a["id"]] = {"name": name, "value": value, "checked": checked, "type": kind}
            if not name or kind in ("submit", "button", "image", "reset"):
                return
            self._put(name, checked if kind in ("checkbox", "radio") else value)
        elif tag == "select":
            self.select = (a.get("name", ""), [])
        elif tag == "option" and self.select is not None:
            self.select[1].append((a.get("value", ""), "selected" in a))
        elif tag == "textarea":
            self.textarea = (a.get("name", ""), [])

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.form = None
        elif tag == "select" and self.select is not None:
            name, options = self.select
            chosen = next((v for v, sel in options if sel), options[0][0] if options else "")
            if name:
                self._put(name, chosen)
            self.select = None
        elif tag == "textarea" and self.textarea is not None:
            name, parts = self.textarea
            if name:
                self._put(name, "".join(parts))
            self.textarea = None

    def handle_data(self, data: str) -> None:
        if self.textarea is not None:
            self.textarea[1].append(data)


def parse(text: str) -> Page:
    p = _Parser()
    p.feed(text)
    p.close()
    m = SESSION.search(text)
    p.page.session_id = html_lib.unescape(m.group(1)) if m else None
    return p.page
