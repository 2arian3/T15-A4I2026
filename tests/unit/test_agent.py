"""Unit tests for the agent's configuration (no model calls)."""

import importlib

import pytest
from google.adk.apps import App

import agent.agent as agent_module
from agent import prompts
from agent.planner import build_clinic_plan


def test_root_agent_identity() -> None:
    root = agent_module.root_agent
    assert root.name == "curbside"
    assert root.description


def test_root_agent_name_matches_manifest() -> None:
    # telemetry and agents-cli read the name from the manifest; keep them equal.
    from pathlib import Path

    manifest = Path(__file__).parents[2] / "agents-cli-manifest.yaml"
    assert 'root_agent_name: "curbside"' in manifest.read_text()


def test_root_agent_has_our_tool() -> None:
    assert build_clinic_plan in agent_module.root_agent.find_agent("planner").tools


def test_instruction_carries_the_ground_rules() -> None:
    instruction = prompts.PLANNER
    assert "build_clinic_plan" in instruction
    assert "race" in instruction.lower()
    assert "modeled" in instruction


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
    assert module.root_agent.find_agent("planner").model.model == "gemini-3.8-flash"


def test_model_override_from_env(monkeypatch, reload_agent) -> None:
    monkeypatch.setenv("CURBSIDE_MODEL", "gemini-test-model")
    module = reload_agent()
    assert module.root_agent.find_agent("planner").model.model == "gemini-test-model"


def test_model_retries_transient_errors() -> None:
    model = agent_module.root_agent.find_agent("planner").model
    assert model.retry_options is not None
    assert model.retry_options.attempts == 3
