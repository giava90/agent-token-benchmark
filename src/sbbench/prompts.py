"""Prompt assembly.

The split matters more than the wording: everything byte-stable goes in the
cached prefix, everything per-claim goes in the tail. Get that ordering wrong
and no amount of cache_control markers helps.
"""

from __future__ import annotations

SYSTEM_VERIFY = """You check whether a citation in a scientific review is correctly placed.

The bibliography below is your reference library. You are given one claim
sentence from the review, with [CITATION] where its reference was removed, and
the citation key of one candidate reference. Look that key up in the
bibliography and decide whether the work it names supports the claim.

Reply with exactly one word on the first line: SUPPORTED or NOT_SUPPORTED.
Then one short sentence giving your reason."""

SYSTEM_ATTRIBUTE = """You attribute claims in a scientific review to their sources.

You are given one claim sentence with [CITATION] marking where its reference
was removed, and a bibliography. Name the reference(s) the review cites there.

Reply with the citation key(s) only, comma-separated, on the first line."""


def format_reference(ref: dict, include_abstract: bool = True) -> str:
    parts = [f"[{ref['key']}]"]
    if ref.get("title"):
        parts.append(ref["title"] + ".")
    if ref.get("author"):
        parts.append(ref["author"] + ".")
    meta = " ".join(x for x in (ref.get("venue", ""), str(ref.get("year", ""))) if x)
    if meta:
        parts.append(meta + ".")
    if include_abstract and ref.get("abstract"):
        parts.append("Abstract: " + ref["abstract"])
    elif include_abstract:
        parts.append("Abstract: (not available)")
    return " ".join(parts)


def bibliography_block(refs: list[dict], include_abstract: bool = True) -> str:
    """The shared, byte-stable prefix. Sorted so the bytes never move."""
    ordered = sorted(refs, key=lambda r: r["key"])
    return "\n\n".join(format_reference(r, include_abstract) for r in ordered)


def verify_prefix(refs: list[dict], include_abstract: bool = True) -> str:
    """System prompt + bibliography: the part we want cached."""
    return SYSTEM_VERIFY + "\n\nBIBLIOGRAPHY\n\n" + bibliography_block(refs, include_abstract)


def verify_tail(claim_text: str, ref: dict, include_abstract: bool = True,
                mode: str = "lookup") -> str:
    """The per-claim remainder, after the cache breakpoint.

    mode="lookup" (default) sends only the citation key, so the model must
    resolve it against the cached bibliography. That is what makes the cached
    prefix load-bearing: with the full reference inlined here instead, the
    bibliography is dead weight and any caching gain measured over it is an
    artefact of the prompt rather than of the task.

    mode="inline" keeps the old behaviour and exists only to measure the
    difference between the two framings.
    """
    if mode == "inline":
        candidate = format_reference(ref, include_abstract)
    else:
        candidate = f"[{ref['key']}]"
    return (
        "CLAIM\n" + claim_text
        + "\n\nCANDIDATE CITATION KEY\n" + candidate
        + "\n\nDoes that reference support the claim?"
    )


def attribute_prefix(refs: list[dict], include_abstract: bool = True) -> str:
    return SYSTEM_ATTRIBUTE + "\n\nBIBLIOGRAPHY\n\n" + bibliography_block(refs, include_abstract)


def attribute_tail(claim_text: str, n_expected: int) -> str:
    return (
        "CLAIM\n" + claim_text
        + f"\n\nThis claim cites {n_expected} reference(s). Give the key(s)."
    )
