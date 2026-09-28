"""BibTeX parsing.

A brace-matching parser rather than a regex split. The regex approach silently
drops the first entry in a file (no preceding newline to split on), which is the
kind of off-by-one that quietly corrupts a benchmark's ground truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Iterator


@dataclass
class BibEntry:
    key: str
    entry_type: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.fields.get("title", "")

    @property
    def author(self) -> str:
        return self.fields.get("author", "")

    @property
    def year(self) -> str:
        return self.fields.get("year", "")

    @property
    def doi(self) -> str:
        return self.fields.get("doi", "")

    @property
    def abstract(self) -> str:
        return self.fields.get("abstract", "")

    @property
    def venue(self) -> str:
        for f in ("journal", "booktitle", "publisher", "school", "howpublished"):
            if self.fields.get(f):
                return self.fields[f]
        return ""

    def to_dict(self) -> dict:
        return asdict(self)


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the '}' matching the '{' at open_idx, or -1."""
    depth = 0
    i = open_idx
    while i < len(text):
        c = text[i]
        if c == "\\":  # skip escaped char
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_fields(body: str) -> dict[str, str]:
    """Parse 'key = value, key = value' where value is {..}, "..", or bare."""
    out: dict[str, str] = {}
    i = 0
    n = len(body)
    while i < n:
        # field name
        m = re.compile(r"\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*").match(body, i)
        if not m:
            # skip to next comma and retry
            nxt = body.find(",", i)
            if nxt == -1:
                break
            i = nxt + 1
            continue
        name = m.group(1).lower()
        i = m.end()
        if i >= n:
            break
        if body[i] == "{":
            close = _match_brace(body, i)
            if close == -1:
                break
            value = body[i + 1 : close]
            i = close + 1
        elif body[i] == '"':
            j = i + 1
            while j < n and not (body[j] == '"' and body[j - 1] != "\\"):
                j += 1
            value = body[i + 1 : j]
            i = j + 1
        else:
            j = i
            while j < n and body[j] != ",":
                j += 1
            value = body[i:j].strip()
            i = j
        out[name] = _clean_value(value)
        nxt = body.find(",", i)
        if nxt == -1:
            break
        i = nxt + 1
    return out


_WS = re.compile(r"\s+")


def _clean_value(v: str) -> str:
    v = v.replace("\n", " ")
    v = _WS.sub(" ", v).strip()
    # Drop the braces LaTeX uses to protect capitalisation, keep the text.
    v = re.sub(r"\{([^{}]*)\}", r"\1", v)
    return v.strip()


def iter_entries(text: str) -> Iterator[BibEntry]:
    i = 0
    n = len(text)
    while i < n:
        at = text.find("@", i)
        if at == -1:
            return
        m = re.compile(r"@([A-Za-z]+)\s*\{").match(text, at)
        if not m:
            i = at + 1
            continue
        entry_type = m.group(1).lower()
        open_idx = m.end() - 1
        close_idx = _match_brace(text, open_idx)
        if close_idx == -1:
            return
        inner = text[open_idx + 1 : close_idx]
        if entry_type in ("comment", "preamble", "string"):
            i = close_idx + 1
            continue
        comma = inner.find(",")
        if comma == -1:
            i = close_idx + 1
            continue
        key = inner[:comma].strip()
        yield BibEntry(key=key, entry_type=entry_type, fields=_split_fields(inner[comma + 1 :]))
        i = close_idx + 1


def parse_bib(text: str) -> dict[str, BibEntry]:
    out: dict[str, BibEntry] = {}
    for e in iter_entries(text):
        if e.key:
            out[e.key] = e
    return out


def load_bib(path: str) -> dict[str, BibEntry]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_bib(fh.read())
