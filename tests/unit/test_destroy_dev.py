"""Unit tests for scripts/destroy_dev.py. Nothing here talks to Google Cloud."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).parents[2]
_SCRIPT = _ROOT / "scripts" / "destroy_dev.py"
_spec = importlib.util.spec_from_file_location("destroy_dev", _SCRIPT)
destroy_dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(destroy_dev)

PROJECT = "qwiklabs-gcp-04-e3eaf2b4f0c3"
REGION = "us-central1"
PROD_RESOURCE = f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/3799"
DEV_RESOURCE = f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/6673"


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("curbside-dev", True),
        ("curbside-dev-501", True),
        ("curbside-dev-42", True),
        ("curbside", False),
        ("curbside-staging", False),
        ("other-dev-501", False),
        ("", False),
    ],
)
def test_is_sandbox_name(name: str, ok: bool) -> None:
    assert destroy_dev.is_sandbox_name(name) is ok


def test_refuse_production_display_name(tmp_path) -> None:
    meta = tmp_path / "deployment_metadata.dev.json"
    meta.write_text(json.dumps({"remote_agent_runtime_id": DEV_RESOURCE}))
    reason = destroy_dev.refuse_reason("curbside", meta)
    assert reason is not None
    assert "curbside" in reason


def test_refuse_production_metadata_filename(tmp_path) -> None:
    meta = tmp_path / "deployment_metadata.json"
    meta.write_text(json.dumps({"remote_agent_runtime_id": DEV_RESOURCE}))
    reason = destroy_dev.refuse_reason("curbside-dev-501", meta)
    assert reason is not None
    assert "production metadata" in reason


def test_refuse_when_dev_metadata_points_at_production(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    prod = tmp_path / "deployment_metadata.json"
    prod.write_text(json.dumps({"remote_agent_runtime_id": PROD_RESOURCE}))
    meta = tmp_path / "deployment_metadata.dev.json"
    meta.write_text(json.dumps({"remote_agent_runtime_id": PROD_RESOURCE}))
    reason = destroy_dev.refuse_reason("curbside-dev-501", meta)
    assert reason is not None
    assert "production agent" in reason


def test_allows_sandbox_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    prod = tmp_path / "deployment_metadata.json"
    prod.write_text(json.dumps({"remote_agent_runtime_id": PROD_RESOURCE}))
    meta = tmp_path / "deployment_metadata.dev.json"
    meta.write_text(json.dumps({"remote_agent_runtime_id": DEV_RESOURCE}))
    assert destroy_dev.refuse_reason("curbside-dev-501", meta) is None
    assert destroy_dev.refuse_reason("curbside-dev", meta) is None


def test_destroy_dev_does_not_call_client_when_refused(tmp_path) -> None:
    meta = tmp_path / "deployment_metadata.json"
    meta.write_text(json.dumps({"remote_agent_runtime_id": PROD_RESOURCE}))
    messages: list[str] = []
    called = []

    def factory(project, region):
        called.append((project, region))
        return SimpleNamespace(runtimes=SimpleNamespace())

    code = destroy_dev.destroy_dev(
        project=PROJECT,
        region=REGION,
        display_name="curbside",
        metadata_path=meta,
        assume_yes=True,
        client_factory=factory,
        out=messages.append,
    )
    assert code == 2
    assert called == []
    assert messages


def test_destroy_dev_delegates_to_destroy_agent(tmp_path, monkeypatch) -> None:
    meta = tmp_path / "deployment_metadata.dev.json"
    meta.write_text(json.dumps({"remote_agent_runtime_id": DEV_RESOURCE}))
    seen = {}

    def fake_destroy(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(destroy_dev.destroy_agent, "destroy", fake_destroy)
    code = destroy_dev.destroy_dev(
        project=PROJECT,
        region=REGION,
        display_name="curbside-dev-501",
        metadata_path=meta,
        assume_yes=True,
        client_factory=lambda *_: None,
        out=lambda _msg: None,
    )
    assert code == 0
    assert seen["display_name"] == "curbside-dev-501"
    assert seen["metadata_path"] == meta
    assert seen["assume_yes"] is True


def test_cli_defaults_to_sandbox_metadata(monkeypatch) -> None:
    seen = {}

    def fake_destroy_dev(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(destroy_dev, "destroy_dev", fake_destroy_dev)
    monkeypatch.setattr(destroy_dev, "default_sandbox_name", lambda: "curbside-dev-501")
    code = destroy_dev.main(
        ["--project", PROJECT, "--region", REGION, "--yes"]
    )
    assert code == 0
    assert seen["display_name"] == "curbside-dev-501"
    assert seen["metadata_path"] == Path("deployment_metadata.dev.json")
    assert seen["assume_yes"] is True
