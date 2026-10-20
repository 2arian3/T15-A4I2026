"""Function tools for the Curbside agent.

These are deterministic Python tools the agent can call. They carry the
project's knowledge about the data rather than querying it, so they are cheap,
safe and unit-testable without Google Cloud credentials.
"""

from __future__ import annotations

import math
import os
from typing import Any, TypedDict

from google.adk.tools.tool_context import ToolContext

from .schemas import PM25_WARNING, SitingPlan


class TableResolution(TypedDict):
    table: str
    role: str
    grain: str
    can_answer_at_tract_level: bool
    caveat: str


# What each table in the `a4i_health` dataset can honestly support.
# Source: the challenge README ("What you'll have" and "Nine defects").
TABLE_RESOLUTIONS: dict[str, TableResolution] = {
    "burden_tracts": {
        "table": "burden_tracts",
        "role": "people",
        "grain": "census tract",
        "can_answer_at_tract_level": True,
        "caveat": (
            "CDC PLACES values are modeled prevalence, not counts. Say they are "
            "modeled. pct_checkup is 'good when high'; the others are 'bad when high'."
        ),
    },
    "exposure_tracts": {
        "table": "exposure_tracts",
        "role": "people",
        "grain": "12 km EPA modeling grid written onto tract IDs",
        "can_answer_at_tract_level": False,
        "caveat": (
            "Neighboring tracts share one grid cell, so tract-to-tract differences "
            "are mostly artifacts. Join LEFT from burden_tracts, not INNER."
        ),
    },
    "disease_weekly": {
        "table": "disease_weekly",
        "role": "people",
        "grain": "multi-county Health Service Area written onto a county key",
        "can_answer_at_tract_level": False,
        "caveat": (
            "Cannot say anything about a neighborhood or even a single county. "
            "Some counties report 'Data Unavailable' for every week."
        ),
    },
    "care_sites": {
        "table": "care_sites",
        "role": "care",
        "grain": "facility point",
        "can_answer_at_tract_level": False,
        "caveat": (
            "Federal sites only (CMS-certified and HRSA-funded). Free and county "
            "clinics are missing, so distances are upper bounds."
        ),
    },
    "care_sites_state": {
        "table": "care_sites_state",
        "role": "care",
        "grain": "facility point (whole state)",
        "can_answer_at_tract_level": False,
        "caveat": "Use it when the nearest clinic is just over the county line.",
    },
    "shortage_areas": {
        "table": "shortage_areas",
        "role": "care",
        "grain": "HRSA shortage-area polygon",
        "can_answer_at_tract_level": False,
        "caveat": (
            "Says a neighborhood is underserved (a siting signal). Legitimately "
            "empty in several counties. Never UNION with shortage_facilities."
        ),
    },
    "shortage_facilities": {
        "table": "shortage_facilities",
        "role": "care",
        "grain": "facility designation",
        "can_answer_at_tract_level": False,
        "caveat": (
            "Marks where care already is, the opposite meaning of shortage_areas."
        ),
    },
    "tract_access": {
        "table": "tract_access",
        "role": "join",
        "grain": "census tract",
        "can_answer_at_tract_level": True,
        "caveat": (
            "Straight-line distances to the nearest care site and nearest "
            "safety-net site. Not travel time."
        ),
    },
}


def describe_table_resolution(table_name: str) -> dict:
    """Explains what geographic resolution a challenge table really supports.

    Call this before making any claim about a specific neighborhood, so the
    answer never states something more precisely than the data allows.

    Args:
        table_name: Name of a table in the a4i_health dataset, for example
            "burden_tracts" or "disease_weekly". Case and surrounding
            whitespace are ignored, and a "dataset." prefix is allowed.

    Returns:
        A dict with status "success" and the table's role, grain, whether it
        can answer at tract level and its caveat; or status "error" with the
        list of known tables.
    """
    key = (table_name or "").strip().lower().rsplit(".", 1)[-1]
    info = TABLE_RESOLUTIONS.get(key)
    if info is None:
        return {
            "status": "error",
            "error_message": f"Unknown table '{table_name}'.",
            "known_tables": sorted(TABLE_RESOLUTIONS),
        }
    return {"status": "success", **info}


