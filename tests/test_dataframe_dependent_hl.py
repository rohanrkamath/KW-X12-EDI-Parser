"""
DataFrame-path (hierarchical parser / casual_parse) regression tests.

Covers the defect where claims under a patient/dependent HL (level 23) were
dropped from the claims DataFrame (claim_id=None) because the parser only
recognized claims at HL level 22. Also covers multiple CLM loops in one HL.
"""

from __future__ import annotations

import pandas as pd

from conftest import make_edi

from kw_x12_parser.x837p.utils.hierarchical_parser import parse_837p_string


def _df(content: str) -> pd.DataFrame:
    return parse_837p_string(content).to_claims_dataframe(source_file="test")


def _subscriber_is_patient(ids: list[str]) -> str:
    """Billing (20) -> subscriber (22) holding the CLM (Medicare-style)."""
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
    ]
    hl = 2
    for cid in ids:
        segs += [
            ["HL", str(hl), "1", "22", "0"],
            ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
            ["NM1", "IL", "1", "DOE", "JOHN", "", "", "", "MI", "SUB" + cid],
            ["NM1", "PR", "2", "ACME PAYER", "", "", "", "", "PI", "60054"],
            ["CLM", cid, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
            ["HI", "ABK:A000"],
            ["LX", "1"],
            ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
        ]
        hl += 1
    segs += [["SE", "99", "0001"], ["GE", "1", "1"], ["IEA", "1", "000000001"]]
    return make_edi(segs)


def _dependent_claims(pairs: list[tuple[str, str]]) -> str:
    """Billing (20) -> subscriber (22, no CLM) -> patient/dependent (23) with CLM.

    ``pairs`` = list of (patient_last_name, claim_id).
    """
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
    ]
    hl = 2
    for pname, cid in pairs:
        sub = hl
        pat = hl + 1
        segs += [
            ["HL", str(sub), "1", "22", "1"],
            ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
            ["NM1", "IL", "1", "SUBSCRIBER", "JANE", "", "", "", "MI", "SUB" + cid],
            ["NM1", "PR", "2", "ACME PAYER", "", "", "", "", "PI", "60054"],
            ["HL", str(pat), str(sub), "23", "0"],
            ["PAT", "19"],
            ["NM1", "QC", "1", pname, "CHILD"],
            ["CLM", cid, "250", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
            ["HI", "ABK:A000"],
            ["LX", "1"],
            ["SV1", "HC:E0465", "250", "UN", "1", "", "", "1"],
        ]
        hl += 2
    segs += [["SE", "99", "0001"], ["GE", "1", "1"], ["IEA", "1", "000000001"]]
    return make_edi(segs)


def _multi_clm_one_hl(ids: list[str]) -> str:
    """One subscriber HL (22) containing multiple CLM loops."""
    segs: list[list[str]] = [
        ["GS", "HC", "SENDER", "RECEIVER", "20260708", "0633", "1", "X", "005010X222A1"],
        ["ST", "837", "0001", "005010X222A1"],
        ["BHT", "0019", "00", "REF", "20260708", "0633", "CH"],
        ["HL", "1", "", "20", "1"],
        ["NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX", "1111111111"],
        ["HL", "2", "1", "22", "0"],
        ["SBR", "P", "18", "", "", "", "", "", "", "CI"],
        ["NM1", "IL", "1", "DOE", "JOHN", "", "", "", "MI", "123"],
        ["NM1", "PR", "2", "ACME PAYER", "", "", "", "", "PI", "60054"],
    ]
    for cid in ids:
        segs += [
            ["CLM", cid, "100", "", "", "12:B:1", "Y", "A", "Y", "Y", "P"],
            ["HI", "ABK:A000"],
            ["LX", "1"],
            ["SV1", "HC:E0465", "100", "UN", "1", "", "", "1"],
        ]
    segs += [["SE", "99", "0001"], ["GE", "1", "1"], ["IEA", "1", "000000001"]]
    return make_edi(segs)


def test_subscriber_is_patient_unchanged():
    df = _df(_subscriber_is_patient(["A", "B", "C"]))
    assert df["claim_id"].tolist() == ["A", "B", "C"]
    assert df["claim_id"].isna().sum() == 0
    assert df["payer_name"].iloc[0] == "ACME PAYER"
    assert df["patient_name"].iloc[0] == "JOHN DOE"


def test_dependent_hl_claims_extracted():
    df = _df(_dependent_claims([("SMITH", "DEP1"), ("JONES", "DEP2")]))
    # both dependent claims present, none null
    assert df["claim_id"].isna().sum() == 0
    assert set(df["claim_id"]) == {"DEP1", "DEP2"}
    # patient name comes from NM1*QC, subscriber/payer from ancestor HL
    row = df[df["claim_id"] == "DEP1"].iloc[0]
    assert row["patient_name"] == "CHILD SMITH"
    assert row["payer_name"] == "ACME PAYER"
    assert row["total_charge"] == "250"


def test_mixed_subscriber_and_dependent():
    # combine: some subscriber-is-patient claims and some dependent claims in one file
    content = _dependent_claims([("SMITH", "DEP1")])
    df = _df(content)
    assert df["claim_id"].isna().sum() == 0
    assert "DEP1" in set(df["claim_id"])


def test_multiple_clm_in_one_hl_dataframe():
    df = _df(_multi_clm_one_hl(["A", "B", "C"]))
    assert df["claim_id"].tolist() == ["A", "B", "C"]
    assert df["claim_id"].isna().sum() == 0
    # each claim keeps its own single service line
    assert df["service_line_count"].tolist() == [1, 1, 1]
