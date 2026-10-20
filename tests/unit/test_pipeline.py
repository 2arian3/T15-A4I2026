"""Real ADK Runner with scripted models, no cloud credentials or model calls."""

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field
from test_outreach import GOOD_FLYER, candidates, demographics, request

from agent.agent import create_app
from agent.contracts import AuditResult


class RuntimeContractSessionService(InMemorySessionService):
    async def append_event(self, session, event):
        # Agent Runtime rejects empty IDs; InMemorySessionService accepts them.
        assert event.invocation_id, f"{event.author}: event.invocation_id is required"
        return await super().append_event(session=session, event=event)


class ScriptedModel(BaseLlm):
    model: str = "scripted"
    requests: list = Field(default_factory=list)
    broken_flyer: bool = False
    skip_planner: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request.model_copy(deep=True))
        tools = llm_request.tools_dict
        if "build_clinic_plan" in tools:
            if self.skip_planner:
                part = types.Part(text="No tool called")
            elif (
                llm_request.contents
                and llm_request.contents[-1].parts[0].function_response
            ):
                part = types.Part(text="PRIVATE PLANNER REASONING: verified calendar")
            else:
                instruction = llm_request.config.system_instruction
                revision = "Round: 1" not in instruction
                part = types.Part(
                    function_call=types.FunctionCall(
                        name="build_clinic_plan",
                        args={
                            "min_separation_km": 5 if revision else 0,
                            "exclude_tracts": [],
                        },
                    )
                )
        elif "audit_plan_tool" in tools:
            if (
                llm_request.contents
                and llm_request.contents[-1].parts[0].function_response
            ):
                # Deliberately lie: the deterministic gate must ignore this text.
                part = types.Part(text="APPROVE")
            else:
                part = types.Part(
                    function_call=types.FunctionCall(name="audit_plan_tool", args={})
                )
        else:
            part = types.Part(text="bad flyer" if self.broken_flyer else GOOD_FLYER)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def run_pipeline(
    monkeypatch, model, revise=False, evidence_error=False, message=None
):
    from agent import data_access, evidence

    async def lookup(ctx, names):
        if evidence_error:
            raise RuntimeError("broken lookup")
        rows = candidates()
        result = {}
        for name in names:
            if name == "burden_for_county":
                result[name] = [
                    t.model_dump(
                        exclude={
                            "km_to_safety_net",
                            "km_to_any_care",
                            "extra_km_if_uninsured",
                            "mean_pm",
                            "days_over_12",
                        }
                    )
                    for t in rows
                ]
            elif name == "access_for_county":
                result[name] = [
                    {"geo_id": t.geo_id, "km_to_safety_net": t.km_to_safety_net}
                    for t in rows
                ]
            elif name == "exposure_for_county":
                result[name] = []
            else:
                result[name] = [
                    {
                        "disease_weeks": 10,
                        "usable_disease_weeks": 0,
                        "shortage_area_count": 0,
                    }
                ]
        return result

    monkeypatch.setattr(evidence, "lookup_tools", lookup)
    monkeypatch.setattr(
        data_access,
        "auditor_rows",
        lambda county: (candidates(), demographics(candidates())),
    )
    if revise:
        import agent.audit as audit

        monkeypatch.setattr(
            audit,
            "evaluate_plan",
            lambda *args: AuditResult(
                verdict="REVISE",
                must_fix=["Choose different anchors"],
                disclosures=[],
                groups=[],
                min_stop_separation_km=1,
            ),
        )
    service = RuntimeContractSessionService()
    session = await service.create_session(app_name="agent", user_id="u")
    runner = Runner(app=create_app(model), session_service=service)
    events = [
        e
        async for e in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=message
                        if message is not None
                        else request().model_dump_json()
                    )
                ],
            ),
        )
    ]
    saved = await service.get_session(
        app_name="agent", user_id="u", session_id=session.id
    )
    return saved.state, events


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Hello", request().model_dump_json()])
async def test_runtime_session_events_keep_the_invocation_id(monkeypatch, message):
    _, events = await run_pipeline(monkeypatch, ScriptedModel(), message=message)
    assert events
    assert all(event.invocation_id for event in events)
    assert len({event.invocation_id for event in events}) == 1


@pytest.mark.asyncio
async def test_full_pipeline_and_isolated_auditor(monkeypatch):
    model = ScriptedModel()
    state, _events = await run_pipeline(monkeypatch, model)
    assert state["plan_status"] == "APPROVED"
    assert state["flyer_status"] == "PASS"
    assert state["revision_count"] == 0
    assert len(state["plan_history"]) == 1
    auditor_requests = [r for r in model.requests if "audit_plan_tool" in r.tools_dict]
    assert auditor_requests
    assert all("PRIVATE PLANNER REASONING" not in str(r) for r in auditor_requests)
    assert state["ev_burden"] and state["ev_access"] and state["ev_context"]


