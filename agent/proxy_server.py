"""HTTP proxy and static UI server for the Curbside Leaflet frontend.

Serves the static frontend from ``frontend/`` at ``/`` and forwards ``POST /agent``
requests to the deployed Vertex AI Agent Runtime (``:streamQuery``) using
Application Default Credentials (Cloud Run service account or local ADC), with a
local ``gcloud auth print-access-token`` fallback.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import mimetypes
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:
    from google.auth import default as google_auth_default
    from google.auth.transport.requests import Request as GoogleAuthRequest
except ImportError:  # pragma: no cover
    google_auth_default = None  # type: ignore[assignment]
    GoogleAuthRequest = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "frontend"
METADATA_PATH = REPO_ROOT / "deployment_metadata.json"
TRACTS_CSV_PATH = FRONTEND_DIR / "fulton_tracts.csv"

FALLBACK_REASONING_ENGINE_ID = (
    "projects/1001206471385/locations/us-central1/reasoningEngines/3799547422611341312"
)

# Avoid stale ADC files from a personal account if the file path no longer exists.
if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and not os.path.exists(
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
):
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


def normalize_reasoning_engine_url(url: str) -> str:
    """Ensure a Reasoning Engine URL or resource name targets ``:streamQuery``."""
    cleaned = url.strip()
    if not cleaned:
        cleaned = FALLBACK_REASONING_ENGINE_ID

    if cleaned.startswith("projects/"):
        match = re.search(r"locations/([^/]+)/", cleaned)
        region = match.group(1) if match else os.environ.get("REGION", "us-central1")
        cleaned = f"https://{region}-aiplatform.googleapis.com/v1/{cleaned}"

    if cleaned.endswith(":query"):
        return cleaned[: -len(":query")] + ":streamQuery"
    if not cleaned.endswith(":streamQuery"):
        return cleaned.rstrip("/") + ":streamQuery"
    return cleaned


def resolve_reasoning_engine_url(metadata_path: Path = METADATA_PATH) -> str:
    """Resolve the active Reasoning Engine URL from env or deployment_metadata.json."""
    env_url = os.environ.get("REASONING_ENGINE_URL", "").strip()
    if env_url:
        return normalize_reasoning_engine_url(env_url)

    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            resource_id = metadata.get("remote_agent_runtime_id", "").strip()
            if resource_id:
                return normalize_reasoning_engine_url(resource_id)
        except (OSError, ValueError):
            pass

    return normalize_reasoning_engine_url(FALLBACK_REASONING_ENGINE_ID)


REASONING_ENGINE_URL = resolve_reasoning_engine_url()


@lru_cache(maxsize=1)
def load_tract_coordinates(
    csv_path: Path = TRACTS_CSV_PATH,
) -> dict[str, tuple[float, float]]:
    """Load Fulton County census tract (latitude, longitude) by 11-digit geo_id."""
    coords: dict[str, tuple[float, float]] = {}
    if not csv_path.is_file():
        return coords
    try:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                geo_id = (row.get("geo_id") or "").strip()
                try:
                    lat = float(row.get("latitude") or "")
                    lng = float(row.get("longitude") or "")
                except ValueError:
                    continue
                if geo_id:
                    coords[geo_id] = (lat, lng)
    except OSError:
        return {}
    return coords


def get_google_access_token() -> str:
    """Obtain an OAuth2 access token via ADC first, then gcloud CLI fallback."""
    if google_auth_default is not None and GoogleAuthRequest is not None:
        try:
            credentials, _ = google_auth_default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(GoogleAuthRequest())
            if credentials.token:
                return str(credentials.token)
        except Exception:
            pass

    executable = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if executable:
        cmd = [executable, "auth", "print-access-token"]
        account = os.environ.get("GCLOUD_ACCOUNT", "").strip()
        if account:
            cmd.extend(["--account", account])
        try:
            token = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                timeout=15,
            ).strip()
            if token:
                return token
        except Exception:
            pass

    raise RuntimeError(
        "Could not obtain a Google Cloud access token. "
        "On Cloud Run, ensure the service account is attached; "
        "locally, run 'make auth' or 'gcloud auth application-default login'."
    )


IGNORED_WALK_KEYS = {
    "ev_burden",
    "ev_access",
    "ev_context",
    "burden_for_county",
    "access_for_county",
    "exposure_for_county",
    "candidate_pool",
    "skipped_adjacent_high_need_tracts",
    "candidates",
}


def walk_values(value: Any, key_names: set[str]):
    """Yield values for any matching key name in nested dicts/lists."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in key_names:
                yield child
            if key in IGNORED_WALK_KEYS:
                continue
            yield from walk_values(child, key_names)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item, key_names)


