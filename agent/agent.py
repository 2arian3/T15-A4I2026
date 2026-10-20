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

"""Curbside: Planner, then Equity auditor, then Explainer.

The root name stays `curbside` so telemetry and the deployed runtime match
agents-cli-manifest.yaml. The three sub-agents use the design names. Order is
fixed: a model cannot skip the auditor.

Gemini is reached through Vertex AI (Gemini platform). Locally that comes from
.env (GOOGLE_GENAI_USE_ENTERPRISE=true, GOOGLE_CLOUD_LOCATION=global); on Agent
Runtime the same variables are set by `make deploy`.
"""

import os

from google.adk.agents import Agent, SequentialAgent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools import (
    audit_plan_tool,
    describe_table_resolution,
    rank_and_select_siting_plan,
)

# gemini-3.6-flash is the model the challenge README verified. Override with
# CURBSIDE_MODEL (in .env, or `make deploy MODEL=...`).
DEFAULT_MODEL = "gemini-3.8-flash"
MODEL = os.environ.get("CURBSIDE_MODEL", DEFAULT_MODEL)

PLANNER_INSTRUCTION = """\
You are the Planner for a county health department's mobile clinic van.
Choose where the van should park on the 4 Saturdays of next month.

Rules:
- Before you make any claim about a specific neighborhood, call
  describe_table_resolution for each table you rely on, and never state
  something more precisely than that table's grain supports.
- When asked to plan or select the 4 Saturday locations, call
  rank_and_select_siting_plan to query BigQuery, compute the composite need and
  accessibility scores, select 4 geographically separated census tracts, and
  save the structured plan in session.state["plan_data"].
- For each of the 4 selected Saturday tracts, report its census tract GEOID,
  scores, supporting metrics (disease burden, uninsured %, no-vehicle %,
  straight-line km to safety-net care), and why it was chosen over alternatives.
- Say which numbers are modeled (such as CDC PLACES prevalence in burden_tracts)
  and which are measured or upper-bound straight-line distances.
- Explicitly report data limitations (12 km PM2.5 grid in exposure_tracts,
  multi-county HSA grain in disease_weekly, federal-only clinics in care_sites,
  and empty shortage_areas in Fulton County).
- Never use race or ethnicity as a reason to choose a place.
- You recommend places, never treatment for individuals.
- Do not write the neighborhood flyer. The Explainer does that.
"""

AUDITOR_INSTRUCTION = """\
You are the Equity auditor. You did not choose the Saturday sites, and you do
not defend them.

Rules:
- Call audit_plan_tool. It reads session.state["plan_data"] and compares the
  selected tracts to the county on the SVI racial-ethnic theme, a column the
  Planner was not allowed to use.
- Report the comparison, including when the difference is small or there is
  no difference. Both results are valid.
- Do not move a Saturday site and do not propose a new ranking.
- Do not give treatment advice.
"""

EXPLAINER_INSTRUCTION = """\
You are the Explainer. Write a short flyer an outreach lead can hand to a
neighborhood. Use warm, plain language at about a sixth-grade reading level.

Plan:
{plan_data?}

Audit:
{plan_status?}

Rules:
- Talk about places the van will park. Do not give treatment advice.
- Use only the plan and the audit above. Do not invent tracts or numbers.
- If the audit disagrees with the plan, say so in the flyer. Do not quietly
  drop a Saturday site to hide the disagreement.
- If the plan is missing, say that the Saturday sites are not ready yet.
"""


def _gemini() -> Gemini:
    return Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


planner = Agent(
    name="planner",
    model=_gemini(),
    description="Planner",
    instruction=PLANNER_INSTRUCTION,
    tools=[describe_table_resolution, rank_and_select_siting_plan],
)

equity_auditor = Agent(
    name="equity_auditor",
    model=_gemini(),
    description="Equity auditor",
    instruction=AUDITOR_INSTRUCTION,
    tools=[audit_plan_tool],
    include_contents="none",
)

explainer = Agent(
    name="explainer",
    model=_gemini(),
    description="Explainer",
    instruction=EXPLAINER_INSTRUCTION,
    include_contents="none",
    output_key="flyer",
)

root_agent = SequentialAgent(
    name="curbside",
    description="Plans Saturday clinic sites, audits them, then explains the choice.",
    sub_agents=[planner, equity_auditor, explainer],
)

app = App(
    root_agent=root_agent,
    name="agent",
)
