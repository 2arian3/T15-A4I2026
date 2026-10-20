"""Load county ACS counts into the audit-only dataset, without touching health data.

Run as a human data administrator, never as the runtime/planner. BigQuery source
schema and Census household-language labels are verified before any destination is changed.
"""

import argparse
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from google.cloud import bigquery

COLUMNS = {
    "geo_id": ("geo_id",),
    "total_pop": ("total_pop",),
    "white_pop": ("white_pop",),
    "black_pop": ("black_pop",),
    "asian_pop": ("asian_pop",),
    "native_pop": ("amerindian_pop", "native_pop"),
    "pacific_pop": ("pacific_islander_pop", "hawaiian_pop", "pacific_pop"),
    "other_pop": ("other_race_pop", "other_pop"),
    "multiracial_pop": ("two_or_more_races_pop", "multiracial_pop"),
    "hispanic_pop": ("hispanic_pop",),
}
CENSUS_COUNTS = {
    "total_pop": "B03002_001E",
    "white_pop": "B03002_003E",
    "black_pop": "B03002_004E",
    "native_pop": "B03002_005E",
    "asian_pop": "B03002_006E",
    "pacific_pop": "B03002_007E",
    "other_pop": "B03002_008E",
    "multiracial_pop": "B03002_009E",
    "hispanic_pop": "B03002_012E",
}


def demographic_projection(columns: set[str]) -> str:
    projection = []
    for target, candidates in COLUMNS.items():
        source = next((c for c in candidates if c in columns), None)
        if not source and target == "pacific_pop":
            projection.append("CAST(NULL AS INT64) AS pacific_pop")
            continue
        if not source:
            raise ValueError(
                f"Public ACS source is missing a count column for {target}; no shares or inferred counts will be substituted"
            )
        projection.append(source if source == target else f"{source} AS {target}")
    return ", ".join(projection)


def language_variables(variables: dict, group: str = "B16002") -> list[str]:
    selected = sorted(
        name
        for name, info in variables.items()
        if re.fullmatch(rf"{group}_\d{{3}}E", name)
        and (
            info.get("label", "").lower().rstrip(":").split("!!")[-1]
            in {
                "limited english speaking household",
                "limited english speaking households",
            }
            or "!!no one 14 and over speaks english" in info.get("label", "").lower()
        )
    )
    # The collapsed table has four languages; newer detailed releases have twelve.
    if len(selected) not in {4, 12} or f"{group}_001E" not in variables:
        raise ValueError(
            f"Cannot identify all limited-English household count variables in ACS {group}"
        )
    return selected


def census_api_key() -> str:
    key = os.getenv("CENSUS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is required for Census data queries. Request and activate a key at "
            "https://api.census.gov/data/key_signup.html, then set CENSUS_API_KEY in .env or export it."
        )
    return key


def get_json(url: str):
    parts = urlsplit(url)
    endpoint = f"{parts.scheme}://{parts.netloc}{parts.path}"
    if "get" in parse_qs(parts.query):
        url += "&" + urlencode({"key": census_api_key()})
    try:
        with urlopen(url, timeout=45) as response:
            if "key" in urlsplit(response.geturl()).path.lower():
                raise RuntimeError(
                    "Census rejected CENSUS_API_KEY. Check that the key is correct and activated."
                )
            content_type = response.headers.get_content_type()
            if content_type != "application/json" and not content_type.endswith(
                "+json"
            ):
                raise RuntimeError(
                    f"Census returned non-JSON ({content_type}) at {endpoint}. Check CENSUS_API_KEY or retry if the service is unavailable."
                )
            try:
                return json.load(response)
            except (ValueError, UnicodeError):
                raise RuntimeError(
                    f"Census returned invalid or empty JSON at {endpoint}. Retry the query."
                ) from None
    except HTTPError as exc:
        hint = (
            "Check CENSUS_API_KEY."
            if exc.code in {401, 403}
            else "Check the requested Census dataset and query."
        )
        raise RuntimeError(
            f"Census API returned HTTP {exc.code} at {endpoint}. {hint}"
        ) from None
    except URLError:
        raise RuntimeError(
            f"Could not reach Census API at {endpoint}. Check connectivity and retry."
        ) from None


