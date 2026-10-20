"""MCP wire parsing, explicit projections, county traps, and no partial evidence."""

import pytest
from test_outreach import candidates

from agent.evidence import merge_evidence, parse_mcp_rows


def test_mcp_rows_and_errors():
    assert parse_mcp_rows(
        {"content": [{"type": "text", "text": '[{"geo_id":"13121000001"}]'}]}
    ) == [{"geo_id": "13121000001"}]
    assert parse_mcp_rows({"structuredContent": {"rows": []}}) == []
    for payload in [
        {"isError": True, "content": []},
        {"error": "denied"},
        {"content": [{"type": "text", "text": "bad json"}]},
    ]:
        with pytest.raises((ValueError, RuntimeError)):
            parse_mcp_rows(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "isError": True,
            "content": [
                {"type": "text", "text": "Unrecognized name: pct_lack_transport"}
            ],
        },
        {
            "is_error": True,
            "content": [
                {"type": "text", "text": "Unrecognized name: pct_lack_transport"}
            ],
        },
        {"error": {"message": "Unrecognized name: pct_lack_transport"}},
    ],
)
def test_mcp_error_preserves_upstream_diagnostic(payload):
    with pytest.raises(RuntimeError, match="Unrecognized name: pct_lack_transport"):
        parse_mcp_rows(payload)


def test_unscoped_shortage_rows_do_not_claim_zero_designations():
    brief, limits = merge_evidence(
        "13121",
        [candidates()[0].model_dump()],
        [],
        [],
        [{"shortage_area_count": 0, "shortage_area_unscoped_count": 2}],
    )
    assert any(
        "county" in c.text and "2" in c.text
        for c in limits
        if c.source_table == "shortage_areas"
    )
    assert not any("designated shortage areas" in c.text for c in brief.context)
    assert not any("No designated" in c.text for c in limits)


def test_mcp_error_redacts_credentials_and_limits_size():
    payload = {
        "error": "denied Authorization: Bearer secret-token https://example.test?key=private-key "
        + "x" * 5000
    }
    with pytest.raises(RuntimeError) as exc:
        parse_mcp_rows(payload)
    assert "denied" in str(exc.value)
    assert "secret-token" not in str(exc.value)
    assert "private-key" not in str(exc.value)
    assert len(str(exc.value)) < 1600


def test_missing_transport_exposure_and_empty_shortage_are_findings():
    burden = [
        t.model_dump(
            exclude={
                "km_to_safety_net",
                "km_to_any_care",
                "extra_km_if_uninsured",
                "mean_pm",
                "days_over_12",
            }
        )
        for t in candidates()
    ]
    access = [
        {"geo_id": t.geo_id, "km_to_safety_net": t.km_to_safety_net}
        for t in candidates()
    ]
    brief, limits = merge_evidence(
        "13121",
        burden,
        access,
        [],
        [{"disease_weeks": 10, "usable_disease_weeks": 0, "shortage_area_count": 0}],
    )
    assert len(brief.candidates) == 8  # LEFT join keeps every burden tract
    assert any("pct_lack_transport" in c.text for c in limits)
    assert any("no usable" in c.text.lower() for c in limits)
    assert any("no designated" in c.text.lower() for c in limits)
    assert any(c.scale == "12 KM GRID" for c in limits)


def test_evidence_rejects_demographics_and_mixed_counties():
    for row in [
        {**candidates()[0].model_dump(), "race": "secret"},
        {**candidates()[0].model_dump(), "geo_id": "48201000001"},
    ]:
        with pytest.raises(ValueError):
            merge_evidence("13121", [row], [], [], [{}])


def test_truncated_toolbox_result_is_rejected():
    rows = [candidates()[0].model_dump()]
    with pytest.raises(ValueError, match="truncated"):
        merge_evidence(
            "13121",
            rows,
            [],
            [],
            [
                {
                    "tract_count": 100,
                    "disease_weeks": 0,
                    "usable_disease_weeks": 0,
                    "shortage_area_count": 0,
                }
            ],
        )


@pytest.mark.asyncio
async def test_adk_mcp_client_consumes_named_tools_over_real_transport(monkeypatch):
    import sys
    from pathlib import Path

    from google.adk.dependencies._mcp import StdioServerParameters
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from google.genai import types
    from test_outreach import request

    from agent.evidence import TOOL_NAMES, EvidenceLookup

    fixture = Path(__file__).parents[1] / "fixtures" / "evidence_mcp_server.py"

    def local_toolset(**kwargs):
        kwargs["connection_params"] = StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable, args=[str(fixture)]
            )
        )
        return McpToolset(**kwargs)

    monkeypatch.setattr(
        "google.adk.tools.mcp_tool.mcp_toolset.McpToolset", local_toolset
    )
    monkeypatch.setenv("TOOLBOX_URL", "http://localhost:5000")
    service = InMemorySessionService()
    session = await service.create_session(
        app_name="test",
        user_id="u",
        state={"request": request().model_dump(mode="json")},
    )
    runner = Runner(
        agent=EvidenceLookup(name="wire", tool_names=TOOL_NAMES, state_key="ev_wire"),
        session_service=service,
        app_name="test",
    )
    events = [
        e
        async for e in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="lookup")]),
        )
    ]
    saved = await service.get_session(
        app_name="test", user_id="u", session_id=session.id
    )
    assert events
    assert set(saved.state["ev_wire"]) == set(TOOL_NAMES)
    assert saved.state["ev_wire"]["burden_for_county"][0]["geo_id"] == "13121000001"
