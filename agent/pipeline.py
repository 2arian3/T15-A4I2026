"""Bounded orchestration; state is the only communication between roles."""

import asyncio
import json
import logging

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import ValidationError

from . import config, prompts
from .audit import audit_plan_tool
from .contracts import ClinicPlan, PlanRequest
from .evidence import evidence_agent
from .flyer import NOT_APPROVED, FlyerChecks
from .gate import ApprovalGate
from .planner import build_clinic_plan

logger = logging.getLogger(__name__)
INPUT_HELP = (
    "Provide a PlanRequest JSON with county_fips, month (YYYY-MM), and four ordered Saturday dates in saturdays. "
    'Example: {"county_fips":"13121","month":"2026-11","saturdays":["2026-11-07","2026-11-14","2026-11-21","2026-11-28"]}. '
    'You can also seed session.state["request"]. Curbside plans clinic stops; it does not offer medical advice.'
)


def begin_round(callback_context):
    state = callback_context.state
    state["round_count"] = state.get("round_count", 0) + 1
    for key in ("plan_data", "audit_result"):
        state[key] = None
    for key in ("audit_error", "calendar_text", "audit_report"):
        state[key] = ""
    state["build_called"] = False
    state["audit_called"] = False
    state["plan_status"] = "REVISING"


def guard_explainer(callback_context):
    if callback_context.state.get("plan_status") != "APPROVED":
        callback_context.state["flyer_text"] = NOT_APPROVED
        return types.Content(role="model", parts=[types.Part(text=NOT_APPROVED)])
    return None


def isolate_model_request(callback_context, llm_request):
    # ADK include_contents='none' still forwards the last agent reply. Retain
    # only this role's tool exchange; all allowed state is in its instruction.
    name = callback_context.agent_name
    allowed = {"planner": "build_clinic_plan", "equity_auditor": "audit_plan_tool"}.get(
        name
    )
    contents = []
    for content in llm_request.contents:
        parts = [
            p
            for p in content.parts or []
            if allowed
            and (
                (p.function_call and p.function_call.name == allowed)
                or (p.function_response and p.function_response.name == allowed)
            )
        ]
        if parts:
            contents.append(types.Content(role=content.role, parts=parts))
    llm_request.contents = contents or [
        types.Content(
            role="user", parts=[types.Part(text="Follow your role instructions.")]
        )
    ]


class OutreachPipeline(BaseAgent):
    timeout_seconds: float = config.RUN_TIMEOUT_SECONDS

    async def _run_async_impl(self, ctx):
        state = ctx.session.state
        text = "".join(
            p.text or "" for p in (ctx.user_content.parts if ctx.user_content else [])
        )
        try:
            raw = (
                json.loads(text)
                if text.strip().startswith("{")
                else state.get("request")
            )
            request = PlanRequest.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                content=types.Content(
                    role="model", parts=[types.Part(text=INPUT_HELP)]
                ),
            )
            return
        initial = {
            "request": request.model_dump(mode="json"),
            "plan_data": None,
            "audit_result": None,
            "evidence_brief": None,
            "limits_brief": [],
            "ev_burden": None,
            "ev_access": None,
            "ev_context": None,
            "revision_count": 0,
            "round_count": 0,
            "plan_history": [],
            "plan_status": "REVISING",
            "plan_summary": "",
            "planner_feedback": "",
            "audit_exclusions": [],
            "audit_report": "",
            "calendar_text": "",
            "flyer_text": "",
            "flyer_check": "",
            "flyer_status": "FAIL",
            "flyer_attempts": 0,
            "run_error": "",
        }
        state.update(initial)
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(state_delta=initial),
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async for event in self.sub_agents[0].run_async(ctx):
                    # The root publishes a handout only after FlyerChecks passes.
                    yield (
                        event.model_copy(update={"content": None})
                        if event.author == "explainer"
                        else event
                    )
        except Exception as exc:
            message = "Run timed out" if isinstance(exc, TimeoutError) else str(exc)
            logger.exception("Outreach run requires human review")
            delta = {
                "plan_status": "NEEDS_HUMAN",
                "run_error": message,
                "plan_summary": "",
                "flyer_text": NOT_APPROVED,
                "flyer_status": "FAIL",
            }
            state.update(delta)
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                actions=EventActions(state_delta=delta),
            )
        if state["plan_status"] == "APPROVED":
            plan = ClinicPlan.model_validate(state["plan_data"])
            coordinates = "\n".join(
                [
                    "Anchor tract centroids (planning references, not confirmed clinic locations).\n",
                    "| Date | Anchor tract | Latitude | Longitude |",
                    "| --- | --- | --- | --- |",
                    *[
                        f"| {stop.date} | {stop.anchor_geo_id} | {stop.latitude:.6f} | {stop.longitude:.6f} |"
                        for stop in plan.stops
                    ],
                ]
            )
            handout = (
                state["flyer_text"]
                if state["flyer_status"] == "PASS"
                else "Flyer failed checks; draft withheld. See flyer_check for required repairs."
            )
            result = "\n\n".join(
                [
                    state.get("calendar_text", ""),
                    coordinates,
                    state.get("audit_report", ""),
                    handout,
                ]
            )
        else:
            result = "\n\n".join(
                [
                    "NEEDS_HUMAN: the plan needs an outreach lead's decision.",
                    state.get("run_error", ""),
                    state.get("calendar_text", ""),
                    state.get("audit_error") or state.get("audit_report", ""),
                    "Completed drafts are attached in plan_history.",
                    NOT_APPROVED,
                ]
            )
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=result)]),
            custom_metadata={
                "plan_status": state["plan_status"],
                "revision_count": state["revision_count"],
                "audit_result": state.get("audit_result"),
            },
        )


def create_pipeline(model):
    planner = LlmAgent(
        name="planner",
        model=model,
        instruction=prompts.PLANNER,
        tools=[build_clinic_plan],
        include_contents="none",
        output_key="calendar_text",
        before_agent_callback=begin_round,
        before_model_callback=isolate_model_request,
    )
    auditor = LlmAgent(
        name="equity_auditor",
        model=model,
        instruction=prompts.AUDITOR,
        tools=[audit_plan_tool],
        include_contents="none",
        output_key="audit_report",
        before_model_callback=isolate_model_request,
    )
    explainer = LlmAgent(
        name="explainer",
        model=model,
        instruction=prompts.EXPLAINER
        + f"\nConfigured second language: {config.SECOND_LANGUAGE}\n",
        tools=[],
        include_contents="none",
        output_key="flyer_text",
        before_agent_callback=guard_explainer,
        before_model_callback=isolate_model_request,
    )
    return OutreachPipeline(
        name="curbside",
        description="Decides where the mobile clinic van parks, audits the plan, and explains why.",
        sub_agents=[
            SequentialAgent(
                name="outreach",
                sub_agents=[
                    evidence_agent(),
                    LoopAgent(
                        name="plan_loop",
                        max_iterations=config.MAX_REVISIONS + 1,
                        sub_agents=[
                            planner,
                            auditor,
                            ApprovalGate(name="approval_gate"),
                        ],
                    ),
                    LoopAgent(
                        name="flyer_loop",
                        max_iterations=3,
                        sub_agents=[explainer, FlyerChecks(name="flyer_checks")],
                    ),
                ],
            )
        ],
    )