def verify_demographics(row: dict, census: dict):
    for column in CENSUS_COUNTS:
        if row[column] is not None and row[column] != census[column]:
            raise ValueError(
                f"ACS count definition or vintage differs for {row['geo_id']}: {column}"
            )
        row[column] = census[column]
    if (
        any(row[k] < 0 for k in CENSUS_COUNTS)
        or sum(row[k] for k in CENSUS_COUNTS if k != "total_pop") != row["total_pop"]
    ):
        raise ValueError(
            "ACS mutually exclusive demographic counts do not sum to total population"
        )


def demographic_rows(year: int, county: str) -> dict[str, dict]:
    base = f"https://api.census.gov/data/{year}/acs/acs5"
    variables = get_json(base + "/groups/B03002.json")["variables"]
    for column, field in CENSUS_COUNTS.items():
        label = variables.get(field, {}).get("label", "").lower()
        if not label.startswith("estimate!!total") or (
            column not in {"total_pop", "hispanic_pop"}
            and "not hispanic or latino" not in label
        ):
            raise ValueError("Unexpected ACS B03002 demographic count definitions")
    query = urlencode(
        {
            "get": ",".join(CENSUS_COUNTS.values()),
            "for": "tract:*",
            "in": f"state:{county[:2]} county:{county[2:]}",
        }
    )
    data = get_json(base + "?" + query)
    rows = {}
    for values in data[1:]:
        raw = dict(zip(data[0], values, strict=True))
        geo_id = raw["state"] + raw["county"] + raw["tract"]
        rows[geo_id] = {
            column: int(raw[field]) for column, field in CENSUS_COUNTS.items()
        }
    return rows


def language_rows(year: int, county: str) -> list[dict]:
    # Same ACS vintage as the public demographic source. Never use a person-level
    # language field as a household count, and never select the flyer's language.
    base = f"https://api.census.gov/data/{year}/acs/acs5"
    available = {g["name"] for g in get_json(base + "/groups.json")["groups"]}
    group = next((g for g in ("B16002", "C16002") if g in available), None)
    if group is None:
        raise ValueError(f"ACS {year} has no supported limited-English household table")
    fields = language_variables(
        get_json(base + f"/groups/{group}.json")["variables"], group
    )
    total_field = f"{group}_001E"
    query = urlencode(
        {
            "get": ",".join([total_field, *fields]),
            "for": "tract:*",
            "in": f"state:{county[:2]} county:{county[2:]}",
        }
    )
    data = get_json(base + "?" + query)
    rows = []
    for values in data[1:]:
        row = dict(zip(data[0], values, strict=True))
        counts = [int(row[f]) for f in fields]
        total = int(row[total_field])
        if total < 0 or any(v < 0 for v in counts) or sum(counts) > total:
            raise ValueError(
                "Census returned missing or inconsistent language-household counts"
            )
        rows.append(
            {
                "geo_id": row["state"] + row["county"] + row["tract"],
                "total_households": total,
                "limited_english_households": sum(counts),
            }
        )
    if not rows:
        raise ValueError("Census returned no language-household rows")
    return rows


