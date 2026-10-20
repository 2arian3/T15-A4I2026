"""Approval decisions depend only on validated tool state."""

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from pydantic import ValidationError

from . import config
from .contracts import AuditResult, ClinicPlan


def decide_plan(state) -> bool:
    """Return whether the planning loop should exit, and persist the decision."""
    state["plan_status"] = "REVISING"
    state["plan_summary"] = ""
    try:
        plan = ClinicPlan.model_validate(state.get("plan_data"))
        result = AuditResult.model_validate(state.get("audit_result"))
        valid = len(plan.stops) == 4 and plan.revision_count == state.get(
            "revision_count"
        )
    except (ValidationError, TypeError):
        plan, result, valid = None, None, False
    history = list(state.get("plan_history", []))
    if plan is not None and (
        not history or history[-1] != plan.model_dump(mode="json")
    ):
        history.append(plan.model_dump(mode="json"))
    state["plan_history"] = history
    if valid and result.verdict == "APPROVE":
        state["plan_status"] = "APPROVED"
        # Only the first Saturday is a flyer input. No counts, dates, IDs or services.
        state["plan_summary"] = (
            "The mobile clinic's first Saturday stop is approved. "
            "Invite residents to meet clinic staff. "
            "Use [DATE] [TIME] [LOCATION] [CONTACT]; the outreach lead fills them in."
        )
        return True
    fixes = (
        result.must_fix
        if result
        else [
            "No valid audit was completed. Verify the saved plan and rerun the audit."
        ]
    )
    state["planner_feedback"] = "MUST FIX:\n" + "\n".join(fixes)
    state["audit_exclusions"] = result.excluded_anchors if result else []
    if (
        state.get("round_count", 1) >= config.MAX_REVISIONS + 1
        or state.get("revision_count", 0) >= config.MAX_REVISIONS
    ):
        state["plan_status"] = "NEEDS_HUMAN"
        return True
    return False


class ApprovalGate(BaseAgent):
    async def _run_async_impl(self, ctx):
        exit_loop = decide_plan(ctx.session.state)
        state = ctx.session.state
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(
                state_delta={
                    k: state[k]
                    for k in (
                        "plan_status",
                        "plan_summary",
                        "plan_history",
                        "planner_feedback",
                        "audit_exclusions",
                    )
                    if k in state
                },
                escalate=exit_loop,
            ),
            custom_metadata={
                "plan_status": state["plan_status"],
                "revision_count": state.get("revision_count", 0),
                "audit_result": state.get("audit_result"),
            },
        )
