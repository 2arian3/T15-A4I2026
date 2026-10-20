"""Evidence is the only MCP client. No model can select or write its SQL."""

import asyncio
import json
import os
import re
from urllib.parse import urlparse

from google.adk.agents import BaseAgent, ParallelAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.tools.tool_context import ToolContext

from .contracts import Claim, EvidenceBrief, PlanRequest, TractEvidence

TOOL_NAMES = [
    "burden_for_county",
    "access_for_county",
    "exposure_for_county",
    "county_context",
]


def parse_mcp_rows(payload) -> list[dict]:
    if not isinstance(payload, dict):
        raise RuntimeError("MCP lookup returned an invalid response")
    if payload.get("isError") or payload.get("is_error") or payload.get("error"):
        detail = payload.get("error") or " ".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        detail = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", str(detail))
        detail = re.sub(
            r"(?i)([?&](?:key|access_token|token)=)[^&\s\"']+", r"\1[REDACTED]", detail
        )
        raise RuntimeError(f"MCP lookup returned an error: {detail[:1500]}")
    structured = payload.get("structuredContent", payload.get("structured_content"))
    if structured is not None:
        rows = (
            structured.get("rows", structured.get("results", structured.get("result")))
            if isinstance(structured, dict)
            else structured
        )
    else:
        rows = []
        for block in payload.get("content", []):
            if block.get("type") == "text":
                value = json.loads(block["text"])
                if isinstance(value, dict) and ("rows" in value or "results" in value):
                    value = value.get("rows", value.get("results"))
                rows.extend(value if isinstance(value, list) else [value])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("MCP lookup must return a list of row objects")
    return rows


