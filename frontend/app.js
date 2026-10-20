const fultonBounds = L.latLngBounds([
  [33.387, -84.937],
  [34.142, -84.049]
]);

const careSitesUrl = "care_sites.csv";
let facilityData = [];

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === ',' && !inQuotes) {
      values.push(current);
      current = "";
      continue;
    }

    current += char;
  }

  values.push(current);
  return values;
}

function parseCsvRows(text) {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (!lines.length) {
    return [];
  }

  const headers = parseCsvLine(lines[0]).map((header) => header.trim());
  return lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    const row = {};

    headers.forEach((header, index) => {
      row[header] = values[index] ?? "";
    });

    return row;
  });
}

async function loadFacilityData() {
  const response = await fetch(careSitesUrl);
  if (!response.ok) {
    throw new Error(`Failed to load care site CSV: ${response.status}`);
  }

  const text = await response.text();
  facilityData = parseCsvRows(text)
    .filter((row) => row.FACILITY_NM && row.latitude && row.longitude)
    .map((row) => ({
      name: row.FACILITY_NM,
      category: row.CMS_PROVIDER_CAT_DESC,
      address: row.CMS_PROVIDER_ADDRESS,
      city: row.CMS_PROVIDER_CITY,
      state: row.CMS_PROVIDER_STATE_ABBR,
      zip: row.CMS_PROVIDER_ZIP_CD,
      latitude: Number(row.latitude),
      longitude: Number(row.longitude),
      site_kind: row.site_kind || row.CMS_PROVIDER_CAT_SUB_TYP_DESC || row.CMS_PROVIDER_CAT_DESC
    }))
    .filter((facility) => Number.isFinite(facility.latitude) && Number.isFinite(facility.longitude));

  return facilityData;
}


const map = L.map("map", {
  zoomControl: true,
  minZoom: 9,
  maxZoom: 16,
  maxBounds: fultonBounds,
  maxBoundsViscosity: 1.0,
  scrollWheelZoom: true
});

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19
}).addTo(map);

const fultonOutline = L.polygon([
  [33.593, -84.859],
  [33.572, -84.685],
  [33.610, -84.529],
  [33.710, -84.425],
  [33.796, -84.263],
  [33.904, -84.299],
  [33.996, -84.360],
  [34.040, -84.530],
  [34.033, -84.744],
  [33.952, -84.863],
  [33.790, -84.905],
  [33.666, -84.916]
], {
  color: "#1d4ed8",
  weight: 2,
  opacity: 0.9,
  fillOpacity: 0.04,
  dashArray: "8 8"
}).addTo(map);

map.fitBounds(fultonOutline.getBounds(), { padding: [24, 24] });

const siteTitle = document.getElementById("siteTitle");
const siteLocation = document.getElementById("siteLocation");
const siteCoords = document.getElementById("siteCoords");
const chatMessages = document.getElementById("chatMessages");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const dateSelector = document.getElementById("dateSelector");

let activeMarker = null;
let facilityLayerGroup = null;

function getNextSaturdayDate() {
  const today = new Date();
  const daysUntilSaturday = (6 - today.getDay() + 7) % 7 || 7;
  const nextSaturday = new Date(today);
  nextSaturday.setDate(today.getDate() + daysUntilSaturday);
  return nextSaturday;
}

function getUpcomingSaturdays(count = 4) {
  const startDate = getNextSaturdayDate();
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(startDate);
    date.setDate(startDate.getDate() + index * 7);
    return date;
  });
}

function formatSaturdayLabel(date) {
  return `Plan for ${date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  })}`;
}

function renderDateSelector() {
  if (!dateSelector) return;

  const dates = getUpcomingSaturdays();
  dateSelector.innerHTML = "";

  dates.forEach((date, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `date-option${index === 0 ? " is-active" : ""}`;
    button.setAttribute("aria-label", formatSaturdayLabel(date));
    button.innerHTML = `
      <span class="date-dot"></span>
      <span class="date-label">${date.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric'
      })}</span>
    `;

    button.addEventListener("click", () => {
      const options = dateSelector.querySelectorAll(".date-option");
      options.forEach((option) => option.classList.remove("is-active"));
      button.classList.add("is-active");
      siteTitle.textContent = formatSaturdayLabel(date);
    });

    dateSelector.appendChild(button);
  });
}

function getNextSaturdayLabel() {
  return formatSaturdayLabel(getNextSaturdayDate());
}

