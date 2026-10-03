"""BBCode -> HTML store preview, with the estimated end of the first screen marked.

The fold height is an estimate (``FOLD_PX``): Valve does not document where "About This Game" collapses, and it
could not be confirmed from recordings (unverified). Pass another height to compare.
"""

from __future__ import annotations

import html
import re

FOLD_PX = 850
"""Estimated visible height of the description before the reader scrolls/expands (unverified)."""

SIMPLE = {
    "b": "strong",
    "i": "em",
    "u": "u",
    "strike": "s",
    "h1": "h1",
    "h2": "h2",
    "h3": "h3",
    "quote": "blockquote",
    "code": "code",
    "p": "p",
}


def bbcode_to_html(text: str) -> str:
    out = html.escape(text)
    out = re.sub(r"\[GIF:\s*([^\]]+)\]", r'<div class="gif">GIF: \1</div>', out, flags=re.I)
    out = re.sub(r"\[img\]([^\[]+)\[/img\]", r'<img src="\1" alt="">', out, flags=re.I)
    out = re.sub(
        r"\[url=[^\]]*\](.*?)\[/url\]",
        r'<span class="hidden-link" title="Steam hides links">\1</span>',
        out,
        flags=re.I | re.S,
    )
    out = re.sub(r"\[hr\]", "<hr>", out, flags=re.I)
    for tag, el in SIMPLE.items():
        out = re.sub(rf"\[{tag}\]", f"<{el}>", out, flags=re.I)
        out = re.sub(rf"\[/{tag}\]", f"</{el}>", out, flags=re.I)
    out = re.sub(r"\[list\]", "<ul>", out, flags=re.I)
    out = re.sub(r"\[/list\]", "</ul>", out, flags=re.I)
    out = re.sub(r"\[olist\]", "<ol>", out, flags=re.I)
    out = re.sub(r"\[/olist\]", "</ol>", out, flags=re.I)
    out = re.sub(r"\[\*\]([^\[<]*)", r"<li>\1</li>", out)
    return out.replace("\n", "<br>")


def store_preview(name: str, short: str, about: str, fold_px: int = FOLD_PX) -> str:
    return f"""<!doctype html><meta charset=utf-8><title>{html.escape(name)}: store preview</title>
<style>
body{{background:#1b2838;color:#acb2b8;font:14px/1.6 "Motiva Sans",Arial,sans-serif;margin:0;padding:24px}}
.page{{max-width:616px;margin:auto}} h1{{color:#fff;font-weight:300}} .short{{color:#c6d4df;margin-bottom:24px}}
.about{{position:relative}} .about h2,.about h3{{color:#fff;font-weight:400;text-transform:uppercase;font-size:14px;

border-bottom:1px solid #2a475e;padding-bottom:4px}}
.gif{{background:#2a475e;color:#66c0f4;padding:28px 12px;text-align:center;
margin:12px 0}} .fold{{position:absolute;left:-8px;right:-8px;top:{fold_px}px;border-top:2px dashed #e84;}}
.fold span{{position:absolute;right:0;top:-20px;color:#e84;font-size:12px}} .hidden-link{{text-decoration:line-through}}
.note{{color:#8f98a0;font-size:12px}}</style>
<div class=page><h1>{html.escape(name)}</h1><div class=short>{html.escape(short)}</div>
<h2>About This Game</h2><div class=about>
<div class=fold><span>estimated first screen ({fold_px}px, unverified)</span></div>
{bbcode_to_html(about)}</div>
<p class=note>Approximation of Steam's styling. Struck-through text is a link Steam would hide.</p></div>"""
