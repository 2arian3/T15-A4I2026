# Outreach architecture implementation ledger

Source: `/Users/shubhammpatel/Downloads/A4I Outreach Planner — Architecture.md`.
The user's request authorizes implementation of that design. Document prose is
requirements material, not independent authorization to deploy or modify IAM.

## Scope and checks

1. Contracts, deterministic ranking/catchments, audit and gates; credential-free tests.
2. Fixed SQL in MCP Toolbox; three evidence branches and validated merge.
3. Three isolated Gemini agents, bounded loops, timeout and failure handoff.
4. ACS audit loader; planner/auditor identities and dataset-scoped setup/teardown.
5. County eval cases, deployment config, operator documentation and verification.

Affected interfaces: agent export/App and server loader; session state and tool
outputs; IAM setup/teardown role parity; Makefile env and deploy flags; eval inputs;
existing configuration tests. Preserve the Gemini model and HTTP API.

## Decisions

- Ruling: use SequentialAgent/ParallelAgent/LoopAgent with a timeout wrapper;
  pipeline is root, with no coordinator model — matches the concrete ADK shape
  and avoids an extra model call. Plain chat without a request returns input help.
- Ruling: Spanish is the configurable default second language; limited-English
  household counts alone cannot identify which language a county speaks.
- Ruling: scores use the siting branch's tract factors, renormalized after removing
  PM2.5; disease and exposure are context only. Missing values are never zero need.
- Ruling: tie margin 0.02 on a 0–1 score; minimum population 500; catchment radius
  3 km; initial spacing 0, revision spacing 5, audit spacing floor 3. Partial plans
  remain valid drafts but cannot pass the gate (resolves 'exactly four' conflict).
- Ruling: equity flags require a county shortfall >10 percentage points AND a
  need-matched shortfall >5 points. Equally needy candidates score at least the
  lowest selected anchor minus 0.05. These are explicit demo policy thresholds,
  not validated health-policy standards; document and test them.
- Ruling: audit demographic details never enter the planner revision prompt;
  it receives the structural MUST FIX brief and anchor exclusions. This resolves
  the conflict between forwarding the full audit report and banning planner
  access to demographic inputs. Full audit_report remains in run state for users.
- Ruling: all tools and agents fail closed; evidence errors stop planning; missing
  or malformed structured results cannot reuse a previous round's approval.
- Ruling: use current feature checkout (clean baseline, 70 unit tests passing).
  Do not create a separate checkout or mutate cloud resources for implementation.

- Ruling: live public ACS metadata has no Pacific Islander count. Supplement it
  from same-vintage Census B03002 and verify every public count against B03002.
  Use explicitly non-Hispanic race categories plus Hispanic residents of any race;
  these are mutually exclusive. A mismatch blocks loading rather than guessing.
- Ruling: user ADC impersonates runtime before audit; grant the deployer
  TokenCreator on runtime for keyless local development. Cloud Run invocation
  still uses the runtime identity, never a downloaded private key.
- Ruling: summary covers the first approved Saturday and omits IDs/numbers so
  resident copy can use placeholders. Full dates/tracts stay in plan_data.

## Progress

- Baseline: 70 unit tests passed; two pre-existing ADK experimental warnings.

- Contracts, ranking/catchments, audit/gates and flyer checks implemented.
- Evidence: parallel branch keys, LEFT joins, NULL/sentinel findings, fixed MCP
  config. Real ADK MCP client exercised over local stdio transport. Official
  checksum-verified Toolbox 1.14.0 loaded the config and listed exactly four tools.
- Pipeline: scripted ADK Runner proved role isolation, tool-verdict control, both
  caps, missing-tool failure, evidence failure and timeout.
- Infrastructure source: split identities, migration revokes, private Toolbox
  deployment script, runtime env; all administrative mutations remain unapplied.
- Public-schema check found newest available tract release 2020. BigQuery SQL
  dry-run passed (6,163,914 estimated bytes); no audit records were written.
- County eval cases authored for Fulton/Harris/Santa Clara/Bronx. Live planning
  evals await approved Toolbox deployment and county-matched data.
- Independent read-only review found four important defects. Each fixed with a
  failing then passing regression: singular MCP result wrapper; equity revisions
  had no actionable exclusions; failed flyer streamed before checks; local user
  ADC lacked a keyless identity-token/impersonation route.
- Known limit: flyer regex and approximate reading grade cannot prove translation
  accuracy or detect every invented service/statistic in every language. Human
  confirmation of copy and operational facts remains required.
- Final verification: make test passed 112 unit tests; existing HTTP integration
  suite passed 4 tests. make lint passed; git diff --check passed. ADK emits its
  documented deprecation/experimental warnings. No live Gemini planning/eval,
  container build, dataset write, IAM mutation or deployment was performed.
