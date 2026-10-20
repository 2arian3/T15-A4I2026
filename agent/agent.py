# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Curbside's evidence → planner → auditor → approval → explainer pipeline."""

import os

from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .config import DEFAULT_MODEL
from .pipeline import create_pipeline

MODEL = os.environ.get("CURBSIDE_MODEL", DEFAULT_MODEL)


def create_app(model=None):
    """Construct the same pipeline for serving and credential-free scripted tests."""
    plugins = []
    if os.getenv("CURBSIDE_DEV_LOGGING") == "1":
        from google.adk.plugins.logging_plugin import LoggingPlugin

        plugins.append(LoggingPlugin())
    analytics = os.getenv("CURBSIDE_ANALYTICS_DATASET")
    if analytics:
        from google.adk.plugins.bigquery_agent_analytics_plugin import (
            BigQueryAgentAnalyticsPlugin,
            BigQueryLoggerConfig,
        )

        from .config import project_id

        plugins.append(
            BigQueryAgentAnalyticsPlugin(
                project_id=project_id(),
                dataset_id=analytics,
                config=BigQueryLoggerConfig(
                    custom_metadata_allowlist=[
                        "plan_status",
                        "revision_count",
                        "audit_result",
                    ]
                ),
            )
        )
    root = create_pipeline(
        model or Gemini(model=MODEL, retry_options=types.HttpRetryOptions(attempts=3))
    )
    return App(root_agent=root, name="agent", plugins=plugins)


app = create_app()
root_agent = app.root_agent
