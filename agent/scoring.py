"""Tract-only need ranking and disjoint centroid catchments.

Adapted from feature/siting-plan-tool: environmental weights and regional
multipliers are removed. Scores are relative priorities, never health outcomes.
"""

from bisect import bisect_left
from math import atan2, cos, radians, sin, sqrt

from . import config
from .contracts import (
    Claim,
    ClinicPlan,
    EvidenceBrief,
    PlanRequest,
    Stop,
    TractEvidence,
)

# Original tract weights, normalized to sum to one after removing the grid factor.
WEIGHTS = {
    "pct_uninsured": 0.15,
    "chronic": 0.12,
    "preventive_gap": 0.08,
    "km_to_safety_net": 0.20,
    "extra_km_if_uninsured": 0.15,
    "pct_no_vehicle": 0.12,
    "pct_mobility_difficulty": 0.08,
    "svi_socioeconomic": 0.05,
}


def haversine_km(a: TractEvidence, b: TractEvidence) -> float:
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    dlat = lat2 - lat1
    dlon = radians(b.longitude - a.longitude)
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * atan2(sqrt(min(1, value)), sqrt(max(0, 1 - value)))


def eligible(t: TractEvidence) -> bool:
    return t.total_pop >= config.MIN_POPULATION and all(
        v is not None
        for v in (
            t.latitude,
            t.longitude,
            t.pct_uninsured,
            t.pct_no_vehicle,
            t.km_to_safety_net,
        )
    )


def score_tracts(rows: list[TractEvidence]) -> list[tuple[TractEvidence, float]]:
    """Percentile rank known values; renormalize weights for missing factors."""
    features = {}
    for t in rows:
        if not eligible(t):
            continue
        values = {name: getattr(t, name, None) for name in WEIGHTS}
        chronic = [t.pct_diabetes, t.pct_asthma, t.pct_copd, t.pct_high_bp]
        values["chronic"] = (
            sum(chronic) if all(v is not None for v in chronic) else None
        )
        values["preventive_gap"] = (
            100 - t.pct_checkup if t.pct_checkup is not None else None
        )
        features[t.geo_id] = values
    distributions = {
        name: sorted(v[name] for v in features.values() if v[name] is not None)
        for name in WEIGHTS
    }
    ranked = []
    for t in rows:
        if t.geo_id not in features:
            continue
        numerator = denominator = 0
        for name, weight in WEIGHTS.items():
            value = features[t.geo_id][name]
            known = distributions[name]
            if value is None or len(known) < 2 or known[0] == known[-1]:
                continue
            numerator += weight * bisect_left(known, value) / (len(known) - 1)
            denominator += weight
        ranked.append((t, round(numerator / denominator if denominator else 0, 6)))
    return sorted(ranked, key=lambda pair: (-pair[1], pair[0].geo_id))


def make_plan(
    request: PlanRequest,
    evidence: EvidenceBrief,
    limits: list[Claim],
    min_separation_km: float,
    exclude_tracts: list[str],
    revision_count: int,
) -> ClinicPlan:
    ranked = score_tracts(evidence.candidates)
    chosen = []
    for tract, score in ranked:
        if tract.geo_id in exclude_tracts:
            continue
        if all(haversine_km(tract, other) >= min_separation_km for other, _ in chosen):
            chosen.append((tract, score))
        if len(chosen) == 4:
            break
    catchments = {t.geo_id: [t] for t, _ in chosen}
    anchors = set(catchments)
    for t in evidence.candidates:
        if (
            t.geo_id in anchors
            or t.latitude is None
            or t.longitude is None
            or not chosen
        ):
            continue
        distance, anchor = min((haversine_km(t, a), a.geo_id) for a, _ in chosen)
        if distance <= config.CATCHMENT_KM:
            catchments[anchor].append(t)
    stops = []
    for i, (t, score) in enumerate(chosen):
        reasons = [
            Claim(
                text=f"Uninsured adults aged 18-64: {t.pct_uninsured:g}%.",
                source_table="burden_tracts",
                scale="TRACT",
                kind="modeled",
            ),
            Claim(
                text=f"Households without a vehicle: {t.pct_no_vehicle:g}%.",
                source_table="burden_tracts",
                scale="TRACT",
                kind="measured",
            ),
            Claim(
                text=f"Straight-line distance to a federal safety-net site: {t.km_to_safety_net:g} km; unlisted clinics may be nearer.",
                source_table="tract_access",
                scale="FACILITY-POINT",
                kind="upper_bound",
            ),
        ]
        stops.append(
            Stop(
                saturday=i + 1,
                date=request.saturdays[i],
                anchor_geo_id=t.geo_id,
                latitude=t.latitude,
                longitude=t.longitude,
                catchment_geo_ids=sorted(r.geo_id for r in catchments[t.geo_id]),
                catchment_population=sum(r.total_pop for r in catchments[t.geo_id]),
                score=score,
                tied_geo_ids=[
                    other.geo_id
                    for other, s in ranked
                    if other.geo_id != t.geo_id and abs(s - score) < config.TIE_MARGIN
                ],
                reasons=reasons,
            )
        )
    return ClinicPlan(
        revision_count=revision_count,
        min_separation_km=min_separation_km,
        exclude_tracts=exclude_tracts,
        stops=stops,
        cannot_tell_you=limits,
        tie_margin=config.TIE_MARGIN,
    )
