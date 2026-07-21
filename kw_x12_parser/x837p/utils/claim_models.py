"""
Data models for 837P (Professional) hierarchical structure.
Based on ASC X12N TR3 005010X222A1 and CMS 837P Companion Guide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas  # type: ignore[import-untyped]


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


@dataclass
class HLNode:
    """Hierarchical Level - represents a node in the 837P hierarchy."""

    hl_id: str           # HL01 - Hierarchical ID
    parent_id: str | None  # HL02 - Parent Hierarchical ID (blank for root)
    level_code: str      # HL03 - 20=Info Source, 22=Subscriber/Patient
    child_code: str      # HL04 - 0=no children, 1=has children
    segments: list[Segment] = field(default_factory=list)


@dataclass
class LoopSegmentGroup:
    """A group of segments belonging to a loop, keyed by segment ID."""

    segments: list[Segment] = field(default_factory=list)

    def get(self, segment_id: str) -> list[Segment]:
        """Return all segments with the given ID."""
        return [s for s in self.segments if s.id == segment_id]

    def get_one(self, segment_id: str) -> Segment | None:
        """Return the first segment with the given ID."""
        for s in self.segments:
            if s.id == segment_id:
                return s
        return None


@dataclass
class ServiceLine:
    """Loop 2400 - Service line (LX, SV1, DTP, REF, NM1, SVD, CAS, etc.)."""

    segments: list[Segment] = field(default_factory=list)

    @property
    def line_number(self) -> str | None:
        lx = self.get_one("LX")
        return lx.get(1) if lx else None

    @property
    def procedure_code(self) -> str | None:
        sv1 = self.get_one("SV1")
        if not sv1:
            return None
        # SV1-01 = HC:qualifier:code:modifier:modifier:modifier:modifier:description
        comp = (sv1.get(1) or "").split(":")
        return comp[1] if len(comp) > 1 else comp[0] if comp else None

    @property
    def charge_amount(self) -> str | None:
        sv1 = self.get_one("SV1")
        return sv1.get(2) if sv1 else None

    @property
    def service_date(self) -> str | None:
        dtp = self.get_one("DTP")
        return dtp.get(3) if dtp and dtp.get(1) == "472" else None

    def get(self, segment_id: str) -> list[Segment]:
        return [s for s in self.segments if s.id == segment_id]

    def get_one(self, segment_id: str) -> Segment | None:
        for s in self.segments:
            if s.id == segment_id:
                return s
        return None


@dataclass
class SubscriberClaim:
    """
    Loop 2000B + 2300 - Subscriber/Patient and Claim Information.
    When subscriber = patient (Medicare), one HL level 22 contains both.
    """

    hl_node: HLNode
    segments: list[Segment] = field(default_factory=list)
    service_lines: list[ServiceLine] = field(default_factory=list)

    @property
    def claim_id(self) -> str | None:
        clm = self.get_one("CLM")
        return clm.get(1) if clm else None

    @property
    def total_charge(self) -> str | None:
        clm = self.get_one("CLM")
        return clm.get(2) if clm else None

    @property
    def diagnosis_codes(self) -> list[str]:
        hi = self.get_one("HI")
        if not hi:
            return []
        codes = []
        for i in range(1, 13):  # HI01-HI12
            val = hi.get(i)
            if val:
                # Format ABK:code or ABF:code
                parts = str(val).split(":")
                if len(parts) >= 2:
                    codes.append(parts[1])
                else:
                    codes.append(val)
        return codes

    @property
    def subscriber_name(self) -> str | None:
        for s in self.segments:
            if s.id == "NM1" and s.get(1) == "IL":  # Insured (NM101)
                nm1 = s
                break
        else:
            return None
        last = nm1.get(3) or ""   # NM103
        first = nm1.get(4) or ""  # NM104
        return f"{first} {last}".strip() or None

    @property
    def patient_name(self) -> str | None:
        # Dependent claims carry the patient as NM1*QC (Loop 2010CA); subscriber-is-
        # patient claims (Medicare, etc.) only have NM1*IL. Prefer QC when present.
        for s in self.segments:
            if s.id == "NM1" and s.get(1) == "QC":  # Patient (NM101)
                last = s.get(3) or ""   # NM103
                first = s.get(4) or ""  # NM104
                name = f"{first} {last}".strip()
                if name:
                    return name
        return self.subscriber_name

    @property
    def payer_name(self) -> str | None:
        for s in self.segments:
            if s.id == "NM1" and s.get(1) == "PR":  # Payer (NM101)
                return s.get(3) or s.get(4)  # NM103 org name, NM104
        return None

    def get(self, segment_id: str) -> list[Segment]:
        return [s for s in self.segments if s.id == segment_id]

    def get_one(self, segment_id: str) -> Segment | None:
        for s in self.segments:
            if s.id == segment_id:
                return s
        return None

    @property
    def all_segments(self) -> list[Segment]:
        """Every segment in this claim in order (claim-level + all service lines)."""
        out: list[Segment] = list(self.segments)
        for sl in self.service_lines:
            out.extend(sl.segments)
        return out


@dataclass
class BillingProvider:
    """Loop 2000A + 2010AA - Billing Provider (HL level 20)."""

    hl_node: HLNode
    segments: list[Segment] = field(default_factory=list)
    claims: list[SubscriberClaim] = field(default_factory=list)

    @property
    def name(self) -> str | None:
        for s in self.segments:
            if s.id == "NM1" and s.get(1) == "85":  # Billing Provider (NM101)
                return s.get(3) or s.get(4)  # NM103 org name
        return None

    @property
    def npi(self) -> str | None:
        for s in self.segments:
            if s.id == "NM1" and s.get(1) == "85":
                return s.get(9)  # NM109
        return None

    @property
    def address(self) -> str | None:
        n3 = self.get_one("N3")
        return n3.get(1) if n3 else None

    @property
    def city_state_zip(self) -> str | None:
        n4 = self.get_one("N4")
        if not n4:
            return None
        parts = [n4.get(2), n4.get(3), n4.get(4)]
        return ", ".join(p for p in parts if p)

    def get(self, segment_id: str) -> list[Segment]:
        return [s for s in self.segments if s.id == segment_id]

    def get_one(self, segment_id: str) -> Segment | None:
        for s in self.segments:
            if s.id == segment_id:
                return s
        return None


@dataclass
class Parsed837P:
    """Result of parsing an 837P transaction with hierarchical structure."""

    # Header (before first HL)
    submitter: LoopSegmentGroup = field(default_factory=LoopSegmentGroup)
    receiver: LoopSegmentGroup = field(default_factory=LoopSegmentGroup)
    bht: Segment | None = None

    # Billing providers (HL level 20), each containing claims (HL level 22)
    billing_providers: list[BillingProvider] = field(default_factory=list)

    # Raw segments for backwards compatibility
    all_segments: list[Segment] = field(default_factory=list)

    # Envelope segments (common for all claims in file)
    isa_segment: Segment | None = None
    gs_segment: Segment | None = None
    se_segment: Segment | None = None
    ge_segment: Segment | None = None
    iea_segment: Segment | None = None

    @property
    def claim_count(self) -> int:
        return sum(len(bp.claims) for bp in self.billing_providers)

    @property
    def all_claims(self) -> list[SubscriberClaim]:
        result: list[SubscriberClaim] = []
        for bp in self.billing_providers:
            result.extend(bp.claims)
        return result

    def to_claims_dataframe_full(
        self, source_file: str | None = None
    ) -> "pandas.DataFrame":
        """
        Return a pandas DataFrame with one row per claim and semantic X12-style columns.
        Columns include: BHT_Date, CLM_*, DMG_*, DTP_qual_Date, NM1_ENTITY_*, N3_*, N4_*,
        SV1_1_HCPCS_CODE, SV1_1_LINE_CHARGE, etc. (similar to colleague's schema).
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "pandas is required for to_claims_dataframe_full(). "
                "Install with: pip install edi-parser[dataframe] or pip install pandas"
            ) from e

        from .full_column_mapper import _build_full_claim_row

        st_seg = next((s for s in self.all_segments if s.id == "ST"), None)
        rows = []
        for bp in self.billing_providers:
            for claim in bp.claims:
                row = _build_full_claim_row(
                    bp=bp,
                    claim=claim,
                    submitter_segments=self.submitter.segments,
                    receiver_segments=self.receiver.segments,
                    bht=self.bht,
                    st_seg=st_seg,
                    source_file=source_file,
                    isa_seg=self.isa_segment,
                    gs_seg=self.gs_segment,
                    se_seg=self.se_segment,
                    ge_seg=self.ge_segment,
                    iea_seg=self.iea_segment,
                )
                rows.append(row)

        df = pd.DataFrame(rows)
        # Reorder columns: known first, then alphabetically for the rest
        if not df.empty:
            known = [
                "claim_id", "provider_name", "provider_npi",
                "ST_Transaction_Set_Identifier_Code", "ST_Transaction_Set_Control_Number",
                "BHT_Date", "BHT_Time", "BHT_Reference_Identification",
                "CLM_Claim_Submitters_Identifier", "CLM_Monetary_Amount",
                "NM1_BILLING_PROVIDER_Name_Last_or_Organization_Name",
                "NM1_BILLING_PROVIDER_Identification_Code",
                "N3_BILLING_PROVIDER_Address_Line1",
                "N4_BILLING_PROVIDER_City", "N4_BILLING_PROVIDER_State", "N4_BILLING_PROVIDER_Zip",
                "NM1_SUBSCRIBER_Name_Last_or_Organization_Name", "NM1_SUBSCRIBER_Name_First",
                "NM1_PAYER_Name_Last_or_Organization_Name",
                "DMG_Date_Time_Period", "DMG_Gender_Code",
                "SV1_LINE_COUNT", "SV1_TOTAL_CHARGE",
            ]
            others = [c for c in df.columns if c not in known]
            df = df[[c for c in known if c in df.columns] + sorted(others)]
        return df

    def to_claims_dataframe(
        self, source_file: str | None = None
    ) -> "pandas.DataFrame":
        """
        Return a pandas DataFrame with one row per claim.
        Use spark.createDataFrame(df) in Palantir Foundry transforms.
        Requires: pip install edi-parser[dataframe] or pip install pandas
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "pandas is required for to_claims_dataframe(). "
                "Install with: pip install edi-parser[dataframe] or pip install pandas"
            ) from e

        rows = []
        for bp in self.billing_providers:
            for claim in bp.claims:
                row = {
                    "provider_name": bp.name,
                    "provider_npi": bp.npi,
                    "provider_address": bp.address,
                    "provider_city_state_zip": bp.city_state_zip,
                    "claim_id": claim.claim_id,
                    "total_charge": claim.total_charge,
                    "patient_name": claim.patient_name,
                    "payer_name": claim.payer_name,
                    "diagnosis_codes": "|".join(claim.diagnosis_codes)
                    if claim.diagnosis_codes
                    else None,
                    "service_line_count": len(claim.service_lines),
                }
                if source_file is not None:
                    row["source_file"] = source_file
                rows.append(row)
        return pd.DataFrame(rows)

    def to_service_lines_dataframe(
        self, source_file: str | None = None
    ) -> "pandas.DataFrame":
        """
        Return a pandas DataFrame with one row per service line (denormalized).
        Use spark.createDataFrame(df) in Palantir Foundry transforms.
        Requires: pip install edi-parser[dataframe] or pip install pandas
        """
        try:
            import pandas as pd  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "pandas is required for to_service_lines_dataframe(). "
                "Install with: pip install edi-parser[dataframe] or pip install pandas"
            ) from e

        rows = []
        for bp in self.billing_providers:
            for claim in bp.claims:
                for sl in claim.service_lines:
                    row = {
                        "provider_name": bp.name,
                        "provider_npi": bp.npi,
                        "claim_id": claim.claim_id,
                        "total_charge": claim.total_charge,
                        "patient_name": claim.patient_name,
                        "payer_name": claim.payer_name,
                        "line_number": sl.line_number,
                        "procedure_code": sl.procedure_code,
                        "line_charge_amount": sl.charge_amount,
                        "service_date": sl.service_date,
                    }
                    if source_file is not None:
                        row["source_file"] = source_file
                    rows.append(row)
        return pd.DataFrame(rows)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for serialization."""
        return {
            "billing_providers": [
                {
                    "hl_id": bp.hl_node.hl_id,
                    "name": bp.name,
                    "npi": bp.npi,
                    "address": bp.address,
                    "city_state_zip": bp.city_state_zip,
                    "claim_count": len(bp.claims),
                    "claims": [
                        {
                            "hl_id": c.hl_node.hl_id,
                            "claim_id": c.claim_id,
                            "total_charge": c.total_charge,
                            "patient_name": c.patient_name,
                            "payer_name": c.payer_name,
                            "diagnosis_codes": c.diagnosis_codes,
                            "service_line_count": len(c.service_lines),
                            "service_lines": [
                                {
                                    "line_number": sl.line_number,
                                    "procedure_code": sl.procedure_code,
                                    "charge_amount": sl.charge_amount,
                                    "service_date": sl.service_date,
                                }
                                for sl in c.service_lines
                            ],
                        }
                        for c in bp.claims
                    ],
                }
                for bp in self.billing_providers
            ],
            "claim_count": self.claim_count,
        }
