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

"""Curbside: decides where the mobile clinic van parks, and explains why.

This is the starting skeleton: a single coordinator agent with one tool of our
own. The multi-agent planner / equity auditor / explainer design is built on
top of this.

Gemini is reached through Vertex AI (Gemini platform). Locally that comes from
.env (GOOGLE_GENAI_USE_ENTERPRISE=true, GOOGLE_CLOUD_LOCATION=global); on Agent
Runtime the same variables are set by `make deploy`.
"""

import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools import describe_table_resolution

# gemini-3.6-flash is the model the challenge README verified. Override with
# CURBSIDE_MODEL (in .env, or `make deploy MODEL=...`).
DEFAULT_MODEL = "gemini-3.6-flash"
MODEL = os.environ.get("CURBSIDE_MODEL", DEFAULT_MODEL)

INSTRUCTION = """\
You are Curbside, an assistant for a county health department's outreach lead.
You help decide where one mobile clinic van should park on upcoming Saturdays,
and you explain the choice in plain language.

Rules:
- Before you make any claim about a specific neighborhood, call
  describe_table_resolution for each table you rely on, and never state
  something more precisely than that table's grain supports.
- Say which numbers are modeled and which are measured.
- Never use race or ethnicity as a reason to choose a place.
- You recommend places, never treatment for individuals.
"""

root_agent = Agent(
    # Keep in sync with create_params.root_agent_name in agents-cli-manifest.yaml:
    # telemetry reports this name as gen_ai.agent.name.
    name="curbside",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description="Decides where the mobile clinic van parks, and explains why.",
    instruction=INSTRUCTION,
    tools=[describe_table_resolution],
)

app = App(
    root_agent=root_agent,
    name="agent",
)
