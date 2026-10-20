"""The planner's sole tool operates on validated, demographic-free evidence."""

from math import isfinite

from google.adk.tools.tool_context import ToolContext

from . import config
from .contracts import Claim, EvidenceBrief, PlanRequest
from .scoring import make_plan


def build_clinic_plan(
    min_separation_km: float, exclude_tracts: list[str], tool_context: ToolContext
) -> dict:
    """Build one deterministic four-Saturday draft from the saved evidence.

    First round: spacing 0 and no exclusions. Revision: spacing 5 and exactly
    the anchor exclusions in the repair brief. No address or service is inferred.
    """
    state = tool_context.state
    if state.get("build_called"):
        return {
            "status": "error",
            "error_message": "Plan tool already called this round",
        }
    state["build_called"] = True
    try:
        revision = state.get("round_count", 1) - 1
        expected_spacing = config.REVISION_SPACING_KM if revision else 0
        expected_exclusions = state.get("audit_exclusions", []) if revision else []
        if (
            not isfinite(min_separation_km)
            or min_separation_km != expected_spacing
            or sorted(exclude_tracts) != sorted(expected_exclusions)
        ):
            raise ValueError(
                f"Use min_separation_km={expected_spacing:g}, exclude_tracts={expected_exclusions}"
            )
        request = PlanRequest.model_validate(state["request"])
        evidence = EvidenceBrief.model_validate(state["evidence_brief"])
        if evidence.county_fips != request.county_fips:
            raise ValueError("Request and evidence counties do not match")
        limits = [Claim.model_validate(c) for c in state["limits_brief"]]
        plan = make_plan(
            request, evidence, limits, min_separation_km, exclude_tracts, revision
        )
        state["revision_count"] = revision
        state["plan_data"] = plan.model_dump(mode="json")
        return {"status": "success", **state["plan_data"]}
    except (ValueError, KeyError, TypeError) as exc:
        state["plan_data"] = None
        return {"status": "error", "error_message": str(exc)}
