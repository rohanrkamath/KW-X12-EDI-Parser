"""
API-level tests for write_to_edi_x837p() original-EDI claim filtering.
Requires pandas.
"""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import clm_ids, make_edi

from kw_x12_parser import write_to_edi_x837p
from kw_x12_parser.x837p.utils.raw_block_parser import ClaimFilterError


def _edi_with_claims(ids: list[str]) -> str:
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
        ["HL", "2", "1", "22", "0"],
        ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
        ["NM1", "IL", "1", "DOE", "JOHN", "", "", "", "MI", "123"],
    ]
    for cid in ids:
        segs += [
            ["CLM", cid, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
            ["LX", "1"],
            ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
        ]
    segs += [["SE", "99", "0001"], ["GE", "1", "1"], ["IEA", "1", "000000001"]]
    return make_edi(segs)


def test_api_filters_non_first_claims(tmp_path):
    original = _edi_with_claims(["A", "B", "C"])
    df = pd.DataFrame({"claim_id": ["B", "C"]})
    out = tmp_path / "out.edi"
    write_to_edi_x837p(df, out, original_edi=original)
    assert clm_ids(out.read_text()) == ["B", "C"]


def test_api_non_first_claim_regression(tmp_path):
    ids = [
        "SYNCLM-0001",
        "SYNCLM-0002",
        "SYNCLM-0003",
        "SYNCLM-0004",
        "SYNCLM-0005",
    ]
    original = _edi_with_claims(ids)
    df = pd.DataFrame({"claim_id": ["SYNCLM-0002", "SYNCLM-0004"]})
    out = tmp_path / "non_first.edi"
    write_to_edi_x837p(df, out, original_edi=original, isa15_usage_indicator="P")
    result = out.read_text()
    assert clm_ids(result) == ["SYNCLM-0002", "SYNCLM-0004"]
    assert result.split("~")[0].split("*")[15] == "P"  # ISA15


def test_api_missing_claim_raises_and_writes_nothing(tmp_path):
    original = _edi_with_claims(["A", "B"])
    df = pd.DataFrame({"claim_id": ["A", "DOES-NOT-EXIST"]})
    out = tmp_path / "missing.edi"
    with pytest.raises(ClaimFilterError) as ei:
        write_to_edi_x837p(df, out, original_edi=original)
    assert "DOES-NOT-EXIST" in str(ei.value)
    assert not out.exists()  # no partial output


def test_api_numeric_claim_id_coercion(tmp_path):
    # DataFrame may coerce numeric IDs to float "123.0"; api normalizes to "123".
    original = _edi_with_claims(["123", "456"])
    df = pd.DataFrame({"claim_id": [123.0]})
    out = tmp_path / "num.edi"
    write_to_edi_x837p(df, out, original_edi=original)
    assert clm_ids(out.read_text()) == ["123"]


def test_api_full_release(tmp_path):
    ids = ["A", "B", "C"]
    original = _edi_with_claims(ids)
    df = pd.DataFrame({"claim_id": ids})
    out = tmp_path / "full.edi"
    write_to_edi_x837p(df, out, original_edi=original)
    assert clm_ids(out.read_text()) == ids
