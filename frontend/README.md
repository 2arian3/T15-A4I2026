# Curbside — Fulton County Leaflet Frontend

This frontend renders an interactive Leaflet map of Fulton County, GA (with the 67 safety-net hospitals & FQHCs and census tract centroids) alongside a Saturday stop selector and chat panel backed by the deployed Curbside Vertex AI Agent Runtime.

## Run locally (single server: UI + Agent proxy)

From the repository root:

```bash
make frontend
```

Then open `http://127.0.0.1:8002`.

`agent/proxy_server.py` serves the static UI from `frontend/` at `/` and proxies `POST /agent` to the active Reasoning Engine resource recorded in `deployment_metadata.json` (`:streamQuery`), authenticating via Application Default Credentials (or `gcloud auth print-access-token` fallback).

## Deploy to Google Cloud Run

From the repository root:

```bash
make deploy-frontend
```

This deploys the `curbside-ui` Cloud Run service in `us-central1` attached to `curbside-agent@qwiklabs-gcp-04-e3eaf2b4f0c3.iam.gserviceaccount.com`, pointing `REASONING_ENGINE_URL` at the production Reasoning Engine in `deployment_metadata.json`.

## Response contract (`POST /agent`)

`agent/proxy_server.py` returns JSON compatible with both single-point and 4-Saturday siting responses:

```json
{
  "lat": 33.7528,
  "lng": -84.3947,
  "summary": "Plain-language summary or outreach flyer",
  "sites": [
    {
      "saturday_number": 1,
      "geo_id": "13121007603",
      "lat": 33.7528,
      "lng": -84.3947,
      "selection_reason": "Why this census tract was selected for Saturday #1"
    }
  ],
  "audit": {
    "passed": true,
    "svi_Guardrail_Triggered": false
  }
}
```
