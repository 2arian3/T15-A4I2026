# Outreach planner

Curbside runs a fixed ADK pipeline:

```text
Request → parallel evidence over MCP → merge
       → [Planner → Equity auditor → Approval gate] × at most 3
       → [Explainer → Flyer checks] × at most 3 → result
```

The three Gemini roles retain the repository's `gemini-3.8-flash` default and
`CURBSIDE_MODEL` override. Code controls ranking, audit verdicts, approval and
rewrites. A run times out after 180 seconds and hands completed drafts to a human.

## Run locally

Install with `uv sync --extra lint`. Supply a private Toolbox Cloud Run origin in
`TOOLBOX_URL`, and ADC for an identity that can invoke it and impersonate
`curbside-auditor`. The deployed runtime is granted these permissions during setup;
local developers use user ADC and keylessly impersonate the runtime, which then
impersonates the auditor. Setup grants the chosen deployer TokenCreator on the
runtime account for that purpose; no private key is created.
There is no direct-BigQuery fallback when Toolbox is unavailable.

Use `make web` or `make serve`, then submit this JSON as the user message, or put
its object in `session.state["request"]` through the ADK session API:

```json
{"county_fips":"13121","month":"2026-11","saturdays":["2026-11-07","2026-11-14","2026-11-21","2026-11-28"]}
```

Messages without a valid request receive input help without a model call. This
version is a planning pipeline, not a general conversational coordinator. Starting
another plan resets prior evidence, audits, drafts and flyer results in that session.
Save the old run if its history is needed. Choose any four ordered Saturdays in a
month with five Saturdays; a request always contains exactly four.

Each stop in `plan_data` and `plan_history` includes `latitude` and `longitude`
copied from its anchor tract's evidence. The final approved response includes a
code-generated coordinate table (six decimal places) even if the planner's prose
omits coordinates. These are tract centroids for planning, not confirmed clinic
locations; an outreach lead must still choose and confirm a venue.

## Data and identity boundary

| Identity | Dataset read access | Use |
|---|---|---|
| `curbside-planner` | `a4i_health` only | Toolbox server; fixed evidence SQL |
| `curbside-auditor` | `a4i_health`, `a4i_audit` | Independent Python audit tool |
| `curbside-agent` | Neither | Models and orchestration; token creation on the two tool identities |

`make setup-dry-run` previews grants and migration revokes. Setup creates
`a4i_audit`, creates the tool identities, grants dataset-scoped viewers and removes
the old runtime viewer grants. This assumes these accounts have no inherited
project-wide dataViewer/editor/owner grants: an administrator must check inherited
and group permissions before claiming live IAM isolation. TokenCreator is granted
on each target identity, not on the project. Standard ADK Trace spans identify
each agent and tool; `CURBSIDE_DEV_LOGGING=1` enables LoggingPlugin call/token logs.

The planner never receives demographic fields. The auditor's prompt has no state
interpolation. All three roles additionally filter their model request contents:
ADK's `include_contents='none'` still forwards the preceding agent's final reply.
Only a role's own tool exchange and its explicitly templated state survive.
The planner receives structural repair instructions and exclusions, while users
retain the full demographic `audit_report` and structured `audit_result`.

## Deployment preparation

These steps change cloud resources and require operator approval. Review the
printed commands first. The existing `CLAUDE.md` also requires explicit approval
before agent deployment.

1. Load the county's eight health tables (`scripts/load.sh`). The challenge loader
   replaces county snapshots; load the matching county before each live eval.
