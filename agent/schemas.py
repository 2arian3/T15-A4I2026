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

"""Pydantic contracts for Curbside tool results.

``SitingPlan`` is the planner response. ``rank_and_select_siting_plan`` returns
``model_dump()`` of this model and stores the same dict at
``session.state["plan_data"]``. The map reads ``selected_tracts``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SiteScores(BaseModel):
    static_need_score: float
    dynamic_surge_multiplier: float
    final_need_score: float


class SiteMetrics(BaseModel):
    pct_uninsured: float
    pct_diabetes: float
    pct_asthma: float
    pct_high_bp: float
    pct_checkup: float
    pct_no_vehicle: float
    pct_mobility_difficulty: float
    svi_socioeconomic: float
    km_to_any_care: float
    km_to_safety_net: float
    extra_km_if_uninsured: float
    nearest_safety_net_name: str | None = None
    mean_pm: float
    surge_week: str | None = None


class SaturdaySite(BaseModel):
    saturday_number: int = Field(
        description="Saturday index in the month, starting at 1."
    )
    geo_id: str
    total_pop: int
    latitude: float = Field(description="Census tract latitude in decimal degrees.")
    longitude: float = Field(description="Census tract longitude in decimal degrees.")
    need_rank: int
    scores: SiteScores
    supporting_metrics: SiteMetrics
    selection_reason: str = Field(
        description="Plain-language reason this tract was chosen for this Saturday."
    )
    why_preferred_over_alternatives: str


class PlanWeights(BaseModel):
    healthcare_needs: float
    accessibility_barriers: float
    transportation_and_mobility_barriers: float
    environmental_pm25_context: float


class PlanMethodology(BaseModel):
    weights: PlanWeights
    race_or_ethnicity_used_in_scoring: bool
    min_separation_km: float


class PlanLimitation(BaseModel):
    table: str
    grain: str
    limitation: str


class SitingPlan(BaseModel):
    status: Literal["success", "error"]
    error_message: str | None = None
    project: str | None = None
    dataset: str | None = None
    methodology: PlanMethodology | None = None
    selected_tracts: list[SaturdaySite] = Field(default_factory=list)
    skipped_adjacent_high_need_tracts: list[dict[str, Any]] = Field(
        default_factory=list
    )
    candidate_pool: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[PlanLimitation] = Field(default_factory=list)
