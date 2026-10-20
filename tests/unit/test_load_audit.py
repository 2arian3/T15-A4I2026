"""Public-schema adaptation stays explicit and rejects incorrect denominators."""

import pytest

from scripts.load_audit import demographic_projection, language_variables


def test_projection_maps_known_count_columns_and_never_uses_shares():
    fields = {
        "geo_id",
        "total_pop",
        "white_pop",
        "black_pop",
        "asian_pop",
        "amerindian_pop",
        "pacific_islander_pop",
        "other_race_pop",
        "two_or_more_races_pop",
        "hispanic_pop",
    }
    sql = demographic_projection(fields)
    assert "amerindian_pop AS native_pop" in sql
    assert "pacific_islander_pop AS pacific_pop" in sql
    assert "pct_" not in sql
    assert "CAST(NULL AS INT64) AS pacific_pop" in demographic_projection(
        fields - {"pacific_islander_pop"}
    )
    with pytest.raises(ValueError, match="black_pop"):
        demographic_projection(fields - {"black_pop"})


def test_language_metadata_requires_household_counts_not_individuals():
    variables = {"B16002_001E": {"label": "Estimate!!Total:"}}
    for i in range(2, 6):
        variables[f"B16002_{i:03}E"] = {
            "label": f"Estimate!!Total:!!Language {i}:!!Limited English speaking households:"
        }
    assert len(language_variables(variables)) == 4
    with pytest.raises(ValueError):
        language_variables({"X": {"label": "Limited English speaking people"}})


def test_census_supplement_fills_missing_pacific_and_verifies_definitions():
    from scripts.load_audit import verify_demographics

    row = {
        "geo_id": "13121000001",
        "total_pop": 100,
        "white_pop": 40,
        "black_pop": 30,
        "asian_pop": 10,
        "native_pop": 1,
        "pacific_pop": None,
        "other_pop": 2,
        "multiracial_pop": 2,
        "hispanic_pop": 10,
    }
    census = {**row, "pacific_pop": 5}
    verify_demographics(row, census)
    assert row["pacific_pop"] == 5
    with pytest.raises(ValueError, match="definition"):
        verify_demographics({**row, "white_pop": 50}, census)


def census_response(
    body,
    content_type="application/json",
    url="https://api.census.gov/data/2020/acs/acs5",
):
    from email.message import Message
    from io import BytesIO

    response = BytesIO(body)
    response.headers = Message()
    response.headers["Content-Type"] = content_type
    response.geturl = lambda: url
    return response


def test_census_data_requests_include_key_but_metadata_does_not(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    import scripts.load_audit as loader

    monkeypatch.setenv("CENSUS_API_KEY", "local-test-key")
    urls = []

    def open_url(url, **kwargs):
        urls.append(url)
        return census_response(b"[]")

    monkeypatch.setattr(loader, "urlopen", open_url)
    assert (
        loader.get_json(
            "https://api.census.gov/data/2020/acs/acs5?get=B03002_001E&for=tract:*"
        )
        == []
    )
    assert parse_qs(urlsplit(urls[0]).query)["key"] == ["local-test-key"]
    assert (
        loader.get_json("https://api.census.gov/data/2020/acs/acs5/groups/B03002.json")
        == []
    )
    assert "key" not in parse_qs(urlsplit(urls[1]).query)


def test_missing_census_key_fails_before_any_bigquery_job(monkeypatch):
    import scripts.load_audit as loader

    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.setattr(
        loader.bigquery,
        "Client",
        lambda **kwargs: pytest.fail(
            "Must validate Census key before starting BigQuery"
        ),
    )
    with pytest.raises(RuntimeError, match="CENSUS_API_KEY"):
        loader.load("qwiklabs-gcp-04-e3eaf2b4f0c3", "13121")


def test_census_html_key_error_is_actionable_and_redacted(monkeypatch):
    import scripts.load_audit as loader

    monkeypatch.setenv("CENSUS_API_KEY", "local-test-key")
    monkeypatch.setattr(
        loader,
        "urlopen",
        lambda *args, **kwargs: census_response(
            b"<html>Missing Key</html>",
            "text/html",
            "https://api.census.gov/data/missing_key.html?original=local-test-key",
        ),
    )
    with pytest.raises(RuntimeError, match="CENSUS_API_KEY") as error:
        loader.get_json("https://api.census.gov/data/2020/acs/acs5?get=B03002_001E")
    assert "local-test-key" not in str(error.value)


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b"", "application/json"),
        (b"broken", "application/json"),
        (b"<html>Unavailable</html>", "text/html"),
    ],
)
def test_census_unexpected_response_has_clear_error(monkeypatch, body, content_type):
    import scripts.load_audit as loader

    monkeypatch.setenv("CENSUS_API_KEY", "local-test-key")
    monkeypatch.setattr(
        loader, "urlopen", lambda *args, **kwargs: census_response(body, content_type)
    )
    with pytest.raises(RuntimeError, match="Census") as error:
        loader.get_json("https://api.census.gov/data/2020/acs/acs5?get=B03002_001E")
    assert "local-test-key" not in str(error.value)


