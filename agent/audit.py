"""Deterministic equity audit; only this tool can reach demographic counts."""

import asyncio
from itertools import combinations
from math import isfinite

from google.adk.tools.tool_context import ToolContext

from . import config
from .contracts import AuditResult, ClinicPlan, GroupShare, PlanRequest, TractEvidence
from .scoring import haversine_km, score_tracts

GROUPS = {
    "White (non-Hispanic)": "white_pop",
    "Black (non-Hispanic)": "black_pop",
    "Asian (non-Hispanic)": "asian_pop",
    "American Indian / Alaska Native (non-Hispanic)": "native_pop",
    "Pacific Islander (non-Hispanic)": "pacific_pop",
    "Other race (non-Hispanic)": "other_pop",
    "Multiple races (non-Hispanic)": "multiracial_pop",
    "Hispanic / Latino": "hispanic_pop",
}


def evaluate_plan(
    plan: ClinicPlan, tracts: list[TractEvidence], demographics: list[dict]
) -> AuditResult:
    must_fix, disclosures, excluded = [], [], []
    by_id = {t.geo_id: t for t in tracts}
    covered = {g for s in plan.stops for g in s.catchment_geo_ids}
    counts = {r["geo_id"]: r for r in demographics}
    ranked = {t.geo_id: s for t, s in score_tracts(tracts)}
    minimum_score = min((ranked.get(s.anchor_geo_id, 1) for s in plan.stops), default=1)
    matched = {
        g
        for g, score in ranked.items()
        if score >= minimum_score - config.NEED_MATCH_MARGIN
    }
    if len(plan.stops) != 4:
        must_fix.append(
            "Provide four distinct Saturday anchors; the current draft has fewer than four."
        )
    distances = []
    for a, b in combinations(plan.stops, 2):
        ta, tb = by_id.get(a.anchor_geo_id), by_id.get(b.anchor_geo_id)
        if (
            ta is None
            or tb is None
            or any(
                v is None
                for v in (ta.latitude, ta.longitude, tb.latitude, tb.longitude)
            )
        ):
            must_fix.append(
                "An anchor has no verified centroid in the county evidence."
            )
            continue
        distance = haversine_km(ta, tb)
        distances.append(distance)
        if distance < config.AUDIT_SPACING_KM:
            must_fix.append(
                f"Separate anchors {a.anchor_geo_id} and {b.anchor_geo_id} by at least {config.AUDIT_SPACING_KM:g} km."
            )
            excluded.append(b.anchor_geo_id)
    groups = []
    valid = bool(covered and matched and counts) and len(counts) == len(demographics)
    valid = valid and covered.issubset(counts) and set(by_id).issubset(counts)
    for row in demographics:
        pop = row.get("total_pop")
        if not isinstance(pop, (int, float)) or not isfinite(pop) or pop < 0:
            valid = False
            break
        for column in GROUPS.values():
            count = row.get(column)
            if (
                not isinstance(count, (int, float))
                or not isfinite(count)
                or not 0 <= count <= pop
            ):
                valid = False
    valid = valid and sum(r.get("total_pop", 0) or 0 for r in demographics) > 0
    valid = valid and all(g in by_id for g in covered)
    if not valid:
        must_fix.append(
            "Audit coverage is incomplete or demographic counts are missing; a human must verify the data."
        )
    else:

        def share(ids, column):
            return round(
                100
                * sum(counts[g][column] for g in ids)
                / sum(counts[g]["total_pop"] for g in ids),
                2,
            )

        for group, column in GROUPS.items():
            result = GroupShare(
                group=group,
                plan_pct=share(covered, column),
                county_pct=share(counts, column),
                need_matched_pct=share(matched, column),
            )
            groups.append(result)
            county_gap = result.county_pct - result.plan_pct
            need_gap = result.need_matched_pct - result.plan_pct
            if county_gap > config.COUNTY_SHORTFALL_PP:
                if need_gap > config.NEED_SHORTFALL_PP:
                    # No demographic name or count is forwarded to the planner.
                    must_fix.append(
                        "Coverage falls short of the need-matched comparison; choose a different defensible set of anchors."
                    )
                    weakest = sorted(
                        plan.stops,
                        key=lambda stop: (
                            share(stop.catchment_geo_ids, column),
                            stop.score,
                            stop.anchor_geo_id,
                        ),
                    )
                    excluded.extend(stop.anchor_geo_id for stop in weakest[:2])
                else:
                    disclosures.append(
                        f"{group}: county shortfall {county_gap:.2f} percentage points is explained by the need-matched comparison; disclose, do not repair."
                    )
        if any(row.get("limited_english_households") is None for row in demographics):
            disclosures.append(
                "Limited-English household counts are incomplete; they cannot choose a flyer language."
            )
    return AuditResult(
        verdict="REVISE" if must_fix else "APPROVE",
        must_fix=list(dict.fromkeys(must_fix)),
        disclosures=disclosures,
        groups=groups,
        min_stop_separation_km=round(min(distances), 3) if distances else None,
        excluded_anchors=sorted(set(excluded)),
        limitations=[
            "TRACT, modeled: ACS five-year estimates have sampling uncertainty; shares are population-weighted, not attendance forecasts.",
            "Categories are mutually exclusive: non-Hispanic race groups and Hispanic / Latino residents of any race, validated against ACS B03002.",
            f"Need-matched tracts score at least the lowest selected anchor minus {config.NEED_MATCH_MARGIN:g}; demo thresholds are {config.COUNTY_SHORTFALL_PP:g} county and {config.NEED_SHORTFALL_PP:g} need-matched percentage points.",
            "FACILITY-POINT, upper bound: centroid spacing is not road travel or service coverage.",
            "Limited-English households do not identify which second language to use.",
        ],
    )


async def audit_plan_tool(tool_context: ToolContext) -> dict:
    """Audit the saved plan once using the auditor identity; report its verdict verbatim."""
    state = tool_context.state
    if state.get("audit_called"):
        return {"status": "error", "error_message": "Audit already called this round"}
    state["audit_called"] = True
    state["audit_result"] = None
    try:
        from .data_access import auditor_rows

        plan = ClinicPlan.model_validate(state["plan_data"])
        request = PlanRequest.model_validate(state["request"])
        tracts, demographics = await asyncio.to_thread(
            auditor_rows, request.county_fips
        )
        result = evaluate_plan(plan, tracts, demographics)
        state["audit_result"] = result.model_dump(mode="json")
        return {"status": "success", **state["audit_result"]}
    except Exception as exc:
        state["audit_error"] = f"AUDIT NOT RUN: {type(exc).__name__}: {exc}"
        return {"status": "error", "error_message": state["audit_error"]}
