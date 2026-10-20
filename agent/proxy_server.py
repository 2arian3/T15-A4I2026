import json
import os
import re
import shutil
import subprocess
from typing import Any

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
        subprocess.check_call([executable, "config", "set", "account", STUDENT_ACCOUNT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def call_reasoning_engine(prompt: str):
    token = get_google_access_token()
    payload = {
        "input": {
            "messages": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }
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

    lat, lng = extract_lat_lng(data)
    if lat is None or lng is None:
        # Try to recover lat/lng from a raw text blob if the model returned a natural-language answer.
        text_blob = extract_summary(data)
        matches = re.findall(r"(-?\d+\.\d+)", text_blob)
        if len(matches) >= 2:
            try:
                lat = float(matches[-2])
                lng = float(matches[-1])
            except ValueError:
                lat = None
                lng = None

    if lat is None or lng is None:
        raise RuntimeError(
            "The agent response did not include usable latitude/longitude values. "
            f"Raw payload: {json.dumps(data)[:1200]}"
        )

    return {
        "lat": lat,
        "lng": lng,
        "summary": extract_summary(data),
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

    if not prompt or not str(prompt).strip():
        return jsonify({"error": "Missing request prompt."}), 400

    try:
        result = call_reasoning_engine(str(prompt).strip())
        return jsonify(result)
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8002")), debug=False)
