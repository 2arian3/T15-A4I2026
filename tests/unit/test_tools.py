"""Unit tests for agent/tools.py."""

import re

import pytest

from agent.schemas import SaturdaySite, SitingPlan
from agent.tools import (
    AUDIT_PLAN_SQL_TEMPLATE,
    SITING_RANKING_SQL_TEMPLATE,
    TABLE_RESOLUTIONS,
    compare_plan_demographics,
    describe_table_resolution,
    haversine_km,
    select_four_saturday_tracts,
)

EXPECTED_TABLES = {
    "burden_tracts",
    "exposure_tracts",
    "disease_weekly",
    "care_sites",
    "care_sites_state",
    "shortage_areas",
    "shortage_facilities",
    "tract_access",
}


def test_covers_all_eight_challenge_tables() -> None:
    assert set(TABLE_RESOLUTIONS) == EXPECTED_TABLES


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_each_entry_is_complete(table: str) -> None:
    info = TABLE_RESOLUTIONS[table]
    assert info["table"] == table
    assert info["role"] in {"people", "care", "join"}
    assert info["grain"]
    assert info["caveat"]
    assert isinstance(info["can_answer_at_tract_level"], bool)


@pytest.mark.parametrize(
    ("table", "tract_level"),
    [
        ("burden_tracts", True),
        ("tract_access", True),
        # The traps: these look tract- or county-level and are not.
        ("exposure_tracts", False),
        ("disease_weekly", False),
    ],
)
def test_tract_level_flags(table: str, tract_level: bool) -> None:
    result = describe_table_resolution(table)
    assert result["status"] == "success"
    assert result["can_answer_at_tract_level"] is tract_level


@pytest.mark.parametrize(
    "name",
    [
        "disease_weekly",
        "  Disease_Weekly ",
        "a4i_health.disease_weekly",
        "p.a4i_health.disease_weekly",
    ],
)
def test_name_normalisation(name: str) -> None:
    result = describe_table_resolution(name)
    assert result["status"] == "success"
    assert result["table"] == "disease_weekly"


@pytest.mark.parametrize("name", ["nope", "", None])
def test_unknown_table_returns_error_with_known_tables(name) -> None:
    result = describe_table_resolution(name)
    assert result["status"] == "error"
    assert result["known_tables"] == sorted(EXPECTED_TABLES)


def test_result_does_not_alias_module_state() -> None:
    result = describe_table_resolution("burden_tracts")
    result["grain"] = "changed"
    assert TABLE_RESOLUTIONS["burden_tracts"]["grain"] == "census tract"


def test_siting_sql_does_not_use_race_or_ethnicity() -> None:
    sql_lower = SITING_RANKING_SQL_TEMPLATE.lower()
    assert "svi_racial_ethnic" not in sql_lower
    assert "race" not in sql_lower
    assert "nullif(b.svi_socioeconomic, -999)" in sql_lower


def test_static_need_score_does_not_weight_pm25() -> None:
    sql_lower = SITING_RANKING_SQL_TEMPLATE.lower()
    assert "env_pm_factor" not in sql_lower
    static_block = sql_lower.split("static_scored as (", 1)[1].split(
        "latest_disease_surge", 1
    )[0]
    score_sql = re.sub(r"--[^\n]*", "", static_block)
    assert "mean_pm" not in score_sql
    assert "0.17 * rank_no_vehicle" in score_sql


def test_audit_sql_reads_the_column_planner_cannot() -> None:
    assert "svi_racial_ethnic" in AUDIT_PLAN_SQL_TEMPLATE
    assert "svi_racial_ethnic" not in SITING_RANKING_SQL_TEMPLATE.lower()


def test_compare_plan_demographics_weights_by_population() -> None:
    rows = [
        {"geo_id": "1", "total_pop": 100, "svi_racial_ethnic": 0.2},
        {"geo_id": "2", "total_pop": 300, "svi_racial_ethnic": 0.8},
        {"geo_id": "3", "total_pop": 100, "svi_racial_ethnic": 0.4},
    ]
    result = compare_plan_demographics(rows, ["1", "3"])
    assert result["status"] == "reviewed"
    assert result["column"] == "svi_racial_ethnic"
    assert result["selected_mean"] == 0.3
    assert result["county_mean"] == 0.6
    assert result["difference"] == -0.3
    assert result["error_message"] is None


def test_compare_plan_demographics_errors_without_selected_values() -> None:
    rows = [
        {"geo_id": "1", "total_pop": 100, "svi_racial_ethnic": None},
        {"geo_id": "2", "total_pop": 100, "svi_racial_ethnic": 0.5},
    ]
    result = compare_plan_demographics(rows, ["1"])
    assert result["status"] == "error"
    assert result["difference"] is None
    assert result["county_mean"] == 0.5


