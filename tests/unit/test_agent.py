"""Unit tests for the agent's configuration (no model calls)."""

import importlib

import pytest
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.tools import AgentTool

import agent.agent as agent_module
from agent.tools import (
    audit_plan_tool,
    describe_table_resolution,
    rank_and_select_siting_plan,
)


def _pipeline_agents(module):
    return list(module.siting_pipeline.sub_agents)


def _modeled_agents(module):
    return [module.root_agent, *_pipeline_agents(module)]


def test_root_agent_identity() -> None:
    root = agent_module.root_agent
    assert isinstance(root, Agent)
    assert root.name == "curbside"
    assert root.description
    assert root.sub_agents == []
    assert len(root.tools) == 1
    tool = root.tools[0]
    assert isinstance(tool, AgentTool)
    assert tool.agent is agent_module.siting_pipeline
    assert tool.skip_summarization is True
    assert [agent.name for agent in _pipeline_agents(agent_module)] == [
        "planner",
        "equity_auditor",
        "explainer",
    ]


def test_root_agent_name_matches_manifest() -> None:
    # telemetry and agents-cli read the name from the manifest; keep them equal.
    from pathlib import Path

    manifest = Path(__file__).parents[2] / "agents-cli-manifest.yaml"
    assert 'root_agent_name: "curbside"' in manifest.read_text()


def test_planner_owns_the_siting_tool() -> None:
    planner = agent_module.planner
    assert describe_table_resolution in planner.tools
    assert rank_and_select_siting_plan in planner.tools
    assert rank_and_select_siting_plan not in agent_module.equity_auditor.tools
    assert agent_module.explainer.tools == []


def test_auditor_is_isolated_and_explainer_writes_flyer() -> None:
    assert audit_plan_tool in agent_module.equity_auditor.tools
    assert agent_module.equity_auditor.include_contents == "none"
    assert agent_module.explainer.include_contents == "none"
    assert agent_module.explainer.output_key == "flyer"
    assert "{plan_data?}" in agent_module.explainer.instruction
    assert "{plan_status?}" in agent_module.explainer.instruction


def test_instruction_carries_the_ground_rules() -> None:
    instruction = agent_module.planner.instruction
    assert "describe_table_resolution" in instruction
    assert "race" in instruction.lower()
    assert "modeled" in instruction
    assert "plan_data" in instruction


def test_coordinator_answers_follow_ups_without_replanning() -> None:
    instruction = agent_module.root_agent.instruction
    assert "siting_pipeline" in instruction
    assert "Do not call" in instruction
    assert "siting_pipeline again" in instruction
    assert "{plan_data?}" in instruction
    assert "{plan_status?}" in instruction
    assert "{flyer?}" in instruction


def test_app_wraps_root_agent() -> None:
    assert isinstance(agent_module.app, App)
    assert agent_module.app.root_agent is agent_module.root_agent
    assert agent_module.app.name == "agent"


@pytest.fixture
def reload_agent(monkeypatch):
    """Reloads agent.agent under a patched environment, then restores it."""

    def _reload():
        return importlib.reload(agent_module)

    yield _reload
    monkeypatch.undo()
    importlib.reload(agent_module)


def test_default_model(monkeypatch, reload_agent) -> None:
    monkeypatch.delenv("CURBSIDE_MODEL", raising=False)
    module = reload_agent()
    assert module.MODEL == module.DEFAULT_MODEL == "gemini-3.8-flash"
    for sub_agent in _modeled_agents(module):
        assert sub_agent.model.model == "gemini-3.8-flash"


def test_model_override_from_env(monkeypatch, reload_agent) -> None:
    monkeypatch.setenv("CURBSIDE_MODEL", "gemini-test-model")
    module = reload_agent()
    for sub_agent in _modeled_agents(module):
        assert sub_agent.model.model == "gemini-test-model"


def test_model_retries_transient_errors() -> None:
    for sub_agent in _modeled_agents(agent_module):
        model = sub_agent.model
        assert model.retry_options is not None
        assert model.retry_options.attempts == 3
