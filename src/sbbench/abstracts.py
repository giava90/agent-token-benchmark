"""Fetch missing abstracts so evidence quality is uniform across claims.

Free, public APIs only: Crossref by DOI, arXiv for 10.48550 DOIs (Crossref
rarely carries their abstracts), Crossref bibliographic search as a last resort.

Crossref's "polite pool" asks for a contact address. We do not send one by
default - set CROSSREF_MAILTO yourself if you want the politer rate limits.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "sbbench/0.1 (citation-grounding benchmark)"
TIMEOUT = 30


def _user_agent() -> str:
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
    return f"{UA} (mailto:{mailto})" if mailto else UA


def _get(url: str, user_agent: str | None = None) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent or _user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception:
        return None


# arXiv rejects unfamiliar agents with HTTP 406.
ARXIV_UA = "Mozilla/5.0 (compatible; sbbench/0.1)"


def _from_inverted_index(inv: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Rebuild the text."""
    if not inv:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in inv.items():
        for p in positions:
            slots.append((p, word))
    slots.sort()
    return _WS.sub(" ", " ".join(w for _, w in slots)).strip()


def from_openalex_doi(doi: str) -> str:
    if not doi:
        return ""
    body = _get("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi, safe=""))
    if not body:
        return ""
    try:
        return _from_inverted_index(json.loads(body).get("abstract_inverted_index"))
    except Exception:
        return ""


def from_openalex_title(title: str) -> str:
    if not title:
        return ""
    q = urllib.parse.urlencode({"filter": f"title.search:{title}", "per-page": 3})
    body = _get("https://api.openalex.org/works?" + q)
    if not body:
        return ""
    try:
        items = json.loads(body).get("results", [])
    except Exception:
        return ""
    want = _norm(title)
    for it in items:
        got = _norm(it.get("title") or "")
        if got and (got == want or (len(want) > 25 and want in got) or (len(got) > 25 and got in want)):
            text = _from_inverted_index(it.get("abstract_inverted_index"))
            if text:
                return text
    return ""


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_abstract(raw: str) -> str:
    """Strip JATS/HTML markup and normalise whitespace."""
    if not raw:
        return ""
    text = _TAG.sub(" ", raw)
    text = html.unescape(text)
    text = _WS.sub(" ", text).strip()
    # Crossref abstracts often begin with a literal "Abstract" heading.
    text = re.sub(r"^(abstract|summary)[:\s]+", "", text, flags=re.I)
    return text


def from_crossref_doi(doi: str) -> str:
    if not doi:
        return ""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    body = _get(url)
    if not body:
        return ""
    try:
        msg = json.loads(body).get("message", {})
    except Exception:
        return ""
    return clean_abstract(msg.get("abstract", ""))


def from_arxiv(doi: str = "", title: str = "") -> str:
    """arXiv Atom API. 10.48550/arXiv.NNNN.NNNNN encodes the arXiv id."""
    arxiv_id = ""
    m = re.search(r"10\.48550/arxiv\.(.+)$", doi, re.I)
    if m:
        arxiv_id = m.group(1)
    if arxiv_id:
        url = f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(arxiv_id)}"
    elif title:
        q = urllib.parse.quote(f'ti:"{title}"')
        url = f"http://export.arxiv.org/api/query?search_query={q}&max_results=1"
    else:
        return ""
    body = _get(url, user_agent=ARXIV_UA)
    if not body:
        return ""
    try:
        root = ET.fromstring(body)
    except Exception:
        return ""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    node = root.find(".//a:entry/a:summary", ns)
    return clean_abstract(node.text or "") if node is not None else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def from_crossref_title(title: str) -> str:
    if not title:
        return ""
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": 3})
    body = _get("https://api.crossref.org/works?" + q)
    if not body:
        return ""
    try:
        items = json.loads(body).get("message", {}).get("items", [])
    except Exception:
        return ""
    want = _norm(title)
    for it in items:
        got = _norm(" ".join(it.get("title", []) or []))
        # Only accept a confident title match; a wrong abstract is worse than none.
        if got and (got == want or (len(want) > 25 and want in got) or (len(got) > 25 and got in want)):
            abs_ = clean_abstract(it.get("abstract", ""))
            if abs_:
                return abs_
    return ""


def fetch(doi: str, title: str, delay: float = 0.4) -> tuple[str, str | None]:
    """Return (abstract, source). Empty string if nothing confident was found.

    Ordered by observed coverage on this bibliography: OpenAlex first (it has
    abstracts for IEEE/ACM material Crossref lacks), arXiv for preprints, then
    title search. A wrong abstract is worse than none, so title matches must be
    confident.
    """
    is_arxiv = bool(doi and re.search(r"10\.48550/arxiv\.", doi, re.I))

    attempts = [
        ("arxiv", lambda: from_arxiv(doi=doi)) if is_arxiv else None,
        ("openalex_doi", lambda: from_openalex_doi(doi)),
        ("crossref_doi", lambda: from_crossref_doi(doi)),
        ("openalex_title", lambda: from_openalex_title(title)),
        ("crossref_title", lambda: from_crossref_title(title)),
        ("arxiv_title", lambda: from_arxiv(title=title)),
    ]
    for item in attempts:
        if item is None:
            continue
        source, fn = item
        text = fn()
        if text and len(text) > 80:  # guard against stub "abstracts"
            return text, source
        time.sleep(delay)
    return "", None