2. Apply `make setup` after reviewing `make setup-dry-run`.
3. Request and activate a [Census API key](https://api.census.gov/data/key_signup.html).
   Set `CENSUS_API_KEY=your-key` in your gitignored `.env` or export that variable;
   the loader reads `.env` automatically. This key is for the administrative loader
   only and is not sent to the deployed agents. Census metadata is available without
   a key, but data queries require one. Then run `make load-audit COUNTY_FIPS=13121`
   as a data administrator. The loader
   discovers the newest public `censustract_YEAR_5yr` table and checks the count
   columns. It validates count definitions against Census B03002, supplements the missing
   Pacific Islander count, and gets language-household counts at the same vintage.
   It selects B16002 where available or C16002 (including 2020) from that year's
   catalog, checking labels rather than assuming stable table or variable names.
   It rejects missing counts and mismatched tract boundaries before replacing
   that county, and preserves other counties in one transaction. `--source-table`
   can select another verified vintage; `--dry-run` validates BigQuery source SQL
   without writing. Neither planner nor runtime has write permission to audit data.
4. Review `make toolbox-dry-run`, then run `make deploy-toolbox`. It creates the
   Artifact Registry Docker repository `curbside` in the target project/region if
   missing and reuses it on later runs. The deployer needs repository creation
   permission, and the build identity must be able to push there. Permission errors
   or an existing non-Docker repository stop the deploy before building. A custom
   `TOOLBOX_IMAGE` requires its destination repository to be provisioned separately.
   Its container pins the
   official Toolbox 1.14.0 binary image and bundles `toolbox/tools.yaml`.
5. Copy the printed Cloud Run origin into `TOOLBOX_URL`. The service requires IAM
   invocation and runs as the planner identity. The script grants only the runtime
   account invoker access. Local developers need a separate approved invoker grant.
6. Run `make env TOOLBOX_URL=https://...`, test locally, then
   `make deploy TOOLBOX_URL=https://...` after deployment approval. The Makefile
   refuses a deployment with an empty Toolbox URL.

Health and audit datasets are in US. `BQ_DATASET` and `BQ_AUDIT_DATASET` are runtime
overrides; the audit loader intentionally uses the canonical `a4i_health` and
`a4i_audit` names. The setup/deployment Makefile uses `DATASET` and `AUDIT_DATASET`.
`CURBSIDE_PROJECT_ID` supplies the textual project ID for BigQuery table paths
and tool service-account emails. `make deploy` passes it from `PROJECT_ID`;
Agent Runtime reserves `GOOGLE_CLOUD_PROJECT` and may inject a numeric project
number there. Local runs still accept a textual `GOOGLE_CLOUD_PROJECT` when the
application setting is absent. Do not pass the reserved variable to deployment.
If renaming datasets, adapt the administrative loader before loading.
Stop the Toolbox service before deleting its service account. `make destroy-sa`
removes the three identities and dataset grants; it preserves both datasets.

## Policy choices and limits

These are explicit demo defaults in `agent/config.py`, not validated public-health
standards. Review them with the outreach lead before operational use.

- Tracts need population at least 500, a centroid, uninsured adult share,
  no-vehicle household share and safety-net distance to qualify as anchors.
  Other missing factors are excluded from a tract's weighted score and reported;
  they are never assigned zero need. Percentile weights come from the siting-tool
  branch with PM2.5 removed and the remaining weights renormalized.
- Disease trends and PM2.5 are context only. Missing transport and disease data,
  empty shortage areas and LEFT-joined exposure gaps produce scale-tagged findings.
  Optional `pct_lack_transport` is read as NULL when absent from the snapshot's
  schema, in both evidence and audit queries. Empty shortage snapshots may omit
  the county column; nonempty rows without a county identifier are reported as
  unscoped, rather than evidence of zero county designations. MCP errors retain
  bounded upstream diagnostics with bearer tokens and URL keys redacted.
  Toolbox returns at most 10,000 rows; a separate county count detects incomplete
  burden results and stops the run.
- Catchments assign nearby tract centroids within 3 km to the nearest anchor,
  once each. An anchor retains its own tract. They are estimates of geographic
  catchments, not evidence of attendance or people actually served.
- Initial spacing is zero. The audit requires 3 km; revisions use 5 km and omit
  anchors flagged by the audit. Equity shortfalls exclude the two lowest-share
  catchments for that comparison so a repair changes the rejected selection; only
  anchor IDs are passed back to the planner. There is no fallback that silently breaks spacing.
  A partial draft can be saved but cannot be approved.
- Scores within 0.02 are ties. Equally needy tracts score at least the lowest
  selected anchor minus 0.05. Audit shares use the audit dataset's population
  counts, not unweighted tract percentages. Categories are mutually exclusive non-Hispanic race groups plus Hispanic / Latino
  residents of any race, verified against B03002 before loading.
- A county shortfall greater than 10 percentage points blocks approval only if
  the need-matched shortfall also exceeds 5 points. County gaps explained by need
  are disclosures. Missing demographic coverage blocks approval. No outcome or
  individual-level inference follows from approval.
- Spanish is the default `SECOND_LANGUAGE`. Limited-English household totals do
  not identify a language. An outreach lead must choose the appropriate language.
- Flyers use logistics placeholders; free/insurance/sliding-scale claims need
  `[CONFIRM]` in the same sentence. The validator checks layout, length, numeric
  and banned claims, common English/Spanish payment claims and an approximate
  Flesch-Kincaid grade. Syllables use a vowel-group heuristic. Passing checks
  cannot prove translation accuracy or semantic fidelity; the lead reviews copy
  and confirms venue, hours, cost, services and eligibility before publication.
- A failed flyer remains a draft in state and is withheld from the final combined
  handout. Explainer content is suppressed from intermediate streaming events.
  Consumers should use the final root event and inspect `flyer_status` before
  publishing any copy; saved drafts remain available in session state.

## State and verification

Tools write validated JSON objects to `plan_data` and `audit_result`; text agents
write `calendar_text`, `audit_report`, `flyer_text`. The gate reads structured
results only, preserves all completed drafts in `plan_history`, and writes
`plan_status` and a statistics-free first-Saturday `plan_summary`. This summary
omits tract identifiers to keep them out of resident copy. `flyer_check` lists
mechanical repairs. `flyer_status` is PASS or FAIL. Missing/invalid tool calls
cannot reuse a previous round's approval. Evidence failure produces no plan.

`make test` runs without cloud credentials. It covers a real ADK Runner with
scripted model responses, isolation on the actual model request, contradictory
auditor prose, both loop caps, timeout, a real MCP stdio transport fixture, missing
county data, scoring invariance, population-weighted comparisons and flyer failures.
The fixture is test-only; deployment always consumes the official Toolbox service.

The county eval dataset includes Fulton, Harris, Santa Clara and Bronx. For each
county, load matching health/audit data first; then evaluate that case separately.
The generic greeting case needs neither cloud data nor a model. Live evals call
Gemini and bill the configured project. Start with one planning case, inspect
trace/token logs, then expand coverage.

Optional `CURBSIDE_ANALYTICS_DATASET` enables ADK BigQuery agent analytics and
captures plan status, revision count and audit result from event custom metadata.
Provision its dataset/table and runtime write permissions separately; do not give
the runtime direct access to either source dataset. Analytics logs contain audit
findings and require the same access discipline as the audit report.
