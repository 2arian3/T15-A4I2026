"""Explicit demo policy, with infrastructure settings supplied by the deploy."""

import os
import re

DEFAULT_MODEL = "gemini-3.8-flash"
SECOND_LANGUAGE = os.getenv("SECOND_LANGUAGE", "Spanish")
MAX_REVISIONS = 2
RUN_TIMEOUT_SECONDS = 180
MIN_POPULATION = 500
CATCHMENT_KM = 3.0
AUDIT_SPACING_KM = 3.0
REVISION_SPACING_KM = 5.0
TIE_MARGIN = 0.02
NEED_MATCH_MARGIN = 0.05
COUNTY_SHORTFALL_PP = 10.0
NEED_SHORTFALL_PP = 5.0


def project_id() -> str:
    # Agent Runtime owns GOOGLE_CLOUD_PROJECT and may supply a project number.
    project = os.getenv(
        "CURBSIDE_PROJECT_ID",
        os.getenv("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-e3eaf2b4f0c3"),
    )
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise ValueError(
            "Set CURBSIDE_PROJECT_ID to a valid textual Google Cloud project ID, "
            "not a project number"
        )
    return project


def dataset_id(env: str, default: str) -> str:
    value = os.getenv(env, default)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid {env}")
    return value
