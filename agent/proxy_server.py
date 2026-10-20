import json
import os
import shutil
import subprocess
import time
from typing import Any

# #region agent log
_DEBUG_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".cursor",
    "debug-199fba.log",
)


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
        line = json.dumps(
            {
                "sessionId": "199fba",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }
        )
        with open(_DEBUG_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass
# #endregion

import requests
from flask import Flask, jsonify, request
from google.auth import default
from google.auth.transport.requests import Request

app = Flask(__name__)

DEFAULT_REASONING_ENGINE_URL = (
    "https://us-central1-aiplatform.googleapis.com/v1/projects/"
    "qwiklabs-gcp-04-e3eaf2b4f0c3/locations/us-central1/"
    "reasoningEngines/1906839310464712704:query"
)

REASONING_ENGINE_URL = os.environ.get(
    "REASONING_ENGINE_URL",
    DEFAULT_REASONING_ENGINE_URL,
)
STUDENT_ACCOUNT = "student-04-236122435814@qwiklabs.net"

# Avoid stale ADC files from a personal account if they are not valid for this project.
if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and not os.path.exists(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]):
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def get_google_access_token() -> str:
    executable = shutil.which("gcloud") or shutil.which("gcloud.cmd") or r"C:\Users\Nirmal\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

    try:
        token = subprocess.check_output(
            [executable, "auth", "print-access-token", "--account", STUDENT_ACCOUNT],
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
        ).strip()
        if token:
            return token
    except Exception:
        pass

    try:
        credentials, _ = default()
        credentials.refresh(Request())
        if credentials.token:
            return credentials.token
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Could not obtain a Google access token using the Qwiklabs account. Run 'gcloud auth login' and 'gcloud config set account student-04-236122435814@qwiklabs.net'."
        ) from exc

    raise RuntimeError("No Google access token was available for the Reasoning Engine call.")


def walk_values(value: Any, key_names: set[str]):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in key_names:
                yield child
            yield from walk_values(child, key_names)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item, key_names)


def extract_lat_lng(data: Any):
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


def coordinates_from_response(data: Any) -> tuple[float, float, str]:
    """Read the pin from plan_data. A failed plan raises its error_message."""
    plans = [
        plan for plan in walk_values(data, {"plan_data"}) if isinstance(plan, dict)
    ]
    # #region agent log
    _debug_log(
        "B",
        "proxy_server.py:coordinates_from_response",
        "plan_data found in engine output",
        {
            "plan_count": len(plans),
            "statuses": [plan.get("status") for plan in plans],
            "tract_counts": [
                len(plan.get("selected_tracts") or [])
                if isinstance(plan.get("selected_tracts"), list)
                else None
                for plan in plans
            ],
            "error_prefixes": [
                str(plan.get("error_message") or "")[:180] for plan in plans
            ],
        },
    )
    # #endregion
    for plan in plans:
        tracts = plan.get("selected_tracts") or []
        if not isinstance(tracts, list):
            continue
        for tract in tracts:
            if not isinstance(tract, dict):
                continue
            try:
                lat = float(tract["latitude"])
                lng = float(tract["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            summary = tract.get("selection_reason") or extract_summary(data)
            return lat, lng, str(summary)
    for plan in plans:
        if plan.get("status") == "error":
            raise RuntimeError(str(plan.get("error_message") or "Siting plan failed."))
    lat, lng = extract_lat_lng(data)
    if lat is None or lng is None:
        raise RuntimeError(
            "The agent response did not include usable latitude/longitude values. "
            f"Raw payload: {json.dumps(data)[:1200]}"
        )
    return lat, lng, extract_summary(data)


def extract_summary(data: Any) -> str:
    for key in ("summary", "message", "text", "output", "response"):
        for value in walk_values(data, {key}):
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                text = value.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    if isinstance(data, str):
        return data.strip()
    return "Agent response received."


def call_reasoning_engine(prompt: str, user_id: str = "frontend-demo"):
    # #region agent log
    _debug_log(
        "C",
        "proxy_server.py:call_reasoning_engine",
        "requesting access token",
        {"user_id": user_id, "prompt_len": len(prompt)},
    )
    # #endregion
    token = get_google_access_token()
    # #region agent log
    _debug_log(
        "C",
        "proxy_server.py:call_reasoning_engine",
        "access token acquired",
        {"token_present": bool(token)},
    )
    # #endregion
    payload = {
        "class_method": "async_stream_query",
        "input": {
            "user_id": user_id,
            "message": prompt,
        },
    }

    response = requests.post(
        REASONING_ENGINE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    # #region agent log
    _debug_log(
        "A",
        "proxy_server.py:call_reasoning_engine",
        "reasoning engine responded",
        {
            "status": response.status_code,
            "bytes": len(response.content),
            "engine": REASONING_ENGINE_URL.rsplit("/", 1)[-1],
        },
    )
    # #endregion

    if response.status_code >= 400:
        try:
            error_body = response.json()
        except ValueError:
            error_body = {"raw": response.text}
        raise RuntimeError(
            json.dumps({
                "status": response.status_code,
                "message": "Reasoning Engine call failed.",
                "details": error_body,
            })
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Agent response was not valid JSON: {response.text[:500]}") from exc

    lat, lng, summary = coordinates_from_response(data)
    return {
        "lat": lat,
        "lng": lng,
        "summary": summary,
        "raw": data,
    }


@app.route("/agent", methods=["POST", "OPTIONS"])
def agent_proxy():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    prompt = (
        data.get("request")
        or data.get("message")
        or data.get("prompt")
        or data.get("query")
        or ""
    )
    if not prompt:
        new_message = data.get("new_message")
        parts = new_message.get("parts") if isinstance(new_message, dict) else None
        if isinstance(parts, list):
            prompt = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict)
            )

    if not prompt or not str(prompt).strip():
        return jsonify({"error": "Missing request prompt."}), 400

    user_id = str(data.get("user_id") or "frontend-demo")
    # #region agent log
    _debug_log(
        "E",
        "proxy_server.py:agent_proxy",
        "accepted /agent body",
        {"keys": sorted(data.keys()), "user_id": user_id, "prompt_len": len(str(prompt))},
    )
    # #endregion
    try:
        result = call_reasoning_engine(str(prompt).strip(), user_id=user_id)
        return jsonify(result)
    except Exception as exc:  # pragma: no cover
        # #region agent log
        _debug_log(
            "D",
            "proxy_server.py:agent_proxy",
            "proxy returning 500",
            {"exc_type": type(exc).__name__, "exc": str(exc)[:500]},
        )
        # #endregion
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8002")), debug=False)
