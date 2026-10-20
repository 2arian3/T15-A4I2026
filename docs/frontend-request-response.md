# Frontend request and response

The `frontend` branch posts to the wrong contract. `app.js` sends `POST http://localhost:8002/agent` with `{ "request", "output_format": "resp_with_latlong" }` and then calls `response.json()`. That URL does not exist.

The agent listens on **port 8000**. Create a session first. The reply is an SSE stream. Coordinates are in `plan_data.selected_tracts`, one object per Saturday, fields `latitude` and `longitude`.

Start the agent like this. Without `ALLOW_ORIGINS`, a page on `localhost:8001` gets `403 origin not allowed`:

```bash
ALLOW_ORIGINS=http://localhost:8001,http://127.0.0.1:8001 \
  uv run --env-file .env uvicorn agent.fast_api_app:app --host 127.0.0.1 --port 8000
```

Frontend, as before:

```bash
cd frontend && python -m http.server 8001
```

In `index.html`, before `app.js`:

```html
<script>
  window.AGENT_BASE = "http://127.0.0.1:8000";
</script>
```

## Request

Two steps. `app_name` is `agent`.

**1. Session**

```http
POST http://127.0.0.1:8000/apps/agent/users/frontend-demo/sessions
Content-Type: application/json

{}
```

```json
{ "id": "SESSION_ID", "appName": "agent", "userId": "frontend-demo" }
```

**2. Question**

```http
POST http://127.0.0.1:8000/run_sse
Content-Type: application/json

{
  "app_name": "agent",
  "user_id": "frontend-demo",
  "session_id": "SESSION_ID",
  "new_message": {
    "role": "user",
    "parts": [{ "text": "Where should the van park on the 4 Saturdays of next month?" }]
  },
  "streaming": false
}
```

The response is an SSE stream (`text/event-stream`), lines of `data: {...}`. The plan arrives on the event that contains `actions.stateDelta.plan_data`. Flyer text is on the event with `author: "explainer"`. The audit is in `actions.stateDelta.plan_status`. The call hits BigQuery and three agents, so wait tens of seconds and read the stream until it ends.

## Response to draw on the map

One event, trimmed to the map fields. A live response has 4 objects in `selected_tracts`.

```text
data: {"author":"planner","actions":{"stateDelta":{"plan_data":{
  "status": "success",
  "selected_tracts": [
    {
      "saturday_number": 1,
      "geo_id": "13121010214",
      "total_pop": 1938,
      "latitude": 33.72,
      "longitude": -84.41,
      "need_rank": 1,
      "scores": {
        "static_need_score": 91.2,
        "dynamic_surge_multiplier": 1.0,
        "final_need_score": 91.2
      },
      "supporting_metrics": {
        "pct_uninsured": 14.0,
        "pct_no_vehicle": 43.0,
        "km_to_safety_net": 8.1,
        "extra_km_if_uninsured": 5.3
      },
      "selection_reason": "Saturday #1 — Census Tract 13121010214 ..."
    }
  ]
}}}}
```

Then the audit and the flyer:

```text
data: {"author":"equity_auditor","actions":{"stateDelta":{"plan_status":{
  "status": "reviewed",
  "column": "svi_racial_ethnic",
  "selected_mean": 0.72,
  "county_mean": 0.55,
  "difference": 0.17
}}}}

data: {"author":"explainer","content":{"role":"model","parts":[{"text":"The van is coming to four neighborhoods this month..."}]}}
```

Pin: `selected_tracts[i].latitude` / `.longitude`. Label: `selection_reason`. `saturday_number` lines up with the four date buttons.

## Drop-in replacement for `callAgentEngine` in `app.js`

```javascript
const AGENT_BASE = window.AGENT_BASE || "http://127.0.0.1:8000";
const USER_ID = "frontend-demo";

async function callAgentEngine(message) {
  const sessionRes = await fetch(
    `${AGENT_BASE}/apps/agent/users/${USER_ID}/sessions`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
  );
  if (!sessionRes.ok) throw new Error(`session ${sessionRes.status}`);
  const sessionId = (await sessionRes.json()).id;

  const runRes = await fetch(`${AGENT_BASE}/run_sse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      app_name: "agent",
      user_id: USER_ID,
      session_id: sessionId,
      new_message: { role: "user", parts: [{ text: message }] },
      streaming: false
    })
  });
  if (!runRes.ok) throw new Error(`run_sse ${runRes.status}`);

  let plan = null;
  let audit = null;
  let flyer = "";
  const reader = runRes.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n");
    buffer = chunks.pop();
    for (const line of chunks) {
      if (!line.startsWith("data: ")) continue;
      const event = JSON.parse(line.slice(6));
      const delta = event.actions?.stateDelta || {};
      if (delta.plan_data?.status === "success") plan = delta.plan_data;
      if (delta.plan_status) audit = delta.plan_status;
      const text = (event.content?.parts || []).map((p) => p.text || "").join("");
      if (event.author === "explainer" && text) flyer += text;
    }
  }
  if (!plan) throw new Error("stream ended without plan_data");
  return { plan, audit, flyer };
}
```

Map the first Saturday onto the current `renderPlan`:

```javascript
const { plan, flyer } = await callAgentEngine(
  "Where should the van park on the 4 Saturdays of next month?"
);
const site = plan.selected_tracts[0];
renderPlan({
  label: `Saturday ${site.saturday_number} · tract ${site.geo_id}`,
  lat: site.latitude,
  lng: site.longitude,
  summary: site.selection_reason
});
addMessage("bot", flyer || site.selection_reason);
```

The deployed engine cannot be called from the browser. `projects/1001206471385/locations/us-central1/reasoningEngines/3799547422611341312` requires `Authorization: Bearer` and does not send CORS headers for `localhost:8001`. For the `frontend` branch, use the local agent at `http://127.0.0.1:8000`.
