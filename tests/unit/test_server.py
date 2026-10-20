"""Unit tests for the HTTP surface the container serves on Agent Engine.

Curbside is a plain ADK agent: no A2A routes, no A2A dependency. Agent Engine
reaches it through the reasoning_engine contract (:query / :streamQuery).
"""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def route_paths() -> set[str]:
    from agent.fast_api_app import app

    return {getattr(route, "path", "") for route in app.routes}


def test_no_a2a_routes(route_paths: set[str]) -> None:
    assert not [p for p in route_paths if "a2a" in p.lower() or "agent-card" in p]


@pytest.mark.parametrize(
    "path",
    [
        "/api/reasoning_engine",  # Agent Engine :query
        "/api/stream_reasoning_engine",  # Agent Engine :streamQuery
        "/run_sse",  # ADK API
        "/list-apps",
    ],
)
def test_serves_adk_and_agent_engine_routes(route_paths: set[str], path: str) -> None:
    assert path in route_paths


def test_no_a2a_module_or_dependency() -> None:
    assert not (ROOT / "agent" / "app_utils" / "a2a.py").exists()
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    assert not [d for d in deps if d.lower().startswith("a2a")]


def test_manifest_marks_project_as_not_a2a() -> None:
    manifest = (ROOT / "agents-cli-manifest.yaml").read_text()
    assert "is_a2a: false" in manifest
    assert 'deployment_target: "agent_runtime"' in manifest