def load(
    project: str, county: str, source: str | None = None, *, dry_run: bool = False
):
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project) or not re.fullmatch(
        r"\d{5}", county
    ):
        raise ValueError("Invalid project ID or county FIPS")
    if not dry_run:
        census_api_key()
    client = bigquery.Client(project=project)
    try:
        if source is None:
            # Metadata only: deterministic latest available tract five-year release.
            available = [
                t.table_id
                for t in client.list_tables("bigquery-public-data.census_bureau_acs")
                if re.fullmatch(r"censustract_20\d{2}_5yr", t.table_id)
            ]
            if not available:
                raise ValueError("No public ACS tract five-year table found")
            source = "bigquery-public-data.census_bureau_acs." + max(available)
        match = re.fullmatch(
            r"bigquery-public-data\.census_bureau_acs\.censustract_(20\d{2})_5yr",
            source,
        )
        if not match:
            raise ValueError(
                "Source must be a public census_bureau_acs.censustract_YEAR_5yr table"
            )
        projection = demographic_projection(
            {f.name for f in client.get_table(source).schema}
        )
        query = f"SELECT {projection} FROM `{source}` WHERE STARTS_WITH(geo_id, @county_fips)"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("county_fips", "STRING", county)
            ],
            dry_run=dry_run,
        )
        job = client.query(query, job_config=job_config)
        if dry_run:
            print(
                f"Validated source {source}; estimated bytes {job.total_bytes_processed}. No data written."
            )
            return
        rows = [dict(r) for r in job.result(timeout=60)]
        census = demographic_rows(int(match[1]), county)
        languages = {r["geo_id"]: r for r in language_rows(int(match[1]), county)}
        health_ids = {
            r["geo_id"]
            for r in client.query(
                f"SELECT geo_id FROM `{project}.a4i_health.burden_tracts` WHERE STARTS_WITH(geo_id,@county_fips)",
                job_config=job_config,
            ).result(timeout=60)
        }
        ids = {r["geo_id"] for r in rows}
        if (
            not rows
            or len(ids) != len(rows)
            or not health_ids
            or not health_ids.issubset(ids)
        ):
            raise ValueError(
                "ACS tract vintage does not cover loaded health tracts; resolve boundaries before loading"
            )
        if not ids.issubset(languages):
            raise ValueError(
                "Language household estimates do not cover every audit tract"
            )
        for row in rows:
            if row["geo_id"] not in census:
                raise ValueError(
                    "Census demographic estimates do not cover every audit tract"
                )
            verify_demographics(row, census[row["geo_id"]])
            row.update(languages[row["geo_id"]])
            if any(
                row[k] is None or row[k] < 0 or row[k] > row["total_pop"]
                for k in COLUMNS
                if k not in {"geo_id", "total_pop"}
            ):
                raise ValueError("ACS population counts are incomplete or inconsistent")
            row["source_table"] = source
        dataset = bigquery.Dataset(f"{project}.a4i_audit")
        dataset.location = "US"
        client.create_dataset(dataset, exists_ok=True)
        # Stage and swap this county in a transaction; other counties are preserved.
        import uuid

        stage = f"{project}.a4i_audit._load_{uuid.uuid4().hex}"
        schema = [
            bigquery.SchemaField(
                k, "STRING" if k in {"geo_id", "source_table"} else "INT64"
            )
            for k in rows[0]
        ]
        try:
            client.load_table_from_json(
                rows, stage, job_config=bigquery.LoadJobConfig(schema=schema)
            ).result(timeout=60)
            client.query(
                f"CREATE TABLE IF NOT EXISTS `{project}.a4i_audit.tract_demographics` AS SELECT * FROM `{stage}` WHERE FALSE"
            ).result(timeout=60)
            client.query(
                f"""BEGIN TRANSACTION;
                DELETE FROM `{project}.a4i_audit.tract_demographics` WHERE STARTS_WITH(geo_id,@county_fips);
                INSERT INTO `{project}.a4i_audit.tract_demographics` SELECT * FROM `{stage}`;
                COMMIT TRANSACTION;""",
                job_config=job_config,
            ).result(timeout=60)
        finally:
            client.delete_table(stage, not_found_ok=True)
        print(
            f"Loaded {len(rows)} audit tracts for {county} from {source} and same-vintage Census household-language estimates."
        )
    finally:
        client.close()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--county", default="13121")
    parser.add_argument("--source-table")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        load(args.project, args.county, args.source_table, dry_run=args.dry_run)
    except RuntimeError as exc:
        parser.exit(1, f"Error: {exc}\n")
