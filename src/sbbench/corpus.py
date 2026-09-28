"""Build the claim corpus and reference index from the review sources."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict

from . import latex
from .bib import BibEntry, load_bib

# The review lives outside this repo; override with SBREVIEW_DIR.
DEFAULT_REVIEW_DIR = r"C:\Users\giacomov\OneDrive - ETH Zurich\Dokumente\git\SBreview"
TEX_NAME = "quantyfyingSB_v2.tex"
BIB_NAME = "references.bib"


def review_dir() -> str:
    return os.environ.get("SBREVIEW_DIR", DEFAULT_REVIEW_DIR)


@dataclass
class Claim:
    claim_id: str
    text: str            # prose with citations removed - what the agent sees
    text_raw: str        # original LaTeX, citations intact
    gold_keys: list[str]
    section: str
    subsection: str
    subsubsection: str
    line_no: int
    n_keys: int

    def to_dict(self) -> dict:
        return asdict(self)


def extract_claims(
    tex_path: str,
    section_substring: str = "Measurement",
    id_prefix: str = "meas",
    min_prose_words: int = 8,
) -> list[Claim]:
    with open(tex_path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")

    start, end = latex.find_section_bounds(lines, section_substring)
    claims: list[Claim] = []
    seq = 0

    for para in latex.iter_paragraphs(lines, start, end):
        # Footnotes go before sentence splitting: their citations are asides, not
        # claims, and their internal punctuation confuses the splitter.
        body = latex.drop_command(para.text, ("footnote", "footnotetext"))
        for sent in latex.split_sentences(body):
            keys = latex.cite_keys(sent)
            if not keys:
                continue
            prose = latex.clean_prose(sent)
            if latex.prose_words(prose) < min_prose_words:
                continue
            seq += 1
            claims.append(
                Claim(
                    claim_id=f"{id_prefix}-{seq:04d}",
                    text=prose,
                    text_raw=sent,
                    gold_keys=keys,
                    section=para.section,
                    subsection=para.subsection,
                    subsubsection=para.subsubsection,
                    line_no=para.line_no,
                    n_keys=len(keys),
                )
            )
    return claims


def reference_index(bib: dict[str, BibEntry], keys: set[str] | None = None) -> list[dict]:
    out = []
    for key, e in bib.items():
        if keys is not None and key not in keys:
            continue
        out.append(
            {
                "key": key,
                "entry_type": e.entry_type,
                "title": e.title,
                "author": e.author,
                "year": e.year,
                "venue": e.venue,
                "doi": e.doi,
                "abstract": e.abstract,
                "abstract_source": "bib" if e.abstract else None,
            }
        )
    out.sort(key=lambda r: r["key"].lower())
    return out


def write_jsonl(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_sources() -> tuple[str, dict[str, BibEntry]]:
    d = review_dir()
    tex = os.path.join(d, TEX_NAME)
    bib = os.path.join(d, BIB_NAME)
    for p in (tex, bib):
        if not os.path.exists(p):
            raise FileNotFoundError(p)
    return tex, load_bib(bib)
