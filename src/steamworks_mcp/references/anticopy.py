"""Anti-copy rule: generated text must not share a run of 8 or more consecutive words with any reference text.

Texts are compared as lowercase word sequences, without BBCode/HTML tags and punctuation, so formatting changes do
not hide a copied passage.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass

MIN_RUN = 8

_TAG = re.compile(r"\[/?[a-z0-9*]+(?:=[^\]]*)?\]|<[^>]+>", re.I)
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def words(text: str) -> list[str]:
    plain = _TAG.sub(" ", html.unescape(text))
    return [w.lower().replace("’", "'") for w in _WORD.findall(plain)]


@dataclass(frozen=True)
class Overlap:
    reference: str
    """Name of the reference text (e.g. "1234560:about")."""
    words: tuple[str, ...]
    """The longest shared run of words."""
    start: int
    """Word index in the generated text where the run starts."""

    @property
    def length(self) -> int:
        return len(self.words)


def find_overlaps(text: str, references: Mapping[str, str], min_run: int = MIN_RUN) -> list[Overlap]:
    """Maximal shared word runs of at least ``min_run`` words, longest first."""
    gen = words(text)
    if len(gen) < min_run:
        return []
    out: list[Overlap] = []
    for name, ref_text in references.items():
        ref = words(ref_text)
        index: dict[tuple[str, ...], list[int]] = {}
        for i in range(len(ref) - min_run + 1):
            index.setdefault(tuple(ref[i : i + min_run]), []).append(i)
        i = 0
        while i <= len(gen) - min_run:
            starts = index.get(tuple(gen[i : i + min_run]))
            if not starts:
                i += 1
                continue
            best = 0
            for j in starts:
                n = min_run
                while i + n < len(gen) and j + n < len(ref) and gen[i + n] == ref[j + n]:
                    n += 1
                best = max(best, n)
            out.append(Overlap(name, tuple(gen[i : i + best]), i))
            i += best
    return sorted(out, key=lambda o: -o.length)


def is_original(text: str, references: Mapping[str, str], min_run: int = MIN_RUN) -> bool:
    return not find_overlaps(text, references, min_run)
