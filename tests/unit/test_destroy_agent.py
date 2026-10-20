"""Unit tests for scripts/destroy_agent.py, using a fake Agent Platform client."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "destroy_agent.py"
_spec = importlib.util.spec_from_file_location("destroy_agent", _SCRIPT)
destroy_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(destroy_agent)

PROJECT = "qwiklabs-gcp-04-e3eaf2b4f0c3"
REGION = "us-central1"
RESOURCE = f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/123"


class FakeRuntimes:
    def __init__(self, agents=()):
        self._agents = list(agents)
        self.deleted: list[tuple[str, bool]] = []
        self.list_filters: list[str] = []

    def list(self, config):
        self.list_filters.append(config["filter"])
        return iter(self._agents)

    def delete(self, *, name, force=None):
        self.deleted.append((name, force))


def fake_agent(name: str, display_name: str):
    return SimpleNamespace(
        api_resource=SimpleNamespace(name=name, display_name=display_name)
    )


@pytest.fixture
def runtimes():
    return FakeRuntimes()


def run(tmp_path, runtimes, *, metadata=None, yes=True, typed="", **kw):
    path = tmp_path / "deployment_metadata.json"
    if metadata is not None:
        path.write_text(json.dumps(metadata))
    messages: list[str] = []
    calls = []

    def factory(project, region):
        calls.append((project, region))
        return SimpleNamespace(runtimes=runtimes)

    code = destroy_agent.destroy(
        project=kw.get("project", PROJECT),
        region=kw.get("region", REGION),
        display_name="curbside",
        metadata_path=path,
        assume_yes=yes,
        client_factory=factory,
        input_fn=lambda _prompt: typed,
        out=messages.append,
    )
    return code, path, messages, calls


# --- metadata helpers ------------------------------------------------------


def test_read_metadata_id(tmp_path) -> None:
    path = tmp_path / "m.json"
    assert destroy_agent.read_metadata_id(path) is None  # missing
    path.write_text("not json")
    assert destroy_agent.read_metadata_id(path) is None
    path.write_text(json.dumps({"remote_agent_runtime_id": None}))
    assert destroy_agent.read_metadata_id(path) is None
    path.write_text(json.dumps({"remote_agent_runtime_id": RESOURCE}))
    assert destroy_agent.read_metadata_id(path) == RESOURCE


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        (RESOURCE, True),
        (f"projects/987654/locations/{REGION}/reasoningEngines/1", True),
        (f"projects/other-project/locations/{REGION}/reasoningEngines/1", False),
        (f"projects/{PROJECT}/locations/us-east1/reasoningEngines/1", False),
        (f"projects/{PROJECT}/locations/{REGION}/somethingElse/1", False),
        ("reasoningEngines/1", False),
    ],
)
def test_resource_matches(name, ok) -> None:
    assert destroy_agent.resource_matches(name, PROJECT, REGION) is ok


# --- destroy flow ----------------------------------------------------------


def test_deletes_agent_from_metadata(tmp_path, runtimes) -> None:
    code, path, _, calls = run(
        tmp_path, runtimes, metadata={"remote_agent_runtime_id": RESOURCE}
    )
    assert code == 0
    assert calls == [(PROJECT, REGION)]  # client is regional, never "global"
    assert runtimes.deleted == [(RESOURCE, True)]
    assert runtimes.list_filters == []  # no lookup needed
    assert json.loads(path.read_text())["remote_agent_runtime_id"] is None


def test_falls_back_to_display_name(tmp_path) -> None:
    runtimes = FakeRuntimes(
        [fake_agent(RESOURCE, "curbside"), fake_agent("projects/p/x", "other")]
    )
    code, _, _, _ = run(tmp_path, runtimes)
    assert code == 0
    assert runtimes.list_filters == ['display_name="curbside"']
    assert runtimes.deleted == [(RESOURCE, True)]


def test_nothing_to_delete_is_success(tmp_path, runtimes) -> None:
    code, _, messages, _ = run(tmp_path, runtimes)
    assert code == 0
    assert runtimes.deleted == []
    assert any("No Agent Runtime agent" in m for m in messages)


def test_ambiguous_display_name_refuses(tmp_path) -> None:
    runtimes = FakeRuntimes(
        [fake_agent(RESOURCE, "curbside"), fake_agent(RESOURCE + "4", "curbside")]
    )
    code, _, _, _ = run(tmp_path, runtimes)
    assert code == 2
    assert runtimes.deleted == []


def test_metadata_for_another_project_refuses(tmp_path, runtimes) -> None:
    other = f"projects/someone-else/locations/{REGION}/reasoningEngines/9"
    code, path, _, _ = run(
        tmp_path, runtimes, metadata={"remote_agent_runtime_id": other}
    )
    assert code == 2
    assert runtimes.deleted == []
    assert json.loads(path.read_text())["remote_agent_runtime_id"] == other


@pytest.mark.parametrize(
    ("typed", "deleted"), [("curbside", True), ("nope", False), ("", False)]
)
def test_confirmation(tmp_path, runtimes, typed, deleted) -> None:
    code, _, _, _ = run(
        tmp_path,
        runtimes,
        metadata={"remote_agent_runtime_id": RESOURCE},
        yes=False,
        typed=typed,
    )
    assert bool(runtimes.deleted) is deleted
    assert code == (0 if deleted else 1)


def test_confirm_handles_eof() -> None:
    def raise_eof(_prompt):
        raise EOFError

    assert destroy_agent.confirm("curbside", raise_eof) is False


def test_cli_parses_arguments(tmp_path, monkeypatch) -> None:
    seen = {}

    def fake_destroy(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(destroy_agent, "destroy", fake_destroy)
    code = destroy_agent.main(
        ["--project", PROJECT, "--region", REGION, "--name", "curbside", "--yes"]
    )
    assert code == 0
    assert seen["project"] == PROJECT
    assert seen["region"] == REGION
    assert seen["assume_yes"] is True
    assert seen["metadata_path"] == Path("deployment_metadata.json")
