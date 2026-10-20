"""Deterministic contracts, scoring, audit, and handout guardrails."""

from datetime import date

import pytest
from pydantic import ValidationError

from agent.audit import evaluate_plan
from agent.contracts import EvidenceBrief, PlanRequest, TractEvidence
from agent.flyer import NOT_APPROVED, check_flyer
from agent.gate import decide_plan
from agent.scoring import make_plan, score_tracts


def request(county="13121"):
    return PlanRequest(
        county_fips=county,
        month="2026-11",
        saturdays=[date(2026, 11, d) for d in (7, 14, 21, 28)],
    )


def candidates(count=8, county="13121", step=0.06):
    return [
        TractEvidence(
            geo_id=f"{county}{i:06}",
            total_pop=1000,
            latitude=33.7 + i * step,
            longitude=-84.4,
            pct_uninsured=40 - i,
            pct_no_vehicle=30 - i,
            km_to_safety_net=10 - i * 0.3,
            pct_diabetes=20 - i,
            pct_asthma=15 - i,
            pct_copd=10 - i * 0.2,
            pct_high_bp=40 - i,
            pct_checkup=60 + i,
            pct_mobility_difficulty=10 - i * 0.1,
            svi_socioeconomic=0.9 - i * 0.1,
        )
        for i in range(count)
    ]


def brief(rows=None):
    return EvidenceBrief(
        county_fips="13121", candidates=rows or candidates(), context=[]
    )


def demographics(rows, black=50):
    return [
        {
            "geo_id": t.geo_id,
            "total_pop": t.total_pop,
            "white_pop": t.total_pop - black,
            "black_pop": black,
            "asian_pop": 0,
            "native_pop": 0,
            "pacific_pop": 0,
            "other_pop": 0,
            "multiracial_pop": 0,
            "hispanic_pop": 0,
            "limited_english_households": 10,
            "total_households": 400,
        }
        for t in rows
    ]


def test_request_requires_four_unique_saturdays_in_month():
    assert request().county_fips == "13121"
    with pytest.raises(ValidationError):
        request().model_copy()  # model_copy intentionally does not validate; validate below
        PlanRequest(
            county_fips="13121", month="2026-11", saturdays=[date(2026, 11, 1)] * 4
        )
    with pytest.raises(ValidationError):
        TractEvidence(**{**candidates()[0].model_dump(), "race": "not allowed"})


def test_score_ignores_regional_and_grid_values_and_orders_ties_stably():
    rows = candidates()
    before = [(t.geo_id, s) for t, s in score_tracts(rows)]
    changed = [
        t.model_copy(update={"mean_pm": 10000, "days_over_12": 365}) for t in rows
    ]
    assert [(t.geo_id, s) for t, s in score_tracts(changed)] == before
    assert score_tracts(list(reversed(rows))) == score_tracts(rows)


def test_plan_spacing_catchments_and_population_are_deterministic():
    plan = make_plan(request(), brief(), [], 0, [], 0)
    assert len(plan.stops) == 4
    covered = [g for stop in plan.stops for g in stop.catchment_geo_ids]
    assert len(covered) == len(set(covered))
    assert all(
        s.catchment_population == len(s.catchment_geo_ids) * 1000 for s in plan.stops
    )
    assert all(
        c.scale in {"TRACT", "FACILITY-POINT"} for s in plan.stops for c in s.reasons
    )
    dense = brief(candidates(step=0.001))
    assert len(make_plan(request(), dense, [], 5, [], 1).stops) == 1


def test_plan_preserves_selected_anchor_coordinates():
    rows = candidates()
    plan = make_plan(request(), brief(rows), [], 5, [rows[0].geo_id], 1)
    by_id = {t.geo_id: t for t in rows}
    assert len(plan.stops) == 4
    for stop in plan.stops:
        saved = stop.model_dump(mode="json")
        assert saved["latitude"] == by_id[stop.anchor_geo_id].latitude
        assert saved["longitude"] == by_id[stop.anchor_geo_id].longitude


def test_audit_population_weighted_and_need_matched():
    rows = candidates()
    plan = make_plan(request(), brief(rows), [], 0, [], 0)
    audit = evaluate_plan(plan, rows, demographics(rows))
    assert audit.verdict == "APPROVE"
    assert audit.groups[1].plan_pct == audit.groups[1].county_pct == 5
    assert audit.groups[1].need_matched_pct == 5
    bad = demographics(rows)
    bad.pop()
    assert evaluate_plan(plan, rows, bad).verdict == "REVISE"


