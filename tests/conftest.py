"""Shared helpers for building synthetic 837P EDI fixtures with arbitrary delimiters."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when running pytest from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def build_isa(element: str = "*", subelement: str = ":", repetition: str = "^") -> str:
    """Return a valid 105-char ISA body (no trailing segment terminator).

    Element separator sits at 0-index 103, the component (subelement) separator at
    104, and the segment terminator will land at 105 once segments are joined.
    """
    fields = [
        "00",
        " " * 10,
        "00",
        " " * 10,
        "ZZ",
        "SENDER".ljust(15),
        "ZZ",
        "RECEIVER".ljust(15),
        "260708",
        "0633",
        repetition,
        "00501",
        "000000001",
        "1",
        "P",
        subelement,  # ISA16 = component element separator
    ]
    body = "ISA" + element + element.join(fields)
    assert len(body) == 105, f"ISA body must be 105 chars, got {len(body)}"
    return body


def make_edi(body_segments: list[list[str]], *, element="*", subelement=":", seg_term="~") -> str:
    """Build a full interchange string.

    ``body_segments`` is a list of segments, each a list of elements (the segment
    ID first). ISA/IEA are added automatically here only if not present.
    """
    segs = [element.join(parts) for parts in body_segments]
    isa = build_isa(element, subelement)
    all_segs = [isa] + segs
    return seg_term.join(all_segs) + seg_term


def segments_of(text: str, seg_term: str = "~") -> list[str]:
    return [s for s in text.split(seg_term) if s.strip()]


def clm_ids(text: str, element: str = "*", seg_term: str = "~") -> list[str]:
    ids = []
    for s in segments_of(text, seg_term):
        parts = s.split(element)
        if parts and parts[0].strip() == "CLM" and len(parts) > 1:
            ids.append(parts[1].strip())
    return ids


def hl_segments(text: str, element: str = "*", seg_term: str = "~") -> list[list[str]]:
    return [
        s.split(element)
        for s in segments_of(text, seg_term)
        if s.split(element)[0].strip() == "HL"
    ]
