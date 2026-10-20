# Fulton GA leaflet prototype

This front-end shows a left-side Leaflet map of Fulton County, Atlanta, plus a right-side summary card and chat panel for the hosted agent.

## Run locally

From the repo root:

```bash
cd frontend
python -m http.server 8001
```

Then open `http://localhost:8001`.

## Agent integration

The page expects a hosted Google Cloud agent endpoint in `window.AGENT_ENGINE_URL`.

Example:

```html
<script>
  window.AGENT_ENGINE_URL = "https://your-agent-endpoint-url";
</script>
```

The agent should return JSON shaped like:

```json
{
  "lat": 33.7528,
  "lng": -84.3947,
  "radius_km": 12,
  "label": "Recommended next Saturday site",
  "summary": "High-need service area near Atlanta"
}
```

If the endpoint is unavailable, the page falls back to a Fulton GA default location so the UI keeps working during development.
