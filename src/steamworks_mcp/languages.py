"""Steam language codes (see ``data/languages.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from steamworks_mcp.data import load_yaml


@dataclass(frozen=True)
class SteamLanguage:
    api: str
    """Code used by Steamworks pages and the client API, e.g. ``schinese``."""
    web: str
    """Web API code, e.g. ``zh-CN``."""
    name: str


# Common mistakes people (and models) make, and the usual English names.
_ALIASES = {
    "korean": "koreana",
    "chinese": "schinese",
    "simplified chinese": "schinese",
    "traditional chinese": "tchinese",
    "portuguese-brazil": "brazilian",
    "brazilian portuguese": "brazilian",
    "spanish-latam": "latam",
    "latin american spanish": "latam",
    # names in the language itself
    "türkçe": "turkish",
    "turkce": "turkish",
    "deutsch": "german",
    "español": "spanish",
    "espanol": "spanish",
    "français": "french",
    "francais": "french",
    "italiano": "italian",
    "português": "portuguese",
    "portugues": "portuguese",
    "português do brasil": "brazilian",
    "русский": "russian",
    "polski": "polish",
    "čeština": "czech",
    "nederlands": "dutch",
    "svenska": "swedish",
    "norsk": "norwegian",
    "dansk": "danish",
    "suomi": "finnish",
    "magyar": "hungarian",
    "română": "romanian",
    "ελληνικά": "greek",
    "български": "bulgarian",
    "українська": "ukrainian",
    "tiếng việt": "vietnamese",
    "ไทย": "thai",
    "العربية": "arabic",
    "bahasa indonesia": "indonesian",
    "日本語": "japanese",
    "한국어": "koreana",
    "简体中文": "schinese",
    "中文": "schinese",
    "繁體中文": "tchinese",
}


@cache
def all_languages() -> tuple[SteamLanguage, ...]:
    return tuple(SteamLanguage(**row) for row in load_yaml("languages.yaml"))


@cache
def _index() -> dict[str, SteamLanguage]:
    out: dict[str, SteamLanguage] = {}
    for lang in all_languages():
        out[lang.api] = lang
        out[lang.web.lower()] = lang
        out[lang.name.lower()] = lang
    for alias, api in _ALIASES.items():
        out[alias] = out[api]
    return out


def find_language(text: str) -> SteamLanguage | None:
    """Accepts an API code, a Web API code or the English name; returns ``None`` when unknown."""
    return _index().get(text.strip().lower())


def is_api_code(code: str) -> bool:
    return any(lang.api == code for lang in all_languages())
