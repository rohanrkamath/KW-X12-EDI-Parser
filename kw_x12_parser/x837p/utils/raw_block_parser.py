"""
837P full-fidelity parser: preserves every segment and loop for repackaging.

Supports hold/release at the individual claim (CLM loop) level: parse -> filter
-> repackage EDI keeping only the requested claims, while preserving the shared
billing-provider / subscriber / patient HL hierarchy and producing structurally
valid envelopes (ST/SE, GS/GE, ISA/IEA).

Design
------
The interchange is modelled as a real tree so filtering happens per CLM loop,
not per HL block:

    Interchange (ISA/IEA)
      RawGroup (GS/GE)
        RawTransaction (ST/SE)
          header_segments        # ST, BHT, Loop 1000A/1000B (before first HL)
          RawHLBlock*            # one per HL
            shared_segments      # HL + everything before the first CLM
            RawClaimLoop*        # one per CLM (CLM .. next CLM/HL/SE)

Claims are detected by the presence of CLM segments (not by HL03 == "22"), so a
claim under a patient/dependent HL is handled correctly. Every CLM occurrence is
retained independently, so duplicate CLM01 values are all preserved (see
``filter`` semantics below).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .segment_parser import parse_string, Delimiters
from .hierarchical_parser import _parse_837p_from_segments
from .claim_models import (
    Parsed837P,
    Segment,
    SubscriberClaim,
)


# --------------------------------------------------------------------------- #
# Small raw-segment helpers (delimiter-driven, never hardcoded)
# --------------------------------------------------------------------------- #
def _seg_id(raw_seg: str, elem_sep: str) -> str:
    """Return the segment ID (first element) of a raw segment string."""
    return raw_seg.split(elem_sep, 1)[0].strip()


def _seg_elem(raw_seg: str, idx: int, elem_sep: str) -> str:
    """Return element ``idx`` (0-based) of a raw segment, or '' if absent."""
    parts = raw_seg.split(elem_sep)
    return parts[idx].strip() if 0 <= idx < len(parts) else ""


def _set_seg_elem(raw_seg: str, idx: int, value: str, elem_sep: str) -> str:
    """Return raw_seg with element ``idx`` (0-based) set to ``value`` (padding if needed)."""
    parts = raw_seg.split(elem_sep)
    while len(parts) <= idx:
        parts.append("")
    parts[idx] = value
    return elem_sep.join(parts)


def _extract_claim_id_from_raw(raw: str, elem_sep: str, seg_term: str) -> str | None:
    """Extract the first CLM01 from raw block content (kept for backward compat)."""
    for raw_seg in raw.split(seg_term):
        raw_seg = raw_seg.strip()
        if not raw_seg:
            continue
        parts = raw_seg.split(elem_sep)
        if parts and parts[0].strip() == "CLM":
            return parts[1].strip() if len(parts) > 1 else None
    return None


def _extract_all_clm_ids(text: str, elem_sep: str, seg_term: str) -> list[str]:
    """Collect every CLM01 (in order, with duplicates) from an EDI string."""
    ids: list[str] = []
    for raw_seg in text.split(seg_term):
        raw_seg = raw_seg.strip()
        if not raw_seg:
            continue
        parts = raw_seg.split(elem_sep)
        if parts and parts[0].strip() == "CLM" and len(parts) > 1:
            ids.append(parts[1].strip())
    return ids


# --------------------------------------------------------------------------- #
# Raw structural model
# --------------------------------------------------------------------------- #
@dataclass
class RawClaimLoop:
    """A single claim loop: the CLM segment and everything up to the next
    CLM / HL / transaction trailer."""

    claim_id: str
    segments: list[str] = field(default_factory=list)  # raw segment strings, CLM first


@dataclass
class RawHLBlock:
    """One HL block. ``shared_segments`` is the HL segment plus every segment
    before the first CLM (subscriber/patient/provider level content). Individual
    claims live in ``claim_loops``."""

    hl_id: str
    parent_id: str | None
    level_code: str            # HL03
    child_code: str            # HL04 (original)
    shared_segments: list[str] = field(default_factory=list)
    claim_loops: list[RawClaimLoop] = field(default_factory=list)

    @property
    def claim_ids(self) -> list[str]:
        return [c.claim_id for c in self.claim_loops]


@dataclass
class RawTransaction:
    """One ST*837 ... SE transaction set."""

    header_segments: list[str] = field(default_factory=list)  # ST .. before first HL
    hl_blocks: list[RawHLBlock] = field(default_factory=list)
    se_segment: str = ""


@dataclass
class RawGroup:
    """One GS ... GE functional group."""

    gs_segment: str = ""
    transactions: list[RawTransaction] = field(default_factory=list)
    ge_segment: str = ""


# --------------------------------------------------------------------------- #
# Backward-compat block model (still populated for external/debug use)
# --------------------------------------------------------------------------- #
@dataclass
class EdiBlock:
    """One HL block with full raw EDI content. Retained for backward
    compatibility; claim-level filtering now uses the RawHLBlock model."""

    hl_id: str
    parent_id: str | None
    level_code: str
    raw_content: str
    claim_id: str | None = None  # first CLM01 in the block, if any


class ClaimFilterError(ValueError):
    """Raised when requested claims cannot be satisfied or the rebuilt EDI is invalid."""


@dataclass
class Parsed837PFull(Parsed837P):
    """
    Full-fidelity 837P parse: every segment and loop preserved, structured as a
    real interchange tree so claims can be filtered individually.
    """

    delimiters: Delimiters = field(default_factory=lambda: Delimiters("*", ":", "~"))
    raw_isa: str = ""
    raw_iea: str = ""
    groups: list[RawGroup] = field(default_factory=list)

    # Backward-compat flat fields (first occurrence / best effort)
    raw_gs: str = ""
    raw_header: str = ""
    raw_blocks: list[EdiBlock] = field(default_factory=list)
    raw_se: str = ""
    raw_ge: str = ""

    # Every segment in the file (ISA through IEA) in document order
    complete_segments: list[Segment] = field(default_factory=list, repr=False)
    # Raw segment strings in order (from parse_string)
    raw_segment_list: list[str] = field(default_factory=list, repr=False)

    # --------------------------------------------------------------------- #
    # Introspection
    # --------------------------------------------------------------------- #
    def iter_every_segment(self):
        """Yield every segment in the file (ISA through IEA) in document order."""
        for seg in self.complete_segments:
            yield seg

    def get_all_claim_ids(self) -> list[str]:
        """Return every claim ID in source order (duplicates preserved)."""
        return [
            c.claim_id
            for g in self.groups
            for txn in g.transactions
            for hl in txn.hl_blocks
            for c in hl.claim_loops
        ]

    def get_claim_by_id(self, claim_id: str) -> SubscriberClaim | None:
        """Get the first SubscriberClaim for a given claim_id (from base parse)."""
        for bp in self.billing_providers:
            for c in bp.claims:
                if c.claim_id == claim_id:
                    return c
        return None

    def iter_all_segments_per_claim(self):
        """Yield (claim_id, segment) for every segment in every claim (base parse)."""
        for bp in self.billing_providers:
            for claim in bp.claims:
                for seg in claim.segments:
                    yield claim.claim_id, seg
                for sl in claim.service_lines:
                    for seg in sl.segments:
                        yield claim.claim_id, seg

    # --------------------------------------------------------------------- #
    # Reconstruction / filtering
    # --------------------------------------------------------------------- #
    def _rebuild_transaction(
        self,
        txn: RawTransaction,
        keep: Callable[[str], bool],
    ) -> str | None:
        """
        Rebuild one ST..SE transaction keeping only claim loops for which
        ``keep(claim_id)`` is True, plus the ancestor HL hierarchy required by
        those claims. Returns the transaction string, or None if it retains no
        claims.
        """
        e = self.delimiters.element
        t = self.delimiters.segment_term

        hl_by_id: dict[str, RawHLBlock] = {hl.hl_id: hl for hl in txn.hl_blocks}

        # 1. Retained claim loops per HL (source order preserved).
        retained_loops: dict[str, list[RawClaimLoop]] = {}
        directly_retained: set[str] = set()
        for hl in txn.hl_blocks:
            kept = [c for c in hl.claim_loops if keep(c.claim_id)]
            retained_loops[hl.hl_id] = kept
            if kept:
                directly_retained.add(hl.hl_id)

        if not directly_retained:
            return None

        # 2. Walk parent chains to retain every required ancestor HL.
        retained_hls: set[str] = set(directly_retained)
        for hid in list(directly_retained):
            cur: RawHLBlock | None = hl_by_id.get(hid)
            while cur is not None and cur.parent_id and cur.parent_id in hl_by_id:
                pid = cur.parent_id
                already = pid in retained_hls
                retained_hls.add(pid)
                if already:
                    break  # this ancestor's chain was already retained
                cur = hl_by_id.get(pid)

        ordered = [hl for hl in txn.hl_blocks if hl.hl_id in retained_hls]

        # 3. Sequential HL renumbering (original order) + parent remap.
        old_to_new = {hl.hl_id: str(i) for i, hl in enumerate(ordered, start=1)}

        # 4. HL04 child indicator: does any retained HL still reference this HL as parent?
        parents_with_children: set[str] = {
            hl.parent_id
            for hl in ordered
            if hl.parent_id and hl.parent_id in retained_hls
        }

        body: list[str] = []
        for hl in ordered:
            hl_seg = hl.shared_segments[0]
            hl_seg = _set_seg_elem(hl_seg, 1, old_to_new[hl.hl_id], e)  # HL01
            new_parent = old_to_new.get(hl.parent_id, "") if hl.parent_id else ""
            hl_seg = _set_seg_elem(hl_seg, 2, new_parent, e)           # HL02
            hl_seg = _set_seg_elem(
                hl_seg, 4, "1" if hl.hl_id in parents_with_children else "0", e
            )  # HL04
            body.append(hl_seg)
            body.extend(hl.shared_segments[1:])
            for loop in retained_loops[hl.hl_id]:
                body.extend(loop.segments)

        # 5. Recalculate SE01 = number of segments from ST through SE inclusive.
        seg_list = list(txn.header_segments) + body + [txn.se_segment]
        se_seg = _set_seg_elem(txn.se_segment, 1, str(len(seg_list)), e)  # SE01
        seg_list[-1] = se_seg  # SE02 preserved (== ST02)

        return t.join(seg_list)

    def to_edi_string(
        self,
        *,
        exclude_claim_ids: set[str] | None = None,
        include_claim_ids: set[str] | None = None,
        include_claim_ids_fn: Callable[[str], bool] | None = None,
        isa15_usage_indicator: str | None = None,
    ) -> str:
        """
        Repackage the EDI, filtering at the individual claim (CLM loop) level.

        Args:
            exclude_claim_ids: Claim IDs to omit (held claims).
            include_claim_ids: If set, only these claim IDs are emitted (released claims).
                Every requested ID must exist in the source or ClaimFilterError is raised.
            include_claim_ids_fn: Callable(claim_id) -> bool; claim kept only when True.
            isa15_usage_indicator: Optional ISA15 override ("T" test / "P" production).

        Precedence (a claim is emitted only if all provided filters agree):
            not in exclude AND (include is None or in include) AND (fn is None or fn(id)).

        Duplicate CLM01: every occurrence matching the filters is retained.

        Returns:
            Valid 837P EDI string containing exactly the retained claims.

        Raises:
            ClaimFilterError: requested include IDs missing from source, no claims
                retained, or the rebuilt EDI fails output validation.
        """
        e = self.delimiters.element
        t = self.delimiters.segment_term
        exclude = exclude_claim_ids or set()
        include = include_claim_ids
        fn = include_claim_ids_fn

        def keep(cid: str) -> bool:
            if cid in exclude:
                return False
            if include is not None and cid not in include:
                return False
            if fn is not None and not fn(cid):
                return False
            return True

        # Source claim IDs (order + duplicates preserved) and validation of requests.
        source_ids = self.get_all_claim_ids()
        source_set = set(source_ids)
        if include is not None:
            missing = sorted(cid for cid in include if cid not in source_set)
            if missing:
                raise ClaimFilterError(
                    "Requested claim IDs not found in source EDI: " + ", ".join(missing)
                )

        expected = [cid for cid in source_ids if keep(cid)]
        if not expected:
            raise ClaimFilterError(
                "No claims retained after filtering; refusing to write empty/header-only EDI."
            )

        # Rebuild envelope: ISA, groups (GS, retained ST..SE, GE), IEA.
        raw_isa = self.raw_isa
        if isa15_usage_indicator is not None:
            raw_isa = _set_seg_elem(raw_isa, 15, isa15_usage_indicator, e)  # ISA15

        parts: list[str] = [raw_isa]
        retained_group_count = 0
        for g in self.groups:
            rebuilt_txns = [
                s for s in (self._rebuild_transaction(txn, keep) for txn in g.transactions)
                if s is not None
            ]
            if not rebuilt_txns:
                continue
            retained_group_count += 1
            parts.append(g.gs_segment)
            parts.extend(rebuilt_txns)
            ge_seg = g.ge_segment
            if ge_seg:
                ge_seg = _set_seg_elem(ge_seg, 1, str(len(rebuilt_txns)), e)  # GE01
            parts.append(ge_seg)

        if retained_group_count == 0:
            raise ClaimFilterError("No transaction sets retained after filtering.")

        iea_seg = self.raw_iea
        if iea_seg:
            iea_seg = _set_seg_elem(iea_seg, 1, str(retained_group_count), e)  # IEA01
        parts.append(iea_seg)

        result = t.join(p for p in parts if p != "")

        self._validate_output(result, expected, include, e, t)
        return result

    def _validate_output(
        self,
        result: str,
        expected: list[str],
        include: set[str] | None,
        elem_sep: str,
        seg_term: str,
    ) -> None:
        """Guarantee the rebuilt EDI contains exactly the expected claims and reparses."""
        out_ids = _extract_all_clm_ids(result, elem_sep, seg_term)
        if out_ids != expected:
            raise ClaimFilterError(
                "Output claim validation failed: "
                f"expected {expected}, got {out_ids}"
            )
        if include is not None:
            unrequested = sorted({c for c in out_ids if c not in include})
            if unrequested:
                raise ClaimFilterError(
                    "Output contains unrequested claims: " + ", ".join(unrequested)
                )
        try:
            reparsed = parse_string(result)
        except Exception as ex:  # pragma: no cover - defensive
            raise ClaimFilterError(f"Rebuilt EDI failed to reparse: {ex}") from ex
        reparsed_ids = _extract_all_clm_ids(
            seg_term.join(reparsed.raw_segments), elem_sep, seg_term
        )
        if reparsed_ids != expected:
            raise ClaimFilterError(
                "Reparsed EDI claim set mismatch: "
                f"expected {expected}, got {reparsed_ids}"
            )

    def write_edi(
        self,
        path: str | Path,
        *,
        exclude_claim_ids: set[str] | None = None,
        include_claim_ids: set[str] | None = None,
        include_claim_ids_fn: Callable[[str], bool] | None = None,
        isa15_usage_indicator: str | None = None,
    ) -> None:
        """Write repackaged EDI to file. See to_edi_string() for args and errors.

        The file is only written after all validation passes, so an incomplete or
        empty EDI is never produced.
        """
        content = self.to_edi_string(
            exclude_claim_ids=exclude_claim_ids,
            include_claim_ids=include_claim_ids,
            include_claim_ids_fn=include_claim_ids_fn,
            isa15_usage_indicator=isa15_usage_indicator,
        )
        Path(path).write_text(content, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _build_raw_tree(
    raw_list: list[str], elem_sep: str
) -> tuple[str, list[RawGroup], str]:
    """Split the raw segment list into (ISA, [RawGroup], IEA) with per-claim loops."""
    raw_isa = ""
    raw_iea = ""
    groups: list[RawGroup] = []

    cur_group: RawGroup | None = None
    cur_txn: RawTransaction | None = None
    cur_hl: RawHLBlock | None = None
    cur_claim: RawClaimLoop | None = None

    for raw in raw_list:
        sid = _seg_id(raw, elem_sep)

        if sid == "ISA":
            raw_isa = raw
        elif sid == "IEA":
            raw_iea = raw
            cur_group = cur_txn = cur_hl = cur_claim = None
        elif sid == "GS":
            cur_group = RawGroup(gs_segment=raw)
            groups.append(cur_group)
            cur_txn = cur_hl = cur_claim = None
        elif sid == "GE":
            if cur_group is not None:
                cur_group.ge_segment = raw
            cur_group = cur_txn = cur_hl = cur_claim = None
        elif sid == "ST":
            cur_txn = RawTransaction(header_segments=[raw])
            if cur_group is not None:
                cur_group.transactions.append(cur_txn)
            cur_hl = cur_claim = None
        elif sid == "SE":
            if cur_txn is not None:
                cur_txn.se_segment = raw
            cur_txn = cur_hl = cur_claim = None
        elif sid == "HL":
            parts = raw.split(elem_sep)
            hl_id = parts[1].strip() if len(parts) > 1 else ""
            parent_id = parts[2].strip() if len(parts) > 2 else ""
            level_code = parts[3].strip() if len(parts) > 3 else ""
            child_code = parts[4].strip() if len(parts) > 4 else ""
            cur_hl = RawHLBlock(
                hl_id=hl_id,
                parent_id=parent_id or None,
                level_code=level_code,
                child_code=child_code,
                shared_segments=[raw],
            )
            cur_claim = None
            if cur_txn is not None:
                cur_txn.hl_blocks.append(cur_hl)
        elif sid == "CLM":
            parts = raw.split(elem_sep)
            claim_id = parts[1].strip() if len(parts) > 1 else ""
            cur_claim = RawClaimLoop(claim_id=claim_id, segments=[raw])
            if cur_hl is not None:
                cur_hl.claim_loops.append(cur_claim)
            elif cur_txn is not None:
                # CLM before any HL (non-conforming) -> keep in header to avoid loss
                cur_txn.header_segments.append(raw)
                cur_claim = None
        else:
            if cur_claim is not None:
                cur_claim.segments.append(raw)
            elif cur_hl is not None:
                cur_hl.shared_segments.append(raw)
            elif cur_txn is not None:
                cur_txn.header_segments.append(raw)
            # else: stray segment outside any transaction -> ignore

    return raw_isa, groups, raw_iea


def parse_837p_full(
    content: str | None = None,
    file_path: str | Path | None = None,
) -> Parsed837PFull:
    """
    Parse 837P with full preservation for repackaging.

    Use ``to_edi_string(include_claim_ids=...)`` / ``write_edi(...)`` to rebuild
    the EDI containing only the requested claims (filtered at the CLM-loop level).
    """
    if content is None and file_path is None:
        raise ValueError("Provide content or file_path")
    if content is not None and file_path is not None:
        raise ValueError("Provide either content or file_path, not both")
    if file_path is not None:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")

    base = parse_string(content)
    result = _parse_837p_from_segments(base.segments)
    full = Parsed837PFull(
        submitter=result.submitter,
        receiver=result.receiver,
        bht=result.bht,
        billing_providers=result.billing_providers,
        all_segments=result.all_segments,
    )
    full.delimiters = base.delimiters
    full.complete_segments = [
        Segment(id=s.id, elements=list(s.elements)) for s in base.segments
    ]

    e = base.delimiters.element
    t = base.delimiters.segment_term
    raw_list = base.raw_segments
    full.raw_segment_list = list(raw_list)

    raw_isa, groups, raw_iea = _build_raw_tree(raw_list, e)
    full.raw_isa = raw_isa
    full.raw_iea = raw_iea
    full.groups = groups

    # Backward-compat flat fields (first occurrence / best effort).
    if groups:
        full.raw_gs = groups[0].gs_segment
        full.raw_ge = groups[0].ge_segment
        if groups[0].transactions:
            first_txn = groups[0].transactions[0]
            full.raw_header = t.join(first_txn.header_segments)
            full.raw_se = first_txn.se_segment

    for g in groups:
        for txn in g.transactions:
            for hl in txn.hl_blocks:
                claim_segs: list[str] = []
                for loop in hl.claim_loops:
                    claim_segs.extend(loop.segments)
                raw_content = t.join(hl.shared_segments + claim_segs)
                full.raw_blocks.append(
                    EdiBlock(
                        hl_id=hl.hl_id,
                        parent_id=hl.parent_id,
                        level_code=hl.level_code,
                        raw_content=raw_content,
                        claim_id=hl.claim_ids[0] if hl.claim_ids else None,
                    )
                )

    return full
