"""
Core X12 EDI parsing logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ISA segment is fixed at 106 characters in X12 00501
# Positions 104-106 (0-indexed: 103-105) define delimiters for the interchange
ISA_LENGTH = 106
ELEMENT_SEP_POS = 103  # Position 104 (1-indexed)
SUBELEMENT_SEP_POS = 104  # Position 105 (1-indexed)
SEGMENT_TERM_POS = 105  # Position 106 (1-indexed)


@dataclass
class Segment:
    """A parsed X12 segment with ID and elements."""

    id: str
    elements: list[str]

    def get(self, index: int, default: str | None = None) -> str | None:
        """Get element by 1-based index (X12 convention)."""
        if 1 <= index <= len(self.elements):
            val = self.elements[index - 1]
            return val if val else default
        return default

    def __getitem__(self, index: int) -> str | None:
        return self.get(index)


@dataclass
class Delimiters:
    """X12 delimiters extracted from ISA segment."""

    element: str
    subelement: str
    segment_term: str


@dataclass
class ParsedEDI:
    """Result of parsing an X12 EDI file."""

    delimiters: Delimiters
    interchange_control_number: str
    segments: list[Segment]
    raw_segments: list[str] = field(repr=False)

    def get_segments(self, segment_id: str) -> list[Segment]:
        """Return all segments with the given ID (e.g. 'CLM', 'NM1')."""
        return [s for s in self.segments if s.id == segment_id]

    def get_segment(self, segment_id: str) -> Segment | None:
        """Return the first segment with the given ID, or None."""
        for s in self.segments:
            if s.id == segment_id:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert parsed result to a dictionary for easy inspection."""
        return {
            "delimiters": {
                "element": self.delimiters.element,
                "subelement": self.delimiters.subelement,
                "segment_term": self.delimiters.segment_term,
            },
            "interchange_control_number": self.interchange_control_number,
            "segment_count": len(self.segments),
            "segment_ids": list(dict.fromkeys(s.id for s in self.segments)),
            "segments": [
                {"id": s.id, "elements": s.elements} for s in self.segments
            ],
        }


def _extract_delimiters(isa_content: str) -> Delimiters:
    """Extract delimiters from ISA segment (first 106 chars)."""
    if len(isa_content) < ISA_LENGTH:
        raise ValueError(
            f"ISA segment must be at least {ISA_LENGTH} characters, got {len(isa_content)}"
        )
    return Delimiters(
        element=isa_content[ELEMENT_SEP_POS],
        subelement=isa_content[SUBELEMENT_SEP_POS],
        segment_term=isa_content[SEGMENT_TERM_POS],
    )


def _parse_segments(
    content: str, delimiters: Delimiters
) -> tuple[list[Segment], list[str]]:
    """Parse content into segments using the given delimiters."""
    raw = content.split(delimiters.segment_term)
    segments: list[Segment] = []
    raw_segments: list[str] = []

    for raw_seg in raw:
        raw_seg = raw_seg.strip()
        if not raw_seg:
            continue
        raw_segments.append(raw_seg)
        parts = raw_seg.split(delimiters.element)
        if parts:
            segment_id = parts[0].strip()
            elements = [p.strip() for p in parts[1:]]
            segments.append(Segment(id=segment_id, elements=elements))

    return segments, raw_segments


def parse_string(content: str) -> ParsedEDI:
    """
    Parse X12 EDI content from a string.

    Args:
        content: Raw EDI file content.

    Returns:
        ParsedEDI object with segments and metadata.

    Raises:
        ValueError: If content doesn't start with ISA or is invalid.
    """
    # Tolerate a UTF-8 BOM and surrounding whitespace/newlines before the ISA.
    content = content.lstrip("\ufeff").strip()
    if not content.startswith("ISA"):
        raise ValueError("EDI content must start with ISA segment")

    isa_content = content[:ISA_LENGTH]
    delimiters = _extract_delimiters(isa_content)

    # Get interchange control number from ISA (element 13, 0-based index 13)
    isa_elements = content[:ISA_LENGTH].split(delimiters.element)
    interchange_control = isa_elements[13].strip() if len(isa_elements) > 13 else ""

    segments, raw_segments = _parse_segments(content, delimiters)

    return ParsedEDI(
        delimiters=delimiters,
        interchange_control_number=interchange_control.strip(),
        segments=segments,
        raw_segments=raw_segments,
    )