def test_gate_never_trusts_auditor_prose_and_stops_at_cap():
    plan = make_plan(request(), brief(), [], 0, [], 0).model_dump(mode="json")
    state = {
        "plan_data": plan,
        "revision_count": 0,
        "audit_report": "APPROVE",
        "audit_result": {
            "verdict": "REVISE",
            "must_fix": ["spacing"],
            "disclosures": [],
            "groups": [],
            "min_stop_separation_km": 1,
        },
    }
    assert decide_plan(state) is False
    assert state["plan_status"] != "APPROVED"
    state["revision_count"] = 2
    assert decide_plan(state) is True
    assert state["plan_status"] == "NEEDS_HUMAN"
    assert state["plan_history"]


GOOD_FLYER = """## FLYER (ENGLISH)
Come meet the clinic team on [DATE] at [TIME]. Find us at [LOCATION]. Bring your questions. Ask clinic staff about care. Call [CONTACT] to learn more.
## FLYER (SECOND LANGUAGE)
Conozca al equipo de la clínica el [DATE] a las [TIME] en [LOCATION]. Llame a [CONTACT].
## TEXT MESSAGE
Meet the clinic team: [DATE], [TIME], [LOCATION]. Ask us at [CONTACT].
## COMMUNITY HEALTH WORKER SCRIPT
Hi! The clinic team will be here on [DATE].
Come by at [TIME].
You can find us at [LOCATION].
Here is a flyer for you.
Please ask clinic staff about care.
Call [CONTACT] for more.
"""


def test_flyer_checks_and_fail_closed_sentence():
    assert check_flyer(GOOD_FLYER) == []
    assert check_flyer(NOT_APPROVED) == []


@pytest.mark.parametrize(
    "bad",
    [
        "90%",
        "at risk",
        "cure",
        "[ADDRESS]",
        "123 Main Street",
        "9 am",
        "404-555-1234",
        "free care",
        "no insurance needed",
        "sliding scale",
        "half of residents",
    ],
)
def test_flyer_rejects_invented_and_banned_claims(bad):
    assert check_flyer(GOOD_FLYER.replace("Bring your questions.", bad + "."))


def test_confirmation_is_local_to_claim():
    assert check_flyer(
        GOOD_FLYER.replace(
            "Bring your questions.", "Free care. Call [CONTACT] [CONFIRM]."
        )
    )
    assert (
        check_flyer(GOOD_FLYER.replace("Bring your questions.", "Free care [CONFIRM]."))
        == []
    )


def test_zero_population_and_invalid_demographic_counts_fail_safely():
    rows = candidates()
    plan = make_plan(request(), brief(rows), [], 0, [], 0)
    demo = demographics(rows)
    zero = {**demo[-1], "geo_id": "13121999999", "total_pop": 0}
    for field in (
        "white_pop",
        "black_pop",
        "limited_english_households",
        "total_households",
    ):
        zero[field] = 0
    assert evaluate_plan(plan, rows, [*demo, zero]).verdict == "APPROVE"
    demo[0]["black_pop"] = float("nan")
    assert evaluate_plan(plan, rows, demo).verdict == "REVISE"


def test_equity_shortfall_requires_both_comparisons():
    rows = candidates()
    plan = make_plan(request(), brief(rows), [], 0, [], 0)
    demo = demographics(rows, black=50)
    for row in demo[4:]:
        row["black_pop"] = 950
        row["white_pop"] = 50
    audit = evaluate_plan(plan, rows, demo)
    assert audit.verdict == "APPROVE"
    assert audit.disclosures
    # Equalize need, so the same county gap is now also a need-matched gap.
    same_need = [
        t.model_copy(
            update={
                k: getattr(rows[0], k)
                for k in TractEvidence.model_fields
                if k not in {"geo_id", "latitude", "longitude"}
            }
        )
        for t in rows
    ]
    plan = make_plan(request(), brief(same_need), [], 0, [], 0)
    assert evaluate_plan(plan, same_need, demo).verdict == "REVISE"


def test_equity_repair_provides_actionable_anchor_exclusions():
    rows = candidates()
    same_need = [
        t.model_copy(
            update={
                k: getattr(rows[0], k)
                for k in TractEvidence.model_fields
                if k not in {"geo_id", "latitude", "longitude"}
            }
        )
        for t in rows
    ]
    demo = demographics(same_need)
    for row in demo[4:]:
        row["black_pop"], row["white_pop"] = 950, 50
    plan = make_plan(request(), brief(same_need), [], 0, [], 0)
    result = evaluate_plan(plan, same_need, demo)
    assert result.verdict == "REVISE"
    assert result.excluded_anchors
    revised = make_plan(request(), brief(same_need), [], 5, result.excluded_anchors, 1)
    assert evaluate_plan(revised, same_need, demo).verdict == "APPROVE"
