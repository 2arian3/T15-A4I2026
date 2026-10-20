"""Contract tests for /api/reasoning_engine without starting the agent."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def async_create_session(self, **kwargs):
        self.calls.append(("async_create_session", kwargs))
        return {"id": "sess", "user_id": kwargs["user_id"]}

    async def async_stream_query(self, **kwargs):
        self.calls.append(("async_stream_query", kwargs))
        yield {"content": {"parts": [{"text": "hello"}]}}

    def stream_query(self, **kwargs):
        self.calls.append(("stream_query", kwargs))
        yield {"content": {"parts": [{"text": "sync-stream"}]}}


def _client() -> tuple[TestClient, _Runtime]:
    runtime = _Runtime()

    def factory():
        return (
            runtime,
            {"async_stream_query", "stream_query"},
            {"async_create_session"},
        )

    app = FastAPI()
    attach_reasoning_engine_routes(app, runtime_factory=factory)
    return TestClient(app), runtime


def test_sync_query_accepts_snake_and_camel_method() -> None:
    client, runtime = _client()
    for body in (
        {"class_method": "async_create_session", "input": {"user_id": "snake"}},
        {"classMethod": "async_create_session", "input": {"user_id": "camel"}},
    ):
        response = client.post("/api/reasoning_engine", json=body)
        assert response.status_code == 200
        assert response.json()["output"]["user_id"] in {"snake", "camel"}
    assert [call[0] for call in runtime.calls] == [
        "async_create_session",
        "async_create_session",
    ]


def test_missing_method_is_400_not_500() -> None:
    client, runtime = _client()
    for body in ({}, {"input": {"user_id": "only-user"}}):
        response = client.post("/api/reasoning_engine", json=body)
        assert response.status_code == 400
        assert "class_method is required" in response.json()["detail"]
    assert runtime.calls == []


def test_agent_turn_without_method_runs_async_stream_query() -> None:
    client, runtime = _client()
    response = client.post(
        "/api/reasoning_engine",
        json={"input": {"user_id": "u1", "message": "Hi"}},
    )
    assert response.status_code == 200
    assert response.json()["output"] == [{"content": {"parts": [{"text": "hello"}]}}]
    assert runtime.calls == [("async_stream_query", {"user_id": "u1", "message": "Hi"})]


def test_explicit_stream_method_on_sync_route_collects_events() -> None:
    client, _runtime = _client()
    response = client.post(
        "/api/reasoning_engine",
        json={
            "classMethod": "async_stream_query",
            "input": {"user_id": "u1", "message": "Hi"},
        },
    )
    assert response.status_code == 200
    assert response.json()["output"][0]["content"]["parts"][0]["text"] == "hello"


def test_agent_turn_without_user_id_is_400() -> None:
    client, runtime = _client()
    response = client.post("/api/reasoning_engine", json={"input": {"message": "Hi"}})
    assert response.status_code == 400
    assert "user_id" in response.json()["detail"]
    assert runtime.calls == []


def test_unknown_method_is_404() -> None:
    client, _runtime = _client()
    response = client.post(
        "/api/reasoning_engine",
        json={"class_method": "query", "input": {"user_id": "u1"}},
    )
    assert response.status_code == 404
    assert "query" in response.json()["detail"]


def test_stream_route_emits_ndjson() -> None:
    client, runtime = _client()
    response = client.post(
        "/api/stream_reasoning_engine",
        json={
            "class_method": "stream_query",
            "input": {"user_id": "u1", "message": "Hi"},
        },
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events == [{"content": {"parts": [{"text": "sync-stream"}]}}]
    assert runtime.calls[0][0] == "stream_query"


def test_stream_route_defaults_a_bare_agent_turn() -> None:
    client, runtime = _client()
    response = client.post(
        "/api/stream_reasoning_engine",
        json={"input": {"user_id": "u1", "message": "Hi"}},
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[0]["content"]["parts"][0]["text"] == "hello"
    assert runtime.calls[0][0] == "async_stream_query"
