"""Function tools for the Curbside agent.

These are deterministic Python tools the agent can call. They carry the
project's knowledge about the data rather than querying it, so they are cheap,
safe and unit-testable without Google Cloud credentials.
"""

from __future__ import annotations

from typing import TypedDict


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
