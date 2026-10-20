"""JSON-serializable contracts for every trusted pipeline hop."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scale = Literal["TRACT", "12 KM GRID", "FACILITY-POINT", "MULTI-COUNTY REGION"]
GeoId = Annotated[str, Field(pattern=r"^\d{11}$")]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Distance = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PlanRequest(Contract):
    county_fips: str = Field(pattern=r"^\d{5}$")
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    saturdays: list[date] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.saturdays != sorted(set(self.saturdays)) or any(
            d.weekday() != 5 or d.strftime("%Y-%m") != self.month
            for d in self.saturdays
        ):
            raise ValueError(
                "Provide four unique, ordered Saturdays in the requested month"
            )
        return self


class Claim(Contract):
    text: str
    source_table: str
    scale: Scale
    kind: Literal["measured", "modeled", "upper_bound"]


class TractEvidence(Contract):
    geo_id: GeoId
    total_pop: int = Field(ge=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    pct_uninsured: Percent | None = None
    pct_diabetes: Percent | None = None
    pct_asthma: Percent | None = None
    pct_copd: Percent | None = None
    pct_high_bp: Percent | None = None
    pct_checkup: Percent | None = None
    pct_no_vehicle: Percent | None = None
    pct_mobility_difficulty: Percent | None = None
    pct_lack_transport: Percent | None = None
    svi_socioeconomic: float | None = Field(default=None, ge=0, le=1)
    km_to_any_care: Distance | None = None
    km_to_safety_net: Distance | None = None
    extra_km_if_uninsured: Distance | None = None
    mean_pm: Distance | None = None
    days_over_12: int | None = Field(default=None, ge=0, le=366)


class EvidenceBrief(Contract):
    county_fips: str = Field(pattern=r"^\d{5}$")
    candidates: list[TractEvidence]
    context: list[Claim]

    @model_validator(mode="after")
    def county_and_unique(self):
        ids = [t.geo_id for t in self.candidates]
        if len(ids) != len(set(ids)) or any(
            not g.startswith(self.county_fips) for g in ids
        ):
            raise ValueError(
                "Evidence must contain unique tracts in the requested county"
            )
        return self


class Stop(Contract):
    saturday: int = Field(ge=1, le=4)
    date: date
    anchor_geo_id: GeoId
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    catchment_geo_ids: list[GeoId] = Field(min_length=1)
    catchment_population: int = Field(ge=0)
    score: float = Field(ge=0, le=1)
    tied_geo_ids: list[GeoId] = Field(default_factory=list)
    reasons: list[Claim]

    @model_validator(mode="after")
    def anchor_in_catchment(self):
        if self.anchor_geo_id not in self.catchment_geo_ids:
            raise ValueError("Catchment must include its anchor")
        return self


class ClinicPlan(Contract):
    # Partial drafts must be representable so the gate can hand them to a human.
    revision_count: int = Field(ge=0, le=2)
    min_separation_km: Distance
    exclude_tracts: list[GeoId]
    stops: list[Stop] = Field(max_length=4)
    cannot_tell_you: list[Claim]
    tie_margin: float = 0.02

    @model_validator(mode="after")
    def unique_coverage(self):
        anchors = [s.anchor_geo_id for s in self.stops]
        covered = [g for s in self.stops for g in s.catchment_geo_ids]
        if len(anchors) != len(set(anchors)) or len(covered) != len(set(covered)):
            raise ValueError("Anchors and catchments must be unique")
        if [s.saturday for s in self.stops] != list(range(1, len(self.stops) + 1)):
            raise ValueError("Stops must be in Saturday order")
        return self


class GroupShare(Contract):
    group: str
    plan_pct: Percent
    county_pct: Percent
    need_matched_pct: Percent


class AuditResult(Contract):
    verdict: Literal["APPROVE", "REVISE"]
    must_fix: list[str]
    disclosures: list[str]
    groups: list[GroupShare]
    min_stop_separation_km: Distance | None
    excluded_anchors: list[GeoId] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source: str = "a4i_audit.tract_demographics (ACS 5-year estimates)"

    @model_validator(mode="after")
    def consistent_verdict(self):
        if (self.verdict == "APPROVE") == bool(self.must_fix):
            raise ValueError("Verdict must agree with MUST FIX items")
        return self
