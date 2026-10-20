"""Unit tests for agent/tools.py."""

import pytest

from agent.tools import TABLE_RESOLUTIONS, describe_table_resolution

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
