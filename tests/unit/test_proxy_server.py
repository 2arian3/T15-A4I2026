"""Unit tests for agent/proxy_server.py (Leaflet UI & Reasoning Engine proxy)."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "proxy_server", ROOT / "agent" / "proxy_server.py"
)
assert SPEC is not None and SPEC.loader is not None
proxy_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy_server)


def test_normalize_reasoning_engine_url_converts_query_to_stream_query() -> None:
    raw = (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/1001206471385/"
        "locations/us-central1/reasoningEngines/3799547422611341312:query"
    )
    assert proxy_server.normalize_reasoning_engine_url(raw).endswith(":streamQuery")


def test_normalize_reasoning_engine_url_from_resource_id() -> None:
    resource_id = (
        "projects/1001206471385/locations/us-central1/"
        "reasoningEngines/3799547422611341312"
    )
    normalized = proxy_server.normalize_reasoning_engine_url(resource_id)
    assert normalized == (
        "https://us-central1-aiplatform.googleapis.com/v1/"
        "projects/1001206471385/locations/us-central1/"
        "reasoningEngines/3799547422611341312:streamQuery"
    )


def test_resolve_reasoning_engine_url_reads_deployment_metadata() -> None:
    url = proxy_server.resolve_reasoning_engine_url()
    assert "reasoningEngines/3799547422611341312:streamQuery" in url


def test_parse_agent_response_text_handles_newline_delimited_stream() -> None:
    event1 = {
        "content": {
            "role": "model",
            "parts": [
                {
                    "text": json.dumps(
                        {
                            "selected_tracts": [
                                {
                                    "saturday_number": 1,
                                    "geo_id": "13121011635",
                                    "latitude": 34.0393,
                                    "longitude": -84.1293,
                                    "selection_reason": "Stop 1 high uninsured gap",
                                },
                                {
                                    "saturday_number": 2,
                                    "geo_id": "13121010130",
                                    "latitude": 33.9418,
                                    "longitude": -84.3553,
                                    "selection_reason": "Stop 2 high transit barrier",
                                },
                            ]
                        }
                    )
                }
            ],
        },
        "actions": {
            "state_delta": {
                "plan_status": {
                    "passed": True,
                    "svi_Guardrail_Triggered": False,
                }
            }
        },
    }
    event2 = {
        "content": {
            "role": "model",
            "parts": [{"text": "Free Saturday Mobile Clinic Flyer for Fulton County."}],
        },
        "actions": {
            "state_delta": {
                "flyer": "Free Saturday Mobile Clinic Flyer for Fulton County."
            }
        },
    }
    stream_text = json.dumps(event1) + "\n" + json.dumps(event2) + "\n"
    parsed = proxy_server.parse_agent_response_text(stream_text)
    assert isinstance(parsed, list)
    assert len(parsed) == 2

    sites = proxy_server.extract_sites(parsed)
    assert len(sites) == 2
    assert sites[0]["geo_id"] == "13121011635"
    assert sites[0]["lat"] == 34.0393
    assert sites[1]["saturday_number"] == 2

    lat, lng = proxy_server.extract_lat_lng(parsed)
    assert (lat, lng) == (34.0393, -84.1293)

    summary = proxy_server.extract_summary(parsed)
    assert "Free Saturday Mobile Clinic Flyer" in summary

    audit = proxy_server.extract_audit(parsed)
    assert audit == {"passed": True, "svi_Guardrail_Triggered": False}


def test_tract_lookup_from_fulton_tracts_csv() -> None:
    coords = proxy_server.load_tract_coordinates()
    assert "13121011635" in coords
    lat, lng = coords["13121011635"]
    assert 33.0 < lat < 34.5
    assert -85.0 < lng < -84.0


def test_resolve_static_asset_prevents_traversal() -> None:
    index_asset = proxy_server.resolve_static_asset("index.html")
    assert index_asset is not None and index_asset.name == "index.html"
    assert proxy_server.resolve_static_asset("../pyproject.toml") is None