def toolbox_headers(url: str) -> dict:
    """An identity token is for the private Cloud Run origin, never for the LLM."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.oauth2.id_token import fetch_id_token

    from . import data_access

    parsed = urlparse(url)
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
        return {}
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "TOOLBOX_URL must be a private HTTPS Cloud Run origin (or localhost)"
        )
    audience = f"{parsed.scheme}://{parsed.netloc}"
    source, _ = data_access.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if isinstance(source, UserCredentials):
        source = data_access.runtime_credentials(source)
        token = data_access.impersonated_credentials.IDTokenCredentials(
            source, target_audience=audience
        )
        token.refresh(Request())
        return {"Authorization": f"Bearer {token.token}"}
    if isinstance(source, data_access.impersonated_credentials.Credentials):
        token = data_access.impersonated_credentials.IDTokenCredentials(
            source, target_audience=audience
        )
        token.refresh(Request())
        return {"Authorization": f"Bearer {token.token}"}
    return {"Authorization": f"Bearer {fetch_id_token(Request(), audience)}"}


async def lookup_tools(ctx, names: list[str]) -> dict[str, list[dict]]:
    from google.adk.tools.mcp_tool.mcp_session_manager import (
        StreamableHTTPConnectionParams,
    )
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    url = os.getenv("TOOLBOX_URL", "").rstrip("/")
    if not url:
        raise RuntimeError(
            "TOOLBOX_URL is required; deploy the fixed Toolbox service first"
        )
    headers = await asyncio.to_thread(toolbox_headers, url)
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=url + "/mcp", headers=headers, timeout=15, sse_read_timeout=60
        ),
        tool_filter=names,
    )
    try:
        tools = {t.name: t for t in await toolset.get_tools()}
        if set(tools) != set(names):
            raise RuntimeError("Toolbox is missing required named tools")
        county = PlanRequest.model_validate(ctx.session.state["request"]).county_fips
        result = {}
        for name in names:
            result[name] = parse_mcp_rows(
                await tools[name].run_async(
                    args={"county_fips": county}, tool_context=ToolContext(ctx)
                )
            )
        return result
    finally:
        await toolset.close()


def merge_evidence(
    county: str,
    burden: list[dict],
    access: list[dict],
    exposure: list[dict],
    context: list[dict],
) -> tuple[EvidenceBrief, list[Claim]]:
    if not burden:
        raise ValueError("burden_for_county returned no tracts; no plan can be built")
    if len(context) != 1:
        raise ValueError("county_context must return one summary row")
    if context[0].get("tract_count", len(burden)) != len(burden):
        raise ValueError(
            "Toolbox burden result was truncated; no partial plan is allowed"
        )
    by_access = {r["geo_id"]: r for r in access}
    by_exposure = {r["geo_id"]: r for r in exposure}
    if len(by_access) != len(access) or len(by_exposure) != len(exposure):
        raise ValueError("Duplicate access or exposure tract rows")
    candidates = []
    for row in burden:
        merged = {
            **row,
            **by_access.get(row["geo_id"], {}),
            **by_exposure.get(row["geo_id"], {}),
        }
        # SVI's missing sentinel must not be interpreted as low need.
        if merged.get("svi_socioeconomic") == -999:
            merged["svi_socioeconomic"] = None
        candidates.append(TractEvidence.model_validate(merged))
    limits = [
        Claim(
            text="Modeled adult prevalence cannot establish an individual's health or predict attendance.",
            source_table="burden_tracts",
            scale="TRACT",
            kind="modeled",
        ),
        Claim(
            text="Centroid catchments use straight-line distance, not road routes, travel time or actual reach.",
            source_table="tract_access",
            scale="FACILITY-POINT",
            kind="upper_bound",
        ),
        Claim(
            text="Federal facility lists omit free and county clinics; a nearer unlisted site may exist.",
            source_table="care_sites_state",
            scale="FACILITY-POINT",
            kind="upper_bound",
        ),
        Claim(
            text="An anchor tract is not a booked venue; confirm the address, hours, services, cost and staffing.",
            source_table="burden_tracts",
            scale="TRACT",
            kind="measured",
        ),
    ]
    for field in TractEvidence.model_fields:
        if field in {"geo_id", "total_pop"}:
            continue
        missing = sum(getattr(t, field) is None for t in candidates)
        if missing:
            source = (
                "exposure_tracts"
                if field in {"mean_pm", "days_over_12"}
                else "tract_access"
                if "km_" in field
                else "burden_tracts"
            )
            limits.append(
                Claim(
                    text=f"{field}: missing for {missing} of {len(candidates)} tracts; missing does not mean zero.",
                    source_table=source,
                    scale="12 KM GRID"
                    if source == "exposure_tracts"
                    else "FACILITY-POINT"
                    if source == "tract_access"
                    else "TRACT",
                    kind="modeled"
                    if source == "exposure_tracts"
                    or (field.startswith("pct_") and field != "pct_no_vehicle")
                    else "upper_bound"
                    if source == "tract_access"
                    else "measured",
                )
            )
    c = context[0]
    claims = []
    weeks, usable = c.get("disease_weeks", 0), c.get("usable_disease_weeks", 0)
    if not usable:
        limits.append(
            Claim(
                text=f"There are no usable disease weeks ({weeks} weeks present); no regional trend can be inferred.",
                source_table="disease_weekly",
                scale="MULTI-COUNTY REGION",
                kind="measured",
            )
        )
    else:
        for disease in ("covid", "influenza", "rsv"):
            value = c.get(f"latest_{disease}_pct")
            if value is not None:
                claims.append(
                    Claim(
                        text=f"Week ending {c.get('latest_week')}: {disease} ED visits {value}%; shared Health Service Area, never a tract score.",
                        source_table="disease_weekly",
                        scale="MULTI-COUNTY REGION",
                        kind="measured",
                    )
                )
    shortage = c.get("shortage_area_count", 0)
    unscoped = c.get("shortage_area_unscoped_count", 0)
    if unscoped:
        limits.append(
            Claim(
                text=f"{unscoped} shortage-area rows have no county identifier; the county designation count cannot be established.",
                source_table="shortage_areas",
                scale="MULTI-COUNTY REGION",
                kind="measured",
            )
        )
    else:
        claims.append(
            Claim(
                text=f"{shortage} designated shortage areas in the county; area designations are not facility designations.",
                source_table="shortage_areas",
                scale="MULTI-COUNTY REGION",
                kind="measured",
            )
        )
    if not shortage and not unscoped:
        limits.append(
            Claim(
                text="No designated shortage areas in this county; absence of a designation does not show adequate access.",
                source_table="shortage_areas",
                scale="MULTI-COUNTY REGION",
                kind="measured",
            )
        )
    claims.append(
        Claim(
            text="PM2.5 values are a shared modeling grid; they provide context and cannot separate nearby tracts.",
            source_table="exposure_tracts",
            scale="12 KM GRID",
            kind="modeled",
        )
    )
    return EvidenceBrief(
        county_fips=county, candidates=candidates, context=claims
    ), limits


class EvidenceLookup(BaseAgent):
    tool_names: list[str]
    state_key: str

    async def _run_async_impl(self, ctx):
        try:
            rows = await lookup_tools(ctx, self.tool_names)
        except Exception as exc:
            raise RuntimeError(
                f"Evidence lookup {', '.join(self.tool_names)} failed: {exc}"
            ) from exc
        ctx.session.state[self.state_key] = rows
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(state_delta={self.state_key: rows}),
        )


class EvidenceMerge(BaseAgent):
    async def _run_async_impl(self, ctx):
        s = ctx.session.state
        brief, limits = merge_evidence(
            s["request"]["county_fips"],
            s["ev_burden"]["burden_for_county"],
            s["ev_access"]["access_for_county"],
            s["ev_access"]["exposure_for_county"],
            s["ev_context"]["county_context"],
        )
        delta = {
            "evidence_brief": brief.model_dump(mode="json"),
            "limits_brief": [c.model_dump() for c in limits],
        }
        s.update(delta)
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            actions=EventActions(state_delta=delta),
        )


def evidence_agent():
    return SequentialAgent(
        name="evidence",
        sub_agents=[
            ParallelAgent(
                name="evidence_lookups",
                sub_agents=[
                    EvidenceLookup(
                        name="burden",
                        tool_names=["burden_for_county"],
                        state_key="ev_burden",
                    ),
                    EvidenceLookup(
                        name="access",
                        tool_names=["access_for_county", "exposure_for_county"],
                        state_key="ev_access",
                    ),
                    EvidenceLookup(
                        name="context",
                        tool_names=["county_context"],
                        state_key="ev_context",
                    ),
                ],
            ),
            EvidenceMerge(name="evidence_merge"),
        ],
    )