@pytest.mark.parametrize("http_error", [True, False])
def test_census_network_errors_never_expose_key(monkeypatch, http_error):
    from urllib.error import HTTPError, URLError

    import scripts.load_audit as loader

    monkeypatch.setenv("CENSUS_API_KEY", "local-test-key")

    def fail(url, **kwargs):
        if http_error:
            raise HTTPError(url, 403, "local-test-key", {}, None)
        raise URLError("local-test-key")

    monkeypatch.setattr(loader, "urlopen", fail)
    with pytest.raises(RuntimeError, match="Census") as error:
        loader.get_json("https://api.census.gov/data/2020/acs/acs5?get=B03002_001E")
    assert "local-test-key" not in str(error.value)


@pytest.mark.parametrize("group,count", [("C16002", 4), ("B16002", 12)])
def test_language_rows_uses_available_same_vintage_household_group(
    monkeypatch, group, count
):
    from urllib.parse import parse_qs, urlsplit

    import scripts.load_audit as loader

    total = f"{group}_001E"
    fields = [f"{group}_{i:03}E" for i in range(4, 4 + count * 3, 3)]
    variables = {
        total: {"label": "Estimate!!Total:"},
        **{
            field: {
                "label": "Estimate!!Total:!!Language:!!Limited English speaking household"
            }
            for field in fields
        },
    }
    seen = []

    def get_json(url):
        seen.append(url)
        if url.endswith("/groups.json"):
            return {"groups": [{"name": group}]}
        if url.endswith(f"/groups/{group}.json"):
            return {"variables": variables}
        parsed = urlsplit(url)
        assert parsed.path == "/data/2020/acs/acs5"
        assert parse_qs(parsed.query)["get"] == [",".join([total, *fields])]
        return [
            [total, *fields, "state", "county", "tract"],
            ["100", *["1"] * count, "13", "121", "000100"],
        ]

    monkeypatch.setattr(loader, "get_json", get_json)
    assert loader.language_rows(2020, "13121") == [
        {
            "geo_id": "13121000100",
            "total_households": 100,
            "limited_english_households": count,
        }
    ]
    assert len(seen) == 3


def test_missing_household_group_has_clear_error(monkeypatch):
    import scripts.load_audit as loader

    monkeypatch.setattr(
        loader, "get_json", lambda url: {"groups": [{"name": "B16001"}]}
    )
    with pytest.raises(ValueError, match="household"):
        loader.language_rows(2020, "13121")


def test_metadata_404_is_not_reported_as_a_key_problem(monkeypatch):
    from urllib.error import HTTPError

    import scripts.load_audit as loader

    def not_found(url, **kwargs):
        raise HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(loader, "urlopen", not_found)
    with pytest.raises(RuntimeError, match="404") as error:
        loader.get_json("https://api.census.gov/data/2020/acs/acs5/groups/B16002.json")
    assert "CENSUS_API_KEY" not in str(error.value)
