"""
Claim-level (CLM loop) filtering regression + edge-case tests for the 837P raw
parser/writer. These operate directly on parse_837p_full / to_edi_string and do
not require pandas.
"""

from __future__ import annotations

import pytest

from conftest import build_isa, clm_ids, hl_segments, make_edi, segments_of

from kw_x12_parser.x837p.utils.raw_block_parser import (
    ClaimFilterError,
    parse_837p_full,
)


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #
def _single_hl_multi_claim(claim_ids: list[str], *, element="*", subelement=":", seg_term="~") -> str:
    """One billing HL + one subscriber HL containing multiple CLM loops."""
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["NM1", "41", "2", "SUBMITTER", "", "", "", "", "46", "SENDER"],
        ["NM1", "40", "2", "RECEIVER", "", "", "", "", "46", "RECEIVER"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
        ["HL", "2", "1", "22", "0"],
        ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
        ["NM1", "IL", "1", "DOE", "JOHN", "", "", "", "MI", "123"],
    ]
    for cid in claim_ids:
        segs += [
            ["CLM", cid, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
            ["HI", "ABK:A000"],
            ["LX", "1"],
            ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
        ]
    segs += [
        ["SE", "99", "0001"],
        ["GE", "1", "1"],
        ["IEA", "1", "000000001"],
    ]
    return make_edi(segs, element=element, subelement=subelement, seg_term=seg_term)


def _multi_subscriber(claim_by_sub: list[str]) -> str:
    """One billing HL, then one subscriber HL per claim id."""
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
    ]
    hl = 2
    for cid in claim_by_sub:
        segs += [
            ["HL", str(hl), "1", "22", "0"],
            ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
            ["NM1", "IL", "1", "SUB", cid, "", "", "", "MI", "X" + cid],
            ["CLM", cid, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
            ["LX", "1"],
            ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
        ]
        hl += 1
    segs += [["SE", "99", "0001"], ["GE", "1", "1"], ["IEA", "1", "000000001"]]
    return make_edi(segs)


def _dependent_hl(claim_id: str) -> str:
    """Billing -> subscriber (has child) -> patient/dependent HL (level 23) with the claim."""
    segs = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
        ["HL", "2", "1", "22", "1"],
        ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
        ["NM1", "IL", "1", "SUBSCRIBER", "JANE", "", "", "", "MI", "SUB1"],
        ["HL", "3", "2", "23", "0"],
        ["PAT", "19"],
        ["NM1", "QC", "1", "PATIENT", "CHILD"],
        ["CLM", claim_id, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
        ["LX", "1"],
        ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
        ["SE", "99", "0001"],
        ["GE", "1", "1"],
        ["IEA", "1", "000000001"],
    ]
    return make_edi(segs)


def _multi_st(txn_claims: list[list[str]]) -> str:
    """Multiple ST*837 sets under one GS; each sublist is the claims for one ST."""
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
    ]
    for st_idx, claims in enumerate(txn_claims, start=1):
        ctrl = str(st_idx).rjust(4, "0")
        segs += [
            ["ST", "837", ctrl, "005010X222A1"],
            ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
            ["HL", "1", "", "20", "1"],
            ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
            ["HL", "2", "1", "22", "0"],
            ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
            ["NM1", "IL", "1", "DOE", "JOHN", "", "", "", "MI", "123"],
        ]
        for cid in claims:
            segs += [
                ["CLM", cid, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
                ["LX", "1"],
                ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
            ]
        segs += [["SE", "99", ctrl]]
    segs += [["GE", str(len(txn_claims)), "1"], ["IEA", "1", "000000001"]]
    return make_edi(segs)


def _se01(text: str) -> int:
    for s in segments_of(text):
        if s.startswith("SE*"):
            return int(s.split("*")[1])
    raise AssertionError("no SE segment")


# --------------------------------------------------------------------------- #
# Scenario 1: requested claim is first CLM in HL -> later unrequested removed
# --------------------------------------------------------------------------- #
def test_first_clm_requested_drops_later():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B"]))
    out = full.to_edi_string(include_claim_ids={"A"})
    assert clm_ids(out) == ["A"]


# Scenario 2 (primary regression): requested claim is NOT the first CLM
def test_non_first_clm_requested():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B", "C"]))
    out = full.to_edi_string(include_claim_ids={"B"})
    assert clm_ids(out) == ["B"]


# Scenario 3: multiple requested claims in one HL, shared content emitted once
def test_multiple_requested_in_one_hl():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B", "C"]))
    out = full.to_edi_string(include_claim_ids={"B", "C"})
    assert clm_ids(out) == ["B", "C"]  # source order preserved
    assert out.count("SBR*P*18") == 1  # shared subscriber content once
    assert out.count("NM1*IL*1*DOE") == 1


# Scenario 4: claims in separate subscriber HLs
def test_separate_subscriber_hls():
    full = parse_837p_full(content=_multi_subscriber(["A", "B", "C"]))
    out = full.to_edi_string(include_claim_ids={"A", "C"})
    assert clm_ids(out) == ["A", "C"]
    hls = hl_segments(out)
    # billing(1) + subA(2) + subC(3); B branch removed
    assert [h[1] for h in hls] == ["1", "2", "3"]
    # subscriber HL02 must point to billing provider's new id "1"
    subs = [h for h in hls if h[3] == "22"]
    assert all(h[2] == "1" for h in subs)
    assert "B" not in clm_ids(out)


# Scenario 5: claim under a patient/dependent HL (level 23, not 22)
def test_claim_under_dependent_hl():
    full = parse_837p_full(content=_dependent_hl("DEP1"))
    assert "DEP1" in full.get_all_claim_ids()
    out = full.to_edi_string(include_claim_ids={"DEP1"})
    assert clm_ids(out) == ["DEP1"]
    hls = hl_segments(out)
    levels = [h[3] for h in hls]
    assert "20" in levels and "22" in levels and "23" in levels
    # subscriber retains child indicator, patient points to subscriber
    patient = [h for h in hls if h[3] == "23"][0]
    subscriber = [h for h in hls if h[3] == "22"][0]
    assert patient[2] == subscriber[1]
    assert subscriber[4] == "1"  # HL04 has-child


# Scenario 6: first claim requested, later claims held (no accidental release)
def test_first_released_later_held():
    full = parse_837p_full(content=_single_hl_multi_claim(["RELEASED", "HELD-1", "HELD-2"]))
    out = full.to_edi_string(include_claim_ids={"RELEASED"})
    assert clm_ids(out) == ["RELEASED"]


# Scenario 7: no requested claim exists -> clear error, no output
def test_no_requested_claim_exists():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B"]))
    with pytest.raises(ClaimFilterError) as ei:
        full.to_edi_string(include_claim_ids={"NOPE"})
    assert "not found in source" in str(ei.value)


# Scenario 8: one requested exists, one does not -> missing-source error
def test_partial_missing_requested():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B"]))
    with pytest.raises(ClaimFilterError) as ei:
        full.to_edi_string(include_claim_ids={"A", "ZZZ"})
    assert "ZZZ" in str(ei.value)


# Scenario 9: multiple ST/SE transactions filtered independently
def test_multi_st_claim_level():
    # txn1: A,B  txn2: C,D
    full = parse_837p_full(content=_multi_st([["A", "B"], ["C", "D"]]))
    # request one from each transaction
    out = full.to_edi_string(include_claim_ids={"B", "C"})
    assert clm_ids(out) == ["B", "C"]
    ge = [s for s in segments_of(out) if s.startswith("GE*")][0]
    assert ge.split("*")[1] == "2"  # two transactions retained
    ses = [s for s in segments_of(out) if s.startswith("SE*")]
    assert len(ses) == 2


def test_multi_st_single_transaction_retained():
    full = parse_837p_full(content=_multi_st([["A", "B"], ["C", "D"]]))
    out = full.to_edi_string(include_claim_ids={"A"})
    assert clm_ids(out) == ["A"]
    ge = [s for s in segments_of(out) if s.startswith("GE*")][0]
    assert ge.split("*")[1] == "1"  # empty transaction removed
    assert len([s for s in segments_of(out) if s.startswith("SE*")]) == 1


# Scenario 10: custom delimiters
def test_custom_delimiters():
    el, sub, term = "|", "^", "\n"
    content = _single_hl_multi_claim(["A", "B", "C"], element=el, subelement=sub, seg_term=term)
    full = parse_837p_full(content=content)
    assert full.delimiters.element == el
    assert full.delimiters.segment_term == term
    out = full.to_edi_string(include_claim_ids={"B"})
    assert clm_ids(out, element=el, seg_term=term) == ["B"]


# Scenario 11: BOM / CRLF / LF / blank lines / leading whitespace
def test_bom_and_newlines():
    base = _single_hl_multi_claim(["A", "B"])  # uses ~ terminators
    # Add BOM + leading whitespace; note segments already terminated by ~
    noisy = "\ufeff  \n" + base
    full = parse_837p_full(content=noisy)
    out = full.to_edi_string(include_claim_ids={"B"})
    assert clm_ids(out) == ["B"]


# Scenario 12: duplicate CLM01 -> all occurrences retained
def test_duplicate_clm_ids_all_included():
    full = parse_837p_full(content=_single_hl_multi_claim(["DUP", "DUP", "OTHER"]))
    assert full.get_all_claim_ids() == ["DUP", "DUP", "OTHER"]
    out = full.to_edi_string(include_claim_ids={"DUP"})
    assert clm_ids(out) == ["DUP", "DUP"]  # both occurrences


# Scenario 13: full release -> every claim retained exactly once, valid counts
def test_full_release():
    ids = ["A", "B", "C", "D"]
    full = parse_837p_full(content=_single_hl_multi_claim(ids))
    out = full.to_edi_string(include_claim_ids=set(ids))
    assert clm_ids(out) == ids
    # SE01 equals number of segments ST..SE inclusive
    seg_list = segments_of(out)
    st_i = next(i for i, s in enumerate(seg_list) if s.startswith("ST*"))
    se_i = next(i for i, s in enumerate(seg_list) if s.startswith("SE*"))
    assert _se01(out) == (se_i - st_i + 1)


# Scenario 14: ISA15 override after filtering
@pytest.mark.parametrize("ind", ["T", "P"])
def test_isa15_override(ind):
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B"]))
    out = full.to_edi_string(include_claim_ids={"A"}, isa15_usage_indicator=ind)
    isa = segments_of(out)[0]
    assert isa.split("*")[15] == ind


# Scenario 15: generated output reparses to the expected claim set
def test_output_reparses():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B", "C"]))
    out = full.to_edi_string(include_claim_ids={"A", "C"})
    reparsed = parse_837p_full(content=out)
    assert reparsed.get_all_claim_ids() == ["A", "C"]


# Scenario 16: regression - claims not first in their HL block
def test_non_first_clm_multi_target_regression():
    # A single subscriber HL with several claims; the two target IDs are NOT first.
    ids = [
        "SYNCLM-0001",
        "SYNCLM-0002",  # target
        "SYNCLM-0003",
        "SYNCLM-0004",  # target
        "SYNCLM-0005",
    ]
    full = parse_837p_full(content=_single_hl_multi_claim(ids))
    targets = {"SYNCLM-0002", "SYNCLM-0004"}
    out = full.to_edi_string(include_claim_ids=targets)
    assert clm_ids(out) == ["SYNCLM-0002", "SYNCLM-0004"]
    # reparse guarantee
    assert set(parse_837p_full(content=out).get_all_claim_ids()) == targets


# exclude + include_fn interactions
def test_exclude_claims():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B", "C"]))
    out = full.to_edi_string(exclude_claim_ids={"B"})
    assert clm_ids(out) == ["A", "C"]


def test_include_fn():
    full = parse_837p_full(content=_single_hl_multi_claim(["KEEP1", "DROP1", "KEEP2"]))
    out = full.to_edi_string(include_claim_ids_fn=lambda c: c.startswith("KEEP"))
    assert clm_ids(out) == ["KEEP1", "KEEP2"]


def test_include_and_exclude_precedence():
    full = parse_837p_full(content=_single_hl_multi_claim(["A", "B", "C"]))
    # include A,B,C but exclude B -> A,C
    out = full.to_edi_string(include_claim_ids={"A", "B", "C"}, exclude_claim_ids={"B"})
    assert clm_ids(out) == ["A", "C"]
