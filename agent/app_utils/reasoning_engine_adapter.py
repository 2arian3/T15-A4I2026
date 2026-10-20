# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Serve the reasoning_engine ``{class_method, input}`` contract over HTTP.

Exists to guarantee support for the Google Cloud console playground and Gemini
(via ADK registration), which both invoke the engine through this contract.
Agent Runtime forwards calls to ``/api/reasoning_engine`` (sync) and
``/api/stream_reasoning_engine`` (streaming); dispatch is limited to the
:class:`AdkApp` ``register_operations()`` methods so the wire output matches an
agent packaged for Agent Runtime.

``:query`` JSON uses ``classMethod``. The container body and ``agents-cli`` use
``class_method``. A call that omits both used to raise ``KeyError``, which
Agent Runtime reports as HTTP 400 ``Internal Server Error``.
"""

import inspect
import json
from collections.abc import Callable
from typing import Any

from agentplatform.frameworks.adk import AdkApp
from fastapi import FastAPI, HTTPException, Request, encoders, responses
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from agent.app_utils import services

RuntimeFactory = Callable[[], tuple[Any, set[str], set[str]]]


def _no_op_instrumentor_builder(project_id: str) -> None:
    """No-op so set_up() keeps the startup instrumentor and generate_content spans."""
    return None


def _request_parts(body: object) -> tuple[str, dict]:
    """Return ``(class_method, input)`` for a reasoning_engine request.

    An agent turn with no method name runs ``async_stream_query``. Anything
    else without a method is a 400 that names the missing field, not a 500.
    """
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400, detail="Request body must be a JSON object"
        )
    method = body.get("class_method") or body.get("classMethod")
    raw_input = body.get("input")
    if raw_input is None:
        kwargs: dict = {}
    elif not isinstance(raw_input, dict):
        raise HTTPException(status_code=400, detail="input must be a JSON object")
    else:
        kwargs = raw_input
    if method:
        return str(method), kwargs
    if "message" not in kwargs:
        raise HTTPException(
            status_code=400,
            detail=(
                "class_method is required. Set class_method or classMethod "
                "to a registered operation, for example async_create_session "
                "or async_stream_query."
            ),
        )
    if not kwargs.get("user_id"):
        raise HTTPException(
            status_code=400,
            detail="user_id is required to run the agent when class_method is omitted",
        )
    return "async_stream_query", kwargs


def _build_adk_runtime() -> tuple[AdkApp, set[str], set[str]]:
    """Build the process AdkApp and the methods Agent Runtime may call."""
    from agent.agent import app as adk_app

    # Reuse the process-wide services so sessions created here are visible to
    # the adk_api routes, and vice versa (see services.py).
    runtime = AdkApp(
        app=adk_app,
        session_service_builder=services.get_session_service,
        artifact_service_builder=services.get_artifact_service,
        instrumentor_builder=_no_op_instrumentor_builder,
    )
    runtime.set_up()
    operations = runtime.register_operations()
    streaming_methods = set(operations.get("stream", [])) | set(
        operations.get("async_stream", [])
    )
    sync_methods = set(operations.get("", [])) | set(operations.get("async", []))
    return runtime, streaming_methods, sync_methods


def attach_reasoning_engine_routes(
    app: FastAPI, *, runtime_factory: RuntimeFactory | None = None
) -> None:
    """Register reasoning_engine routes that dispatch to an AdkApp."""
    factory = runtime_factory or _build_adk_runtime
    cached: tuple[Any, set[str], set[str]] | None = None

    def get_runtime() -> tuple[Any, set[str], set[str]]:
        nonlocal cached
        if cached is None:
            cached = factory()
        return cached

    def resolve_method(class_method: str, *, streaming: bool):
        runtime, streaming_methods, sync_methods = get_runtime()
        allowed = streaming_methods if streaming else streaming_methods | sync_methods
        if class_method not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported reasoning_engine method: {class_method!r}",
            )
        return getattr(runtime, class_method), class_method in streaming_methods

    async def read_call(request: Request, *, streaming: bool):
        try:
            body = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
        class_method, kwargs = _request_parts(body)
        return resolve_method(class_method, streaming=streaming), kwargs

    async def invoke(method, kwargs):
        if inspect.iscoroutinefunction(method):
            return await method(**kwargs)
        if inspect.isasyncgenfunction(method):
            return method(**kwargs)
        return await run_in_threadpool(method, **kwargs)

    async def collect(value):
        if hasattr(value, "__aiter__"):
            return [event async for event in value]
        if inspect.isgenerator(value):
            return [event async for event in iterate_in_threadpool(value)]
        return value

    @app.post("/api/stream_reasoning_engine")
    async def stream_query(request: Request) -> responses.StreamingResponse:
        (method, _is_streaming), kwargs = await read_call(request, streaming=True)
        stream = await invoke(method, kwargs)

        async def generator():
            if hasattr(stream, "__aiter__"):
                async for event in stream:
                    yield json.dumps(encoders.jsonable_encoder(event)) + "\n"
            else:
                async for event in iterate_in_threadpool(stream):
                    yield json.dumps(encoders.jsonable_encoder(event)) + "\n"

        return responses.StreamingResponse(
            content=generator(), media_type="application/json"
        )

    @app.post("/api/reasoning_engine")
    async def query(request: Request) -> responses.JSONResponse:
        (method, is_streaming), kwargs = await read_call(request, streaming=False)
        output = await invoke(method, kwargs)
        if is_streaming:
            output = await collect(output)
        return responses.JSONResponse(
            content=encoders.jsonable_encoder({"output": output})
        )
