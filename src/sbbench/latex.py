"""LaTeX source handling: comments, float/math environments, sentences, citations.

Claim extraction is the ground truth for the whole benchmark, so this module errs
towards dropping material it cannot parse confidently rather than emitting a
half-parsed sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# \cite, \citep, \citet, \citealp, ... with optional [..] arguments.
CITE = re.compile(r"\\cite[a-zA-Z]*\s*(?:\[[^\]]*\])*\s*\{([^}]*)\}")

SECTION = re.compile(r"^\s*\\(section|subsection|subsubsection)\*?\s*\{")

# Environments whose contents are not prose claims.
DROP_ENVS = (
    "figure", "figure*", "table", "table*", "tabular", "equation", "equation*",
    "align", "align*", "gather", "gather*", "eqnarray", "eqnarray*",
    "tikzpicture", "lstlisting", "verbatim", "multline", "multline*",
)

_COMMENT = re.compile(r"(?<!\\)%.*$")
_WS = re.compile(r"\s+")

# Abbreviations that must not end a sentence.
_ABBREV = [
    "Sec", "Secs", "Eq", "Eqs", "Fig", "Figs", "Tab", "Tabs", "Ref", "Refs",
    "App", "Ch", "No", "Vol", "pp", "cf", "vs", "resp", "approx", "Prof", "Dr",
    "et al", "e.g", "i.e", "Inc", "Ltd", "St",
]

_DOT = "\x00DOT\x00"


def strip_comments(line: str) -> str:
    return _COMMENT.sub("", line)


def cite_keys(text: str) -> list[str]:
    """Citation keys in source order, de-duplicated."""
    keys: list[str] = []
    for m in CITE.finditer(text):
        for raw in m.group(1).split(","):
            k = raw.strip()
            if k and k not in keys:
                keys.append(k)
    return keys


def strip_citations(text: str) -> str:
    return CITE.sub("", text)


CITATION_MASK = "[CITATION]"


def match_brace(text: str, open_idx: int) -> int:
    """Index of the '}' matching the '{' at open_idx, or -1."""
    depth, i = 0, open_idx
    while i < len(text):
        c = text[i]
        if c == "\\":
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


def drop_command(text: str, names: tuple[str, ...]) -> str:
    """Remove \\name{...} including its (possibly nested) argument."""
    pattern = re.compile(r"\\(" + "|".join(names) + r")\s*\{")
    while True:
        m = pattern.search(text)
        if not m:
            return text
        close = match_brace(text, m.end() - 1)
        if close == -1:
            return text[: m.start()]
        text = text[: m.start()] + " " + text[close + 1 :]


def mask_citations(text: str) -> str:
    """Replace each citation command with a single placeholder.

    Textual citations (\\citet, \\citeauthor) carry the grammar of the sentence -
    deleting them leaves an unreadable hole. Masking keeps the sentence intact and
    marks the slot the reference belongs in, which is exactly what the attribution
    task asks the agent to fill.
    """
    return CITE.sub(CITATION_MASK, text)


def prose_words(text: str) -> int:
    """Alphabetic word count outside math, used to reject formula-only sentences."""
    t = re.sub(r"\$[^$]*\$", " ", text)
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    t = t.replace(CITATION_MASK, " ")
    return len(re.findall(r"[A-Za-z]{2,}", t))


def clean_prose(text: str) -> str:
    """Light normalisation. Math is preserved: claims often turn on a formula."""
    text = drop_command(text, ("footnote", "footnotetext"))
    text = mask_citations(text)
    text = re.sub(r"\\label\s*\{[^}]*\}", "", text)
    text = re.sub(r"\\(ref|eqref|autoref|nameref)\s*\{[^}]*\}", "[ref]", text)
    text = re.sub(r"\\(emph|textit|textbf|textrm|text|mbox)\s*\{([^{}]*)\}", r"\2", text)
    text = text.replace("~", " ")
    text = re.sub(r"\\[,;:!]", " ", text)
    text = re.sub(r"\(\s*[,;]?\s*\)", "", text)  # parens emptied by cite removal
    text = re.sub(r"\[\s*[,;]?\s*\]", "", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return _WS.sub(" ", text).strip()


def split_sentences(text: str) -> list[str]:
    protected = text
    for ab in _ABBREV:
        protected = protected.replace(ab + ".", ab + _DOT)
    protected = re.sub(r"(\d)\.(\d)", r"\1" + _DOT + r"\2", protected)
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [p.replace(_DOT, ".").strip() for p in parts if p.strip()]


@dataclass
class Paragraph:
    line_no: int          # 1-indexed line in the source .tex
    text: str
    section: str
    subsection: str
    subsubsection: str


def _heading_title(line: str) -> str:
    i = line.find("{")
    if i == -1:
        return ""
    depth, j = 0, i
    while j < len(line):
        if line[j] == "{":
            depth += 1
        elif line[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    title = line[i + 1 : j]
    title = re.sub(r"\\label\s*\{[^}]*\}", "", title)
    title = re.sub(r"\\[a-zA-Z]+\s*", "", title)
    return _WS.sub(" ", title).replace("{", "").replace("}", "").strip()


def iter_paragraphs(lines: list[str], start: int, end: int) -> list[Paragraph]:
    """Blank-line-separated paragraphs in lines[start:end], with heading context.

    start/end are 0-indexed, end exclusive. Comments, float and math
    environments are removed.
    """
    out: list[Paragraph] = []
    sec = sub = subsub = ""
    env_depth = 0
    buf: list[str] = []
    buf_line = start + 1

    def flush() -> None:
        nonlocal buf, buf_line
        if buf:
            text = _WS.sub(" ", " ".join(buf)).strip()
            if text:
                out.append(Paragraph(buf_line, text, sec, sub, subsub))
        buf = []

    for idx in range(start, min(end, len(lines))):
        raw = lines[idx]
        line = strip_comments(raw)

        begin = re.search(r"\\begin\s*\{([^}]*)\}", line)
        if begin and begin.group(1) in DROP_ENVS:
            flush()
            env_depth += 1
            continue
        endm = re.search(r"\\end\s*\{([^}]*)\}", line)
        if endm and endm.group(1) in DROP_ENVS:
            env_depth = max(0, env_depth - 1)
            continue
        if env_depth > 0:
            continue

        m = SECTION.match(line)
        if m:
            flush()
            title = _heading_title(line)
            level = m.group(1)
            if level == "section":
                sec, sub, subsub = title, "", ""
            elif level == "subsection":
                sub, subsub = title, ""
            else:
                subsub = title
            continue

        if not line.strip():
            flush()
            continue

        if not buf:
            buf_line = idx + 1
        buf.append(line.strip())

    flush()
    return out


def find_section_bounds(lines: list[str], title_substring: str) -> tuple[int, int]:
    """0-indexed [start, end) of the \\section whose title contains the substring."""
    start = -1
    for i, raw in enumerate(lines):
        line = strip_comments(raw)
        if re.match(r"^\s*\\section\*?\s*\{", line):
            if start == -1 and title_substring.lower() in _heading_title(line).lower():
                start = i
            elif start != -1:
                return start, i
    if start == -1:
        raise ValueError(f"no \\section matching {title_substring!r}")
    return start, len(lines)
