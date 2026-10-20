"""Credential isolation: never silently fall back to the runtime's identity."""

import os

from google.auth import default, impersonated_credentials
from google.cloud import bigquery
from google.oauth2.credentials import Credentials as UserCredentials

from .config import dataset_id, project_id
from .contracts import TractEvidence
from .queries import AUDIT_COUNTS_SQL, AUDIT_HEALTH_SQL, BURDEN_COLUMNS


def runtime_credentials(source):
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=os.getenv(
            "CURBSIDE_RUNTIME_SA",
            f"curbside-agent@{project_id()}.iam.gserviceaccount.com",
        ),
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=600,
    )


def impersonated_client(identity: str) -> bigquery.Client:
    project = project_id()
    source, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if isinstance(source, UserCredentials):
        source = runtime_credentials(source)
    credentials = impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=f"curbside-{identity}@{project}.iam.gserviceaccount.com",
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=600,
    )
    return bigquery.Client(project=project, credentials=credentials)


def auditor_rows(county_fips: str) -> tuple[list[TractEvidence], list[dict]]:
    health, audit = (
        dataset_id("BQ_DATASET", "a4i_health"),
        dataset_id("BQ_AUDIT_DATASET", "a4i_audit"),
    )
    client = impersonated_client("auditor")
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("county_fips", "STRING", county_fips)
        ]
    )
    try:
        health_rows = client.query(
            AUDIT_HEALTH_SQL.format(
                columns=BURDEN_COLUMNS, project=project_id(), health=health
            ),
            job_config=job_config,
        ).result(timeout=45)
        tracts = [TractEvidence.model_validate(dict(r)) for r in health_rows]
        demographic_rows = client.query(
            AUDIT_COUNTS_SQL.format(project=project_id(), audit=audit),
            job_config=job_config,
        ).result(timeout=45)
        return tracts, [dict(r) for r in demographic_rows]
    finally:
        client.close()