SITING_RANKING_SQL_TEMPLATE = """\
WITH combined_tracts AS (
    SELECT
        b.geo_id,
        b.total_pop,
        b.latitude,
        b.longitude,
        -- 1. Healthcare Needs (CDC PLACES modeled prevalence & uninsured)
        b.pct_uninsured,
        b.pct_diabetes,
        b.pct_asthma,
        b.pct_copd,
        b.pct_high_bp,
        b.pct_checkup,
        -- 2. Transportation & Socioeconomic Barriers (SVI -999 sentinel guarded)
        b.pct_no_vehicle,
        b.pct_mobility_difficulty,
        NULLIF(b.svi_socioeconomic, -999) AS svi_socioeconomic,
        NULLIF(b.svi_housing_transport, -999) AS svi_housing_transport,
        -- 3. Healthcare Accessibility (straight-line km to federal care sites)
        a.km_to_any_care,
        a.km_to_safety_net,
        a.extra_km_if_uninsured,
        -- 4. Environmental context only (12 km EPA grid; not used in ranking)
        e.mean_pm,
        e.days_over_12
    FROM `{project}.{dataset}.burden_tracts` AS b
    LEFT JOIN `{project}.{dataset}.tract_access` AS a
        ON b.geo_id = a.geo_id
    LEFT JOIN `{project}.{dataset}.exposure_tracts` AS e
        ON b.geo_id = e.geo_id
    WHERE b.total_pop >= 500
),
percentile_ranks AS (
    SELECT
        *,
        -- Dimension 1: Healthcare Need (uninsured + chronic disease + low checkups)
        PERCENT_RANK() OVER (ORDER BY COALESCE(pct_uninsured, 0) ASC) AS rank_uninsured,
        PERCENT_RANK() OVER (
            ORDER BY COALESCE(pct_diabetes + pct_asthma + COALESCE(pct_copd, 0) + pct_high_bp, 0) ASC
        ) AS rank_chronic_disease,
        PERCENT_RANK() OVER (ORDER BY COALESCE(100.0 - pct_checkup, 0) ASC) AS rank_preventive_gap,
        -- Dimension 2: Healthcare Accessibility Barrier (distance to safety net + uninsured penalty)
        PERCENT_RANK() OVER (ORDER BY COALESCE(km_to_safety_net, 0) ASC) AS rank_safety_net_dist,
        PERCENT_RANK() OVER (ORDER BY COALESCE(extra_km_if_uninsured, 0) ASC) AS rank_uninsured_gap_km,
        -- Dimension 3: Transportation & Socioeconomic Vulnerability
        PERCENT_RANK() OVER (ORDER BY COALESCE(pct_no_vehicle, 0) ASC) AS rank_no_vehicle,
        PERCENT_RANK() OVER (ORDER BY COALESCE(pct_mobility_difficulty, 0) ASC) AS rank_mobility_diff,
        PERCENT_RANK() OVER (ORDER BY COALESCE(svi_socioeconomic, 0) ASC) AS rank_socioeconomic
    FROM combined_tracts
),
static_scored AS (
    SELECT
        *,
        ROUND((
            -- 35% Healthcare Needs
            0.15 * rank_uninsured +
            0.12 * rank_chronic_disease +
            0.08 * rank_preventive_gap +
            -- 35% Healthcare Accessibility Barriers
            0.20 * rank_safety_net_dist +
            0.15 * rank_uninsured_gap_km +
            -- 30% Transportation & Mobility Barriers
            -- The former 5% PM2.5 weight moved here. mean_pm stays in the
            -- SELECT for context and is not part of static_need_score.
            0.17 * rank_no_vehicle +
            0.08 * rank_mobility_diff +
            0.05 * rank_socioeconomic
        ) * 100.0, 2) AS static_need_score
    FROM percentile_ranks
),
latest_disease_surge AS (
    SELECT
        week_end,
        county,
        COALESCE(percent_visits_covid, 0) + COALESCE(percent_visits_influenza, 0) + COALESCE(percent_visits_rsv, 0) AS total_respiratory_pct,
        ed_trends_covid,
        ed_trends_influenza,
        ed_trends_rsv,
        CASE
            WHEN ed_trends_covid = 'Increasing' OR ed_trends_influenza = 'Increasing' OR ed_trends_rsv = 'Increasing' THEN 1.15
            ELSE 1.00
        END AS trend_multiplier
    FROM `{project}.{dataset}.disease_weekly`
    WHERE week_end = (SELECT MAX(week_end) FROM `{project}.{dataset}.disease_weekly`)
    LIMIT 1
)
SELECT
    s.geo_id,
    s.total_pop,
    ROUND(s.latitude, 5) AS latitude,
    ROUND(s.longitude, 5) AS longitude,
    ROUND(s.pct_uninsured, 1) AS pct_uninsured,
    ROUND(s.pct_diabetes, 1) AS pct_diabetes,
    ROUND(s.pct_asthma, 1) AS pct_asthma,
    ROUND(s.pct_copd, 1) AS pct_copd,
    ROUND(s.pct_high_bp, 1) AS pct_high_bp,
    ROUND(s.pct_checkup, 1) AS pct_checkup,
    ROUND(s.pct_no_vehicle, 1) AS pct_no_vehicle,
    ROUND(s.pct_mobility_difficulty, 1) AS pct_mobility_difficulty,
    ROUND(s.svi_socioeconomic, 3) AS svi_socioeconomic,
    ROUND(s.km_to_any_care, 2) AS km_to_any_care,
    ROUND(s.km_to_safety_net, 2) AS km_to_safety_net,
    ROUND(s.extra_km_if_uninsured, 2) AS extra_km_if_uninsured,
    ROUND(s.mean_pm, 2) AS mean_pm,
    CAST(d.week_end AS STRING) AS surge_week,
    ROUND(1.0 + (COALESCE(d.trend_multiplier, 1.0) - 1.0) * (s.pct_asthma / 12.0), 3) AS dynamic_surge_multiplier,
    s.static_need_score,
    ROUND(
        s.static_need_score * (1.0 + (COALESCE(d.trend_multiplier, 1.0) - 1.0) * (s.pct_asthma / 12.0)),
        2
    ) AS final_need_score,
    DENSE_RANK() OVER (
        ORDER BY s.static_need_score * (1.0 + (COALESCE(d.trend_multiplier, 1.0) - 1.0) * (s.pct_asthma / 12.0)) DESC
    ) AS need_rank
FROM static_scored AS s
LEFT JOIN latest_disease_surge AS d
    ON TRUE
ORDER BY final_need_score DESC
LIMIT {top_n}
"""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance in kilometers between two lat/lon points."""
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * radius_km * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def select_four_saturday_tracts(
    candidates: list[dict[str, Any]],
    min_separation_km: float = 3.0,
    num_saturdays: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Selects 4 geographically spread census tracts from ranked candidates.

    Uses greedy selection over candidates ordered by `final_need_score` DESC,
    enforcing at least `min_separation_km` between chosen Saturday sites so the
    mobile clinic van serves distinct neighborhoods rather than parking on
    adjacent blocks every week.

    Returns:
        (selected_tracts, skipped_Spatial_duplicates)
    """
    selected: list[dict[str, Any]] = []
    skipped_nearby: list[dict[str, Any]] = []

    for row in candidates:
        if len(selected) >= num_saturdays:
            break
        lat = float(row.get("latitude") or 0.0)
        lon = float(row.get("longitude") or 0.0)
        too_close_to: dict[str, Any] | None = None
        min_dist = float("inf")
        for chosen in selected:
            dist = haversine_km(
                lat,
                lon,
                float(chosen.get("latitude") or 0.0),
                float(chosen.get("longitude") or 0.0),
            )
            if dist < min_separation_km and dist < min_dist:
                min_dist = dist
                too_close_to = chosen

        if too_close_to is not None:
            skipped_copy = dict(row)
            skipped_copy["skipped_due_to_tract"] = too_close_to["geo_id"]
            skipped_copy["distance_to_selected_km"] = round(min_dist, 2)
            skipped_nearby.append(skipped_copy)
            continue

        selected.append(dict(row))

    # Fallback if fewer than num_saturdays met the separation threshold
    if len(selected) < num_saturdays:
        chosen_ids = {s["geo_id"] for s in selected}
        for row in candidates:
            if len(selected) >= num_saturdays:
                break
            if row["geo_id"] not in chosen_ids:
                selected.append(dict(row))
                chosen_ids.add(row["geo_id"])

    # Enrich each of the 4 selected tracts with Saturday assignment and explanations
    enriched_selected: list[dict[str, Any]] = []
    for idx, tract in enumerate(selected, start=1):
        geo_id = tract["geo_id"]
        nearby_alts = [
            s for s in skipped_nearby if s.get("skipped_due_to_tract") == geo_id
        ]
        if nearby_alts:
            alt_strs = [
                f"tract {a['geo_id']} (rank #{a['need_rank']}, score {a['final_need_score']}, "
                f"located {a['distance_to_selected_km']} km away)"
                for a in nearby_alts[:2]
            ]
            why_preferred = (
                f"Preferred over adjacent high-need {', '.join(alt_strs)} because "
                f"tract {geo_id} achieved a higher composite need score ({tract['final_need_score']}) "
                f"within the same {min_separation_km:.1f} km geographic catchment, preserving "
                f"subsequent Saturdays for other underserved corridors."
            )
        else:
            lower_candidates = [
                c
                for c in candidates
                if int(c.get("need_rank", 999)) > int(tract.get("need_rank", 0))
            ][:2]
            alt_ids = ", ".join(
                f"{c['geo_id']} (score {c['final_need_score']}, km_to_safety_net={c['km_to_safety_net']} km)"
                for c in lower_candidates
            )
            why_preferred = (
                f"Selected as a distinct geographic anchor (>= {min_separation_km:.1f} km from prior "
                f"Saturday stops) and outranked lower-scoring alternatives ({alt_ids}) due to severe "
                f"combined safety-net distance ({tract['km_to_safety_net']} km; +{tract['extra_km_if_uninsured']} km "
                f"penalty if uninsured) and transportation barriers ({tract['pct_no_vehicle']}% no vehicle)."
            )

        selection_reason = (
            f"Saturday #{idx} — Census Tract {geo_id} (Pop: {tract['total_pop']}, Rank #{tract['need_rank']}, "
            f"Final Score: {tract['final_need_score']}/100): High modeled chronic burden "
            f"(diabetes {tract['pct_diabetes']}%, asthma {tract['pct_asthma']}%, high BP {tract['pct_high_bp']}%, "
            f"routine checkup {tract['pct_checkup']}%), {tract['pct_uninsured']}% uninsured, "
            f"severe transit barriers ({tract['pct_no_vehicle']}% households without a vehicle, "
            f"{tract['pct_mobility_difficulty']}% mobility difficulty), and {tract['km_to_safety_net']} km "
            f"straight-line distance to the nearest federal safety-net clinic "
            f"({tract.get('nearest_safety_net_name') or 'nearest safety-net site'}, "
            f"+{tract['extra_km_if_uninsured']} km extra distance if uninsured)."
        )

        enriched_selected.append(
            {
                "saturday_number": idx,
                "geo_id": geo_id,
                "total_pop": tract["total_pop"],
                "latitude": tract["latitude"],
                "longitude": tract["longitude"],
                "need_rank": tract["need_rank"],
                "scores": {
                    "static_need_score": tract["static_need_score"],
                    "dynamic_surge_multiplier": tract["dynamic_surge_multiplier"],
                    "final_need_score": tract["final_need_score"],
                },
                "supporting_metrics": {
                    "pct_uninsured": tract["pct_uninsured"],
                    "pct_diabetes": tract["pct_diabetes"],
                    "pct_asthma": tract["pct_asthma"],
                    "pct_high_bp": tract["pct_high_bp"],
                    "pct_checkup": tract["pct_checkup"],
                    "pct_no_vehicle": tract["pct_no_vehicle"],
                    "pct_mobility_difficulty": tract["pct_mobility_difficulty"],
                    "svi_socioeconomic": tract["svi_socioeconomic"],
                    "km_to_any_care": tract["km_to_any_care"],
                    "km_to_safety_net": tract["km_to_safety_net"],
                    "extra_km_if_uninsured": tract["extra_km_if_uninsured"],
                    "nearest_safety_net_name": tract.get("nearest_safety_net_name"),
                    "mean_pm": tract["mean_pm"],
                    "pm25_warning": PM25_WARNING,
                    "surge_week": tract.get("surge_week"),
                },
                "selection_reason": selection_reason,
                "why_preferred_over_alternatives": why_preferred,
            }
        )

    return enriched_selected, skipped_nearby