def test_select_four_saturday_tracts_enforces_spatial_separation() -> None:
    # Two adjacent tracts (~0.8 km apart) plus 3 spread-out tracts
    fake_candidates = [
        {
            "geo_id": "13121007604",
            "total_pop": 3191,
            "latitude": 33.6815,
            "longitude": -84.4962,
            "pct_uninsured": 19.8,
            "pct_diabetes": 18.8,
            "pct_asthma": 12.1,
            "pct_high_bp": 44.3,
            "pct_checkup": 77.7,
            "pct_no_vehicle": 33.3,
            "pct_mobility_difficulty": 22.8,
            "svi_socioeconomic": 0.962,
            "km_to_any_care": 3.06,
            "km_to_safety_net": 4.6,
            "extra_km_if_uninsured": 1.54,
            "nearest_safety_net_name": "Clinic A",
            "mean_pm": 9.31,
            "surge_week": "2026-01-31",
            "static_need_score": 82.34,
            "dynamic_surge_multiplier": 1.151,
            "final_need_score": 94.79,
            "need_rank": 1,
        },
        {
            # Very close (< 1.5 km) to 13121007604 -> should be skipped for spatial diversity
            "geo_id": "13121007603",
            "total_pop": 3979,
            "latitude": 33.6880,
            "longitude": -84.4929,
            "pct_uninsured": 17.7,
            "pct_diabetes": 15.8,
            "pct_asthma": 11.7,
            "pct_high_bp": 37.8,
            "pct_checkup": 75.5,
            "pct_no_vehicle": 49.4,
            "pct_mobility_difficulty": 18.7,
            "svi_socioeconomic": 0.942,
            "km_to_any_care": 0.69,
            "km_to_safety_net": 4.03,
            "extra_km_if_uninsured": 3.34,
            "nearest_safety_net_name": "Clinic A",
            "mean_pm": 9.33,
            "surge_week": "2026-01-31",
            "static_need_score": 79.78,
            "dynamic_surge_multiplier": 1.146,
            "final_need_score": 91.45,
            "need_rank": 2,
        },
        {
            "geo_id": "13121007500",
            "total_pop": 3447,
            "latitude": 33.6634,
            "longitude": -84.3849,
            "pct_uninsured": 19.6,
            "pct_diabetes": 18.8,
            "pct_asthma": 11.9,
            "pct_high_bp": 42.3,
            "pct_checkup": 77.6,
            "pct_no_vehicle": 42.7,
            "pct_mobility_difficulty": 21.8,
            "svi_socioeconomic": 0.960,
            "km_to_any_care": 1.48,
            "km_to_safety_net": 3.74,
            "extra_km_if_uninsured": 2.26,
            "nearest_safety_net_name": "Clinic B",
            "mean_pm": 9.67,
            "surge_week": "2026-01-31",
            "static_need_score": 81.89,
            "dynamic_surge_multiplier": 1.149,
            "final_need_score": 94.07,
            "need_rank": 3,
        },
        {
            "geo_id": "13121011421",
            "total_pop": 3933,
            "latitude": 33.9912,
            "longitude": -84.3450,
            "pct_uninsured": 22.7,
            "pct_diabetes": 10.6,
            "pct_asthma": 10.2,
            "pct_high_bp": 29.0,
            "pct_checkup": 72.7,
            "pct_no_vehicle": 12.1,
            "pct_mobility_difficulty": 12.8,
            "svi_socioeconomic": 0.875,
            "km_to_any_care": 2.68,
            "km_to_safety_net": 14.61,
            "extra_km_if_uninsured": 11.93,
            "nearest_safety_net_name": "Clinic C",
            "mean_pm": 9.48,
            "surge_week": "2026-01-31",
            "static_need_score": 74.10,
            "dynamic_surge_multiplier": 1.128,
            "final_need_score": 83.55,
            "need_rank": 4,
        },
        {
            "geo_id": "13121010538",
            "total_pop": 5642,
            "latitude": 33.5515,
            "longitude": -84.5810,
            "pct_uninsured": 16.5,
            "pct_diabetes": 15.8,
            "pct_asthma": 11.0,
            "pct_high_bp": 41.3,
            "pct_checkup": 78.6,
            "pct_no_vehicle": 5.3,
            "pct_mobility_difficulty": 14.6,
            "svi_socioeconomic": 0.850,
            "km_to_any_care": 5.90,
            "km_to_safety_net": 7.62,
            "extra_km_if_uninsured": 1.72,
            "nearest_safety_net_name": "Clinic D",
            "mean_pm": 9.15,
            "surge_week": "2026-01-31",
            "static_need_score": 74.66,
            "dynamic_surge_multiplier": 1.138,
            "final_need_score": 84.93,
            "need_rank": 5,
        },
    ]

    selected, skipped = select_four_saturday_tracts(
        fake_candidates, min_separation_km=3.0
    )
    assert len(selected) == 4
    selected_ids = [s["geo_id"] for s in selected]
    assert "13121007604" in selected_ids
    assert "13121007603" not in selected_ids
    assert len(skipped) == 1
    assert skipped[0]["geo_id"] == "13121007603"
    assert selected[0]["why_preferred_over_alternatives"]
    assert selected[0]["selection_reason"]
    site = SaturdaySite.model_validate(selected[0])
    assert site.supporting_metrics.pm25_warning.startswith(
        "12 km EPA grid context only"
    )
    assert haversine_km(33.6815, -84.4962, 33.6880, -84.4929) < 3.0