def build_default_plan_request(
    county_fips: str = "13121",
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Build a valid PlanRequest dict for the 4 Saturdays of next calendar month."""
    ref = today or dt.date.today()
    if ref.month == 12:
        year, month = ref.year + 1, 1
    else:
        year, month = ref.year, ref.month + 1

    month_str = f"{year:04d}-{month:02d}"
    saturdays: list[str] = []
    current = dt.date(year, month, 1)
    while current.month == month and len(saturdays) < 4:
        if current.weekday() == 5:
            saturdays.append(current.isoformat())
        current += dt.timedelta(days=1)

    return {
        "county_fips": county_fips,
        "month": month_str,
        "saturdays": saturdays,
    }


def coerce_prompt_to_plan_request(
    prompt: str,
    today: dt.date | None = None,
) -> str:
    """Ensure the prompt sent to OutreachPipeline is a valid PlanRequest JSON string."""
    parsed = _try_parse_json_dict(prompt)
    if (
        isinstance(parsed, dict)
        and "county_fips" in parsed
        and "month" in parsed
        and isinstance(parsed.get("saturdays"), list)
        and len(parsed["saturdays"]) == 4
    ):
        return json.dumps(parsed)

    fips_match = re.search(r"\b(\d{5})\b", prompt)
    county_fips = fips_match.group(1) if fips_match else "13121"

    month_match = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", prompt)
    if month_match:
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        saturdays: list[str] = []
        current = dt.date(year, month, 1)
        while current.month == month and len(saturdays) < 4:
            if current.weekday() == 5:
                saturdays.append(current.isoformat())
            current += dt.timedelta(days=1)
        return json.dumps(
            {
                "county_fips": county_fips,
                "month": f"{year:04d}-{month:02d}",
                "saturdays": saturdays,
            }
        )

    return json.dumps(build_default_plan_request(county_fips=county_fips, today=today))


def parse_agent_response_text(raw_text: str) -> Any:
    """Parse either a single JSON document or newline-delimited JSON events."""
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("Empty response from Reasoning Engine.")

    try:
        return json.loads(stripped)
    except ValueError:
        pass

    events: list[Any] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))

    if not events:
        raise ValueError("No valid JSON events found in Reasoning Engine stream.")
    return events


def _try_parse_json_dict(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from a string (including fenced ```json blocks)."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def _normalize_site(
    raw_site: dict[str, Any],
    index: int,
    tract_coords: dict[str, tuple[float, float]],
) -> dict[str, Any] | None:
    """Normalize a single Saturday stop dict with lat/lng lookup fallback."""
    geo_id = str(raw_site.get("geo_id") or raw_site.get("anchor_geo_id") or "").strip()
    lat_raw = raw_site.get("latitude", raw_site.get("lat"))
    lng_raw = raw_site.get("longitude", raw_site.get("lng"))

    lat: float | None = None
    lng: float | None = None
    try:
        if lat_raw is not None and lng_raw is not None:
            lat = float(lat_raw)
            lng = float(lng_raw)
    except (TypeError, ValueError):
        lat = None
        lng = None

    if (lat is None or lng is None) and geo_id in tract_coords:
        lat, lng = tract_coords[geo_id]

    if lat is None or lng is None:
        return None

    saturday_num = (
        raw_site.get("saturday_number") or raw_site.get("saturday") or (index + 1)
    )
    try:
        saturday_num = int(saturday_num)
    except (TypeError, ValueError):
        saturday_num = index + 1

    reasons_list = raw_site.get("reasons")
    reasons_text = ""
    if isinstance(reasons_list, list):
        claim_texts = [
            str(item.get("text") or "").strip()
            for item in reasons_list
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if claim_texts:
            reasons_text = " ".join(claim_texts)

    default_reason = (
        f"Recommended Saturday #{saturday_num} mobile clinic site "
        f"(Tract {geo_id or 'Fulton'})."
    )
    reason = str(
        raw_site.get("selection_reason")
        or raw_site.get("summary")
        or reasons_text
        or default_reason
    ).strip()

    composite_score = raw_site.get("composite_score")
    if composite_score is None:
        composite_score = raw_site.get("score")

    return {
        "saturday_number": saturday_num,
        "date": raw_site.get("date"),
        "geo_id": geo_id,
        "lat": lat,
        "lng": lng,
        "latitude": lat,
        "longitude": lng,
        "selection_reason": reason,
        "nearest_site_name": raw_site.get("nearest_site_name"),
        "nearest_care_km": raw_site.get("nearest_care_km"),
        "composite_score": composite_score,
    }


def extract_sites(data: Any) -> list[dict[str, Any]]:
    """Extract up to 4 Saturday stops from tool responses, state_delta, or JSON text."""
    tract_coords = load_tract_coordinates()
    raw_tracts: list[dict[str, Any]] = []

    for candidate in walk_values(data, {"stops", "selected_tracts", "sites"}):
        if isinstance(candidate, list) and candidate:
            dict_items = [item for item in candidate if isinstance(item, dict)]
            if dict_items:
                raw_tracts = dict_items
                break

    if not raw_tracts:
        for text_candidate in walk_values(data, {"plan_data", "plan_json", "text"}):
            if isinstance(text_candidate, str):
                parsed = _try_parse_json_dict(text_candidate)
                if parsed:
                    list_candidate = (
                        parsed.get("stops")
                        or parsed.get("selected_tracts")
                        or parsed.get("sites")
                    )
                    if isinstance(list_candidate, list):
                        raw_tracts = [
                            item for item in list_candidate if isinstance(item, dict)
                        ]
                        if raw_tracts:
                            break

    sites: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_tracts):
        normalized = _normalize_site(item, idx, tract_coords)
        if normalized is not None:
            sites.append(normalized)

    return sites


def extract_lat_lng(data: Any) -> tuple[float | None, float | None]:
    """Extract the primary (lat, lng) pair from structured fields or tract IDs."""
    sites = extract_sites(data)
    if sites:
        return sites[0]["lat"], sites[0]["lng"]

    lat_values = list(walk_values(data, {"lat", "latitude"}))
    lng_values = list(walk_values(data, {"lng", "longitude"}))

    lat = None
    lng = None

    for value in lat_values:
        try:
            lat = float(value)
            break
        except (TypeError, ValueError):
            pass

    for value in lng_values:
        try:
            lng = float(value)
            break
        except (TypeError, ValueError):
            pass

    return lat, lng


def extract_summary(data: Any) -> str:
    """Extract the most relevant human-readable response text from ADK events."""
    if isinstance(data, list):
        root_texts: list[str] = []
        model_texts: list[str] = []
        pipeline_results: list[str] = []
        flyer_text: str | None = None
        for event in data:
            if not isinstance(event, dict):
                continue
            author = str(event.get("author") or "").strip()
            actions = event.get("actions") or {}
            state_delta = (
                actions.get("state_delta") or actions.get("stateDelta") or {}
                if isinstance(actions, dict)
                else {}
            )
            if isinstance(state_delta, dict):
                flyer = state_delta.get("flyer") or state_delta.get("flyer_json")
                if isinstance(flyer, str) and flyer.strip():
                    if _try_parse_json_dict(flyer) is None:
                        flyer_text = flyer.strip()

            content = event.get("content")
            if not isinstance(content, dict):
                continue
            for part in content.get("parts") or []:
                if not isinstance(part, dict) or part.get("thought"):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    if _try_parse_json_dict(text) is None:
                        cleaned = text.strip()
                        model_texts.append(cleaned)
                        if author == "curbside":
                            root_texts.append(cleaned)
                fn_resp = part.get("function_response") or part.get("functionResponse")
                if isinstance(fn_resp, dict):
                    resp_obj = fn_resp.get("response")
                    if isinstance(resp_obj, dict):
                        res_text = resp_obj.get("result")
                        if (
                            isinstance(res_text, str)
                            and res_text.strip()
                            and _try_parse_json_dict(res_text) is None
                        ):
                            pipeline_results.append(res_text.strip())

        if root_texts:
            return root_texts[-1]
        if flyer_text:
            return flyer_text
        if model_texts:
            return "\n\n".join(model_texts)
        if pipeline_results:
            return "\n\n".join(pipeline_results)

    for key in (
        "flyer",
        "summary",
        "selection_reason",
        "message",
        "text",
        "result",
        "output",
        "response",
    ):
        for value in walk_values(data, {key}):
            if isinstance(value, str) and value.strip():
                if _try_parse_json_dict(value) is None:
                    return value.strip()
            if isinstance(value, dict):
                text = value.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    if isinstance(data, str) and data.strip():
        return data.strip()
    return "Agent response received."


def extract_audit(data: Any) -> dict[str, Any] | None:
    """Extract the equity audit status dictionary if present in the agent events."""
    for value in walk_values(data, {"audit_result", "plan_status", "audit"}):
        if isinstance(value, dict) and (
            "verdict" in value
            or "passed" in value
            or "svi_Guardrail_Triggered" in value
            or "selected_mean" in value
        ):
            return value
        if isinstance(value, str):
            parsed = _try_parse_json_dict(value)
            if parsed:
                return parsed
    return None


def _post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> tuple[int, str]:
    """Send an HTTP POST with JSON payload using requests or urllib fallback."""
    if requests is not None:
        resp = requests.post(url, headers=headers, json=payload, timeout=240)
        return resp.status_code, resp.text

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def call_reasoning_engine(prompt: str) -> dict[str, Any]:
    """Query the deployed ADK Reasoning Engine and normalize the response for the UI."""
    token = get_google_access_token()
    target_url = resolve_reasoning_engine_url()
    normalized_message = coerce_prompt_to_plan_request(prompt)

    payload = {
        "class_method": "stream_query",
        "input": {
            "user_id": "web_user",
            "message": normalized_message,
        },
    }

    status_code, response_text = _post_json(
        target_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        payload=payload,
    )

    if status_code >= 400:
        try:
            error_body = json.loads(response_text)
        except ValueError:
            error_body = {"raw": response_text}
        raise RuntimeError(
            json.dumps(
                {
                    "status": status_code,
                    "message": "Reasoning Engine call failed.",
                    "details": error_body,
                }
            )
        )

    try:
        data = parse_agent_response_text(response_text)
    except ValueError as exc:
        raise RuntimeError(
            f"Agent response was not valid JSON: {response_text[:500]}"
        ) from exc

    sites = extract_sites(data)
    summary = extract_summary(data)
    lat, lng = extract_lat_lng(data)

    if lat is None or lng is None:
        tract_coords = load_tract_coordinates()
        mentioned_tracts = re.findall(r"\b(13121\d{6})\b", summary)
        for idx, geo_id in enumerate(dict.fromkeys(mentioned_tracts)):
            if geo_id in tract_coords:
                t_lat, t_lng = tract_coords[geo_id]
                if lat is None or lng is None:
                    lat, lng = t_lat, t_lng
                if len(sites) < 4:
                    sites.append(
                        {
                            "saturday_number": idx + 1,
                            "geo_id": geo_id,
                            "lat": t_lat,
                            "lng": t_lng,
                            "latitude": t_lat,
                            "longitude": t_lng,
                            "selection_reason": (
                                f"Census tract {geo_id} referenced by Curbside."
                            ),
                        }
                    )

    if lat is None or lng is None:
        matches = re.findall(r"(-?\d+\.\d+)", summary)
        if len(matches) >= 2:
            try:
                lat = float(matches[-2])
                lng = float(matches[-1])
            except ValueError:
                lat = None
                lng = None

    return {
        "lat": lat,
        "lng": lng,
        "summary": summary,
        "sites": sites,
        "audit": extract_audit(data),
        "raw": data,
    }


def extract_prompt_from_body(data: Any) -> str:
    """Extract the user prompt string from a request JSON dict."""
    if not isinstance(data, dict):
        return ""
    prompt = (
        data.get("request")
        or data.get("message")
        or data.get("prompt")
        or data.get("query")
        or ""
    )
    return str(prompt).strip()


def resolve_static_asset(asset_path: str) -> Path | None:
    """Resolve a path inside ``frontend/`` safely against directory traversal."""
    rel = asset_path.lstrip("/") or "index.html"
    candidate = (FRONTEND_DIR / rel).resolve()
    if not candidate.is_relative_to(FRONTEND_DIR.resolve()) or not candidate.is_file():
        return None
    return candidate


try:
    from fastapi import FastAPI
    from fastapi import Request as FastAPIRequest
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, Response

    app = FastAPI(
        title="curbside-ui",
        description="Curbside Leaflet UI and Vertex AI Agent Runtime proxy",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Health check endpoint for Cloud Run and local checks."""
        return {
            "status": "ok",
            "reasoning_engine_url": resolve_reasoning_engine_url(),
        }

    @app.options("/agent")
    def agent_options() -> Response:
        """CORS preflight handler for /agent."""
        return Response(status_code=204)

    @app.post("/agent")
    async def agent_proxy(request: FastAPIRequest) -> JSONResponse:
        """Forward a user prompt from the Leaflet UI to Vertex AI Agent Runtime."""
        try:
            data = await request.json()
        except Exception:
            data = {}

        prompt = extract_prompt_from_body(data)
        if not prompt:
            return JSONResponse({"error": "Missing request prompt."}, status_code=400)

        try:
            result = await asyncio.to_thread(call_reasoning_engine, prompt)
            return JSONResponse(result)
        except Exception as exc:  # pragma: no cover
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/")
    def serve_index() -> Response:
        """Serve the Leaflet frontend index.html at the root path."""
        target = resolve_static_asset("index.html")
        if target is None:
            return JSONResponse(
                {"error": "Frontend index.html not found."}, status_code=404
            )
        return FileResponse(target)

    @app.get("/{asset_path:path}")
    def serve_frontend_asset(asset_path: str) -> Response:
        """Serve static assets (JS, CSS, CSV) from the frontend directory."""
        target = resolve_static_asset(asset_path)
        if target is None:
            return JSONResponse({"error": "Not found."}, status_code=404)
        return FileResponse(target)

except ImportError:  # pragma: no cover
    app = None  # type: ignore[assignment]


class _StdlibProxyHandler(BaseHTTPRequestHandler):  # pragma: no cover
    """Fallback HTTP handler when FastAPI/uvicorn are not installed locally."""

    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(
                {
                    "status": "ok",
                    "reasoning_engine_url": resolve_reasoning_engine_url(),
                }
            )
            return

        target = resolve_static_asset(path)
        if target is None:
            self._send_json({"error": "Not found."}, status=404)
            return

        content = target.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self._send_cors()
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/agent":
            self._send_json({"error": "Not found."}, status=404)
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}

        prompt = extract_prompt_from_body(data)
        if not prompt:
            self._send_json({"error": "Missing request prompt."}, status=400)
            return

        try:
            result = call_reasoning_engine(prompt)
            self._send_json(result, status=200)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)


if __name__ == "__main__":  # pragma: no cover
    port = int(os.environ.get("PORT", "8002"))
    if app is not None:
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        server = ThreadingHTTPServer(("0.0.0.0", port), _StdlibProxyHandler)
        print(f"Serving Curbside UI at http://0.0.0.0:{port}")
        server.serve_forever()