@pytest.mark.asyncio
async def test_final_response_includes_verified_centroids_even_if_model_omits_them(
    monkeypatch,
):
    state, events = await run_pipeline(monkeypatch, ScriptedModel())
    text = events[-1].content.parts[0].text
    assert state["plan_status"] == "APPROVED"
    assert "tract centroids" in text
    assert "confirmed clinic locations" in text
    assert "| Date | Anchor tract | Latitude | Longitude |" in text
    assert "| 2026-11-07 | 13121000000 | 33.700000 | -84.400000 |" in text
    assert "| 2026-11-28 | 13121000003 | 33.880000 | -84.400000 |" in text


@pytest.mark.asyncio
async def test_tool_verdict_wins_and_loop_terminates(monkeypatch):
    model = ScriptedModel()
    state, _ = await run_pipeline(monkeypatch, model, revise=True)
    assert state["plan_status"] == "NEEDS_HUMAN"
    assert state["revision_count"] == 2
    assert len(state["plan_history"]) == 3
    assert state["flyer_text"].startswith("PLAN NOT APPROVED")
    assert state["round_count"] == 3


@pytest.mark.asyncio
async def test_missing_tool_calls_cannot_reuse_approval(monkeypatch):
    state, _ = await run_pipeline(monkeypatch, ScriptedModel(skip_planner=True))
    assert state["plan_status"] == "NEEDS_HUMAN"
    assert state["round_count"] == 3


@pytest.mark.asyncio
async def test_flyer_rewrites_terminate_and_withhold_failed_handout(monkeypatch):
    state, events = await run_pipeline(monkeypatch, ScriptedModel(broken_flyer=True))
    assert state["flyer_attempts"] == 3
    assert state["flyer_status"] == "FAIL"
    assert "withheld" in events[-1].content.parts[0].text.lower()


@pytest.mark.asyncio
async def test_evidence_error_stops_before_models(monkeypatch):
    model = ScriptedModel()
    state, _ = await run_pipeline(monkeypatch, model, evidence_error=True)
    assert state["plan_status"] == "NEEDS_HUMAN"
    assert not state.get("plan_data")
    assert not model.requests


@pytest.mark.asyncio
async def test_timeout_produces_handoff_with_no_flyer(monkeypatch):
    import asyncio

    import agent.pipeline as pipeline

    original = pipeline.create_pipeline

    def short_pipeline(model):
        root = original(model)
        root.timeout_seconds = 0.001
        return root

    monkeypatch.setattr("agent.agent.create_pipeline", short_pipeline)
    from agent.evidence import EvidenceLookup

    original_lookup = EvidenceLookup._run_async_impl

    async def slow_lookup(self, ctx):
        await asyncio.sleep(0.02)
        async for e in original_lookup(self, ctx):
            yield e

    monkeypatch.setattr(EvidenceLookup, "_run_async_impl", slow_lookup)
    state, _ = await run_pipeline(monkeypatch, ScriptedModel())
    assert state["plan_status"] == "NEEDS_HUMAN"
    assert state["run_error"] == "Run timed out"
    assert state["flyer_text"].startswith("PLAN NOT APPROVED")


@pytest.mark.asyncio
async def test_demographics_never_return_to_planner_or_explainer(monkeypatch):
    import agent.audit as audit

    original = audit.evaluate_plan
    calls = 0

    def revise_once(*args):
        nonlocal calls
        calls += 1
        result = original(*args)
        if calls == 1:
            return result.model_copy(
                update={
                    "verdict": "REVISE",
                    "must_fix": ["Choose different anchors"],
                    "disclosures": ["SECRET DEMOGRAPHIC CONTEXT"],
                }
            )
        return result

    monkeypatch.setattr(audit, "evaluate_plan", revise_once)
    model = ScriptedModel()
    state, _ = await run_pipeline(monkeypatch, model)
    assert state["plan_status"] == "APPROVED"
    for r in model.requests:
        if "audit_plan_tool" not in r.tools_dict:
            assert "SECRET DEMOGRAPHIC CONTEXT" not in str(r)
            assert "need_matched_pct" not in str(r)


@pytest.mark.asyncio
async def test_failed_flyer_is_never_streamed_to_user(monkeypatch):
    state, events = await run_pipeline(monkeypatch, ScriptedModel(broken_flyer=True))
    assert state["flyer_status"] == "FAIL"
    assert not any(
        p.text and "bad flyer" in p.text
        for e in events
        if e.content
        for p in e.content.parts or []
    )