def _enriched_saturday_site() -> dict:
    return {
        "saturday_number": 1,
        "geo_id": "13121007604",
        "total_pop": 3191,
        "latitude": 33.6815,
        "longitude": -84.4962,
        "need_rank": 1,
        "scores": {
            "static_need_score": 82.34,
            "dynamic_surge_multiplier": 1.151,
            "final_need_score": 94.79,
        },
        "supporting_metrics": {
            "pct_uninsured": 19.8,
            "pct_diabetes": 18.8,
            "pct_asthma": 12.1,
            "pct_high_bp": 44.3,
            "pct_checkup": 77.7,
            "pct_no_vehicle": 33.3,
            "pct_mobility_difficulty": 22.8,
            "svi_socioeconomic": 0.962,
            "km_to_any_care": 3.06,
            "km_to_safety_net": 4.6,
            "extra_km_if_uninsured": 1.54,
            "nearest_safety_net_name": "Clinic A",
            "mean_pm": 9.31,
            "surge_week": "2026-01-31",
        },
        "selection_reason": "Saturday #1 — Census Tract 13121007604",
        "why_preferred_over_alternatives": "Selected as a distinct geographic anchor.",
    }


def test_enriched_tract_validates_as_saturday_site() -> None:
    site = SaturdaySite.model_validate(_enriched_saturday_site())
    assert site.saturday_number == 1
    assert site.latitude == 33.6815
    assert site.longitude == -84.4962
    assert site.selection_reason.startswith("Saturday #1")


def test_success_plan_keeps_map_fields() -> None:
    payload = SitingPlan.model_validate(
        {
            "status": "success",
            "project": "demo-project",
            "dataset": "a4i_health",
            "methodology": {
                "weights": {
                    "healthcare_needs": 0.35,
                    "accessibility_barriers": 0.35,
                    "transportation_and_mobility_barriers": 0.30,
                    "environmental_pm25_context": 0.0,
                },
                "race_or_ethnicity_used_in_scoring": False,
                "min_separation_km": 3.0,
            },
            "selected_tracts": [_enriched_saturday_site()],
            "limitations": [
                {
                    "table": "burden_tracts",
                    "grain": "census tract",
                    "limitation": "CDC PLACES values are modeled prevalence.",
                }
            ],
        }
    ).model_dump()

    tract = payload["selected_tracts"][0]
    weights = payload["methodology"]["weights"]
    assert payload["status"] == "success"
    assert tract["latitude"] == 33.6815
    assert tract["longitude"] == -84.4962
    assert tract["saturday_number"] == 1
    assert tract["selection_reason"].startswith("Saturday #1")
    assert weights["environmental_pm25_context"] == 0.0
    assert weights["transportation_and_mobility_barriers"] == 0.30
    assert sum(weights.values()) == pytest.approx(1.0)
    assert tract["supporting_metrics"]["pm25_warning"].startswith(
        "12 km EPA grid context only"
    )


def test_site_metrics_allow_null_svi_and_pm() -> None:
    site = _enriched_saturday_site()
    site["supporting_metrics"]["svi_socioeconomic"] = None
    site["supporting_metrics"]["mean_pm"] = None
    validated = SaturdaySite.model_validate(site)
    assert validated.supporting_metrics.svi_socioeconomic is None
    assert validated.supporting_metrics.mean_pm is None


def test_error_plan_has_empty_tracts() -> None:
    payload = SitingPlan.model_validate(
        {
            "status": "error",
            "error_message": "BigQuery siting query failed: boom",
        }
    ).model_dump()

    assert payload["status"] == "error"
    assert payload["error_message"] == "BigQuery siting query failed: boom"
    assert payload["selected_tracts"] == []