function addMessage(role, text) {
  const message = document.createElement("div");
  message.className = `message ${role}`;

  const bubble = document.createElement("p");
  bubble.textContent = text;

  message.appendChild(bubble);
  chatMessages.appendChild(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function updateSummary(plan) {
  if (siteTitle) siteTitle.textContent = plan.label;
  siteLocation.textContent = "Fulton GA (Atlanta)";
  siteCoords.textContent = `${plan.lat.toFixed(4)}, ${plan.lng.toFixed(4)}`;
}

function clearHighlight() {
  if (activeMarker) {
    map.removeLayer(activeMarker);
    activeMarker = null;
  }
}

function renderFacilityMarkers() {
  if (facilityLayerGroup) {
    map.removeLayer(facilityLayerGroup);
  }

  facilityLayerGroup = L.layerGroup().addTo(map);
  window.__facilityMarkers = facilityLayerGroup;
  window.__facilityData = facilityData;

  facilityData.forEach((facility) => {
    const point = [facility.latitude, facility.longitude];
    const tooltipText = [
      facility.name,
      facility.category,
      `${facility.address}, ${facility.city}, ${facility.state} ${facility.zip}`,
      `Lat: ${facility.latitude.toFixed(6)}, Lng: ${facility.longitude.toFixed(6)}`
    ].join('<br>');

    const marker = L.circleMarker(point, {
      radius: 7,
      color: facility.site_kind === 'Hospital' ? '#0f766e' : '#1d4ed8',
      fillColor: facility.site_kind === 'Hospital' ? '#14b8a6' : '#60a5fa',
      fillOpacity: 0.9,
      weight: 1.5
    }).addTo(facilityLayerGroup);

    marker.bindTooltip(tooltipText, {
      direction: 'top',
      permanent: false,
      sticky: true,
      opacity: 0.96
    });

    marker.bindPopup(`
      <div>
        <strong>${facility.name}</strong><br>
        <span>${facility.category}</span><br>
        <span>${facility.address}</span><br>
        <span>${facility.city}, ${facility.state} ${facility.zip}</span><br>
        <span>Site type: ${facility.site_kind}</span>
      </div>
    `);
  });
}


function renderPlan(plan) {
  clearHighlight();

  const point = [plan.lat, plan.lng];

  activeMarker = L.marker(point, {
    title: plan.label
  }).addTo(map);

  activeMarker.bindPopup(`<strong>${plan.label}</strong><br>${plan.summary}`);
  activeMarker.openPopup();

  map.flyTo(point, 11, { duration: 1.4 });
  updateSummary(plan);
}

function normalizeAgentResult(payload) {
  const candidates = [
    payload?.data,
    payload?.result,
    payload?.output,
    payload?.response,
    payload
  ];

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue;

    const lat = Number(candidate.lat ?? candidate.latitude ?? candidate.location?.lat ?? candidate.location?.latitude);
    const lng = Number(candidate.lng ?? candidate.longitude ?? candidate.location?.lng ?? candidate.location?.longitude);
    const radius = Number(candidate.radius_km ?? candidate.radius ?? candidate.areaRadiusKm);

    if (Number.isFinite(lat) && Number.isFinite(lng)) {
      return {
        label: candidate.week || "Recommended next Saturday site",
        lat,
        lng,
        radius_km: Number.isFinite(radius) ? radius : null,
        summary: candidate.summary || candidate.message || "Next Saturday outreach site recommended by the agent."
      };
    }
  }

  return null;
}

window.AGENT_ENGINE_URL = window.AGENT_ENGINE_URL || "http://localhost:8002/agent";

async function callAgentEngine(payload) {
  const endpoint = window.AGENT_ENGINE_URL || "";

  if (!endpoint || endpoint === "https://your-agent-engine-endpoint.example") {
    throw new Error("No agent endpoint configured. Set window.AGENT_ENGINE_URL before requesting the next Saturday plan.");
  }

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`Agent request failed: ${response.status}`);
  }

  const result = await response.json();
  const plan = normalizeAgentResult(result);
  if (!plan) {
    throw new Error("Agent response was missing latitude/longitude metadata.");
  }

  return plan;
}

async function sendInitialPlannerPrompt() {
  const message = "Where should the van park on the 4 Saturdays of next month?";

  addMessage("user", message);

  try {
    const plan = await callAgentEngine({
      request: message,
      output_format: "resp_with_latlong"
    });

    renderPlan(plan);
    addMessage("bot", `${plan.summary} I highlighted the recommended site at ${plan.lat.toFixed(4)}, ${plan.lng.toFixed(4)}.`);
  } catch (error) {
    addMessage("bot", `The agent could not respond right now. ${error.message}`);
  }
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const message = chatInput.value.trim();
  if (!message) {
    return;
  }

  addMessage("user", message);
  chatInput.value = "";

  try {
    const plan = await callAgentEngine({
      request: message,
      output_format: "resp_with_latlong"
    });

    renderPlan(plan);
    addMessage("bot", `${plan.summary} I highlighted the recommended site at ${plan.lat.toFixed(4)}, ${plan.lng.toFixed(4)}.`);
  } catch (error) {
    addMessage("bot", `The agent could not respond right now. ${error.message}`);
  }
});

loadFacilityData()
  .then((loadedFacilities) => {
    facilityData = loadedFacilities;
    renderFacilityMarkers();
    addMessage("bot", `Loaded ${facilityData.length} care sites from the Fulton CSV.`);
  })
  .catch((error) => {
    addMessage("bot", `The Fulton care site CSV could not be loaded: ${error.message}`);
  });

renderDateSelector();
siteTitle.textContent = getNextSaturdayLabel();
addMessage("bot", "The map is focused on Fulton GA and overlays the census tract outlines. Hover the pin or tract border to see the tract ID.");

sendInitialPlannerPrompt();