def rank_and_select_siting_plan(
    min_separation_km: float = 3.0,
    top_n: int = 20,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Queries BigQuery to rank census tracts and selects a 4-Saturday mobile clinic siting plan.

    Evaluates healthcare needs (`burden_tracts`), safety-net clinic accessibility
    (`tract_access`), transportation barriers (`pct_no_vehicle`, `pct_mobility_difficulty`),
    and regional respiratory trends (`disease_weekly`) without using race or ethnicity
    as an input. Stores the resulting siting plan in `session.state["plan_data"]`
    for the equity auditor.

    Args:
        min_separation_km: Minimum straight-line distance in km between the 4
            selected Saturday tracts so the van covers distinct geographic areas.
        top_n: Number of top-ranked candidate tracts to retrieve from BigQuery.
        tool_context: Optional ADK ToolContext used to persist `plan_data` in
            `session.state`.

    Returns:
        A `SitingPlan` dict. On success it contains `selected_tracts` (4 Saturday
        stops with scores, metrics, reasons, and alternative comparisons),
        `methodology`, `limitations`, `candidate_pool`, and
        `skipped_adjacent_high_need_tracts`. On failure `status` is `error` and
        `error_message` explains why. The same dict is stored in
        `session.state["plan_data"]`.
    """
    from google.cloud import bigquery

    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or "qwiklabs-gcp-04-e3eaf2b4f0c3"
    )
    dataset = os.environ.get("BQ_DATASET", "a4i_health")
    safe_top_n = max(4, min(int(top_n), 50))
    sql = SITING_RANKING_SQL_TEMPLATE.format(
        project=project,
        dataset=dataset,
        top_n=safe_top_n,
    )

    try:
        client = bigquery.Client(project=project)
        query_job = client.query(sql)
        rows = [dict(row.items()) for row in query_job.result()]
    except Exception as exc:
        siting_plan = SitingPlan(
            status="error",
            error_message=f"BigQuery siting query failed: {exc}",
        ).model_dump()
        if tool_context is not None and hasattr(tool_context, "state"):
            tool_context.state["plan_data"] = siting_plan
        return siting_plan

    selected_tracts, skipped_nearby = select_four_saturday_tracts(
        candidates=rows,
        min_separation_km=float(min_separation_km),
        num_saturdays=4,
    )

    limitations = [
        {
            "table": "burden_tracts",
            "grain": TABLE_RESOLUTIONS["burden_tracts"]["grain"],
            "limitation": TABLE_RESOLUTIONS["burden_tracts"]["caveat"],
        },
        {
            "table": "tract_access",
            "grain": TABLE_RESOLUTIONS["tract_access"]["grain"],
            "limitation": (
                f"{TABLE_RESOLUTIONS['tract_access']['caveat']} Also reflects "
                f"federal care_sites only ({TABLE_RESOLUTIONS['care_sites']['caveat']})"
            ),
        },
        {
            "table": "exposure_tracts",
            "grain": TABLE_RESOLUTIONS["exposure_tracts"]["grain"],
            "limitation": TABLE_RESOLUTIONS["exposure_tracts"]["caveat"],
        },
        {
            "table": "disease_weekly",
            "grain": TABLE_RESOLUTIONS["disease_weekly"]["grain"],
            "limitation": TABLE_RESOLUTIONS["disease_weekly"]["caveat"],
        },
        {
            "table": "shortage_areas",
            "grain": TABLE_RESOLUTIONS["shortage_areas"]["grain"],
            "limitation": (
                f"{TABLE_RESOLUTIONS['shortage_areas']['caveat']} In Fulton County, "
                "all 99 HRSA polygons are in 'Proposed For Withdrawal' status and "
                "therefore excluded from live shortage_areas."
            ),
        },
    ]

    siting_plan = SitingPlan.model_validate(
        {
            "status": "success",
            "project": project,
            "dataset": dataset,
            "methodology": {
                "weights": {
                    "healthcare_needs": 0.35,
                    "accessibility_barriers": 0.35,
                    "transportation_and_mobility_barriers": 0.30,
                    "environmental_pm25_context": 0.0,
                },
                "race_or_ethnicity_used_in_scoring": False,
                "min_separation_km": float(min_separation_km),
            },
            "selected_tracts": selected_tracts,
            "skipped_adjacent_high_need_tracts": skipped_nearby,
            "candidate_pool": rows,
            "limitations": limitations,
        }
    ).model_dump()

    if tool_context is not None and hasattr(tool_context, "state"):
        tool_context.state["plan_data"] = siting_plan

    return siting_plan


AUDIT_COLUMN = "svi_racial_ethnic"

AUDIT_PLAN_SQL_TEMPLATE = """\
WITH selected AS (
    SELECT geo_id FROM UNNEST([{geo_id_array}]) AS geo_id
)
SELECT
    b.geo_id,
    b.total_pop,
    NULLIF(b.svi_racial_ethnic, -999) AS svi_racial_ethnic,
    s.geo_id IS NOT NULL AS is_selected
FROM `{project}.{dataset}.burden_tracts` AS b
LEFT JOIN selected AS s
    ON b.geo_id = s.geo_id
WHERE b.total_pop >= 500
"""


def _population_weighted_mean(rows: list[dict[str, Any]], column: str) -> float | None:
    weighted = 0.0
    population = 0.0
    for row in rows:
        value = row.get(column)
        pop = float(row.get("total_pop") or 0)
        if value is None or pop <= 0:
            continue
        weighted += float(value) * pop
        population += pop
    if population == 0:
        return None
    return weighted / population


def compare_plan_demographics(
    rows: list[dict[str, Any]],
    selected_geo_ids: list[str],
    column: str = AUDIT_COLUMN,
) -> dict[str, Any]:
    """Compares a population-weighted mean on the selected tracts to the county.

    `rows` are tract dicts with `geo_id`, `total_pop`, and `column`. Nulls and
    non-positive populations are skipped. This does not call BigQuery.
    """
    selected_ids = {str(geo_id) for geo_id in selected_geo_ids}
    selected_rows = [row for row in rows if str(row.get("geo_id")) in selected_ids]
    selected_mean = _population_weighted_mean(selected_rows, column)
    county_mean = _population_weighted_mean(rows, column)
    if selected_mean is None or county_mean is None:
        return {
            "status": "error",
            "column": column,
            "selected_mean": None if selected_mean is None else round(selected_mean, 4),
            "county_mean": None if county_mean is None else round(county_mean, 4),
            "difference": None,
            "error_message": (
                "Not enough population with a non-null audit column to compare."
            ),
        }
    return {
        "status": "reviewed",
        "column": column,
        "selected_mean": round(selected_mean, 4),
        "county_mean": round(county_mean, 4),
        "difference": round(selected_mean - county_mean, 4),
        "error_message": None,
    }


def _quote_geo_ids(geo_ids: list[str]) -> str:
    quoted: list[str] = []
    for geo_id in geo_ids:
        text = str(geo_id)
        if not text.isdigit():
            raise ValueError(f"Census tract geo_id must be digits, got {geo_id!r}.")
        quoted.append(f"'{text}'")
    return ", ".join(quoted)


def _bq_project_and_dataset() -> tuple[str, str]:
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or "qwiklabs-gcp-04-e3eaf2b4f0c3"
    )
    dataset = os.environ.get("BQ_DATASET", "a4i_health")
    return project, dataset


def _write_state(
    tool_context: ToolContext | None, key: str, value: dict[str, Any]
) -> None:
    if tool_context is not None and hasattr(tool_context, "state"):
        tool_context.state[key] = value


def audit_plan_tool(tool_context: ToolContext | None = None) -> dict[str, Any]:
    """Compares the Saturday sites to the county on the SVI racial-ethnic theme.

    Reads `session.state["plan_data"]` and queries `burden_tracts.svi_racial_ethnic`,
    a column the planner ranking does not use. Stores the comparison in
    `session.state["plan_status"]`. Does not change which tracts were selected.

    Args:
        tool_context: ADK ToolContext whose state holds `plan_data`.

    Returns:
        A dict with `status` (`reviewed` or `error`), `column`, `selected_mean`,
        `county_mean`, `difference`, and `error_message`.
    """
    plan = {}
    if tool_context is not None and hasattr(tool_context, "state"):
        plan = tool_context.state.get("plan_data") or {}

    selected = plan.get("selected_tracts") or []
    geo_ids = [str(site.get("geo_id")) for site in selected if site.get("geo_id")]
    if plan.get("status") != "success" or not geo_ids:
        plan_status = {
            "status": "error",
            "column": AUDIT_COLUMN,
            "selected_mean": None,
            "county_mean": None,
            "difference": None,
            "error_message": "No successful plan_data to audit.",
        }
        _write_state(tool_context, "plan_status", plan_status)
        return plan_status

    from google.cloud import bigquery

    project, dataset = _bq_project_and_dataset()
    try:
        sql = AUDIT_PLAN_SQL_TEMPLATE.format(
            project=project,
            dataset=dataset,
            geo_id_array=_quote_geo_ids(geo_ids),
        )
        client = bigquery.Client(project=project)
        rows = [dict(row.items()) for row in client.query(sql).result()]
    except Exception as exc:
        plan_status = {
            "status": "error",
            "column": AUDIT_COLUMN,
            "selected_mean": None,
            "county_mean": None,
            "difference": None,
            "error_message": f"Equity audit query failed: {exc}",
        }
        _write_state(tool_context, "plan_status", plan_status)
        return plan_status

    plan_status = compare_plan_demographics(rows, geo_ids)
    _write_state(tool_context, "plan_status", plan_status)
    return plan_status
