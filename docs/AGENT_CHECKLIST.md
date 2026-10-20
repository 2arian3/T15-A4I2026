# Agent completion checklist

Use this once the agents are built, before the demo is recorded. Every item has a **how to check**
so anyone on the team can verify it, not only the person who built it. An item is done only when
someone other than its builder has checked it.

Based on: `README.md` (challenge rules and judging), `agent/README.md` (requirements),
`docs/IDEA_README.md` and `docs/IDEA.md` (our design) on the `idea` branch.

Judging weights, for prioritising: Impact 30, Technical execution 30, Rigor & judgment 25,
Craft & communication 15, Bonus range +10.

---

## 1. Hard requirements (missing any of these loses points outright)

- [ ] **ADK agent in Python.** How to check: `make web` starts and the root agent loads.
- [ ] **More than one agent, passing state.** How to check: in the `adk web` State tab, see keys
      written by one agent and read by another (see section 3).
- [ ] **At least two agents whose instructions conflict.** How to check: Explainer prompt says
      "plain, warm, no hedging"; Reviewer prompt says "hedge, cite, refuse". Paste both into the deck.
- [ ] **At least one tool we wrote ourselves** that holds real logic, not a single query
      (`build_clinic_plan`, the audit tool). How to check: the function is in `agent/` and has unit tests.
- [ ] **A Google-managed MCP server is consumed** (BigQuery's built-in MCP server or MCP Toolbox).
      We do not author our own. How to check: a trace shows an MCP tool call hitting BigQuery.
- [ ] **Deployed to Google Cloud** via `make deploy`. How to check: `make status` shows the agent,
      and one full plan request succeeds against the deployed endpoint.
- [ ] **Runs on real Fulton data, not mock data.** How to check: plan output names real Fulton
      tract IDs (FIPS starting `13121`).

## 2. Each agent does its job

### Evidence step (plain code, no model)
- [ ] Writes `burden_findings`, `access_findings`, `context_findings` as separate state keys.
- [ ] Missing data is reported as "cannot tell", never as `0`. How to check: run on a tract with a
      null value and read the output.
- [ ] Every figure carries its scale: tract, 12 km grid, facility point or multi-county region.

### Planner (LLM + `build_clinic_plan`)
- [ ] Never receives demographic data. How to check: grep the planner's tools and prompt for race or
      ethnicity columns; there should be none. Ideally the planner's tools cannot query the
      demographics table at all.
- [ ] Stops come from the tool, not the model. How to check: every tract in `plan_data` appears in
      the tool's output.
- [ ] Four Saturdays, four named tracts, each with people covered and two or three numbers.
- [ ] Stops are spread apart, not clustered.
- [ ] Tracts whose `_crude95ci` intervals overlap are treated as ties, not ranked.
- [ ] Measured inputs (uninsured, no vehicle, access gap) outweigh modeled PLACES prevalence.
- [ ] Uses the audit report when revising. How to check: plan v2 changes the stops the audit flagged.

### Equity Auditor (LLM + audit tool)
- [ ] The only agent that sees demographics. How to check: only the audit tool reads that table.
- [ ] Does not see the planner's reasoning, only `plan_data`.
- [ ] Compares the plan to the county **and** to a need-matched baseline.
- [ ] Reports numbers verbatim, including uncomfortable or "no difference" results.
- [ ] Cannot change the verdict its tool returned. How to check: the `approved` flag comes from the
      tool, not from model text.
- [ ] Demographic data source and license are written down.

### Approval gate (plain code)
- [ ] Ends the loop only when the audit approves.
- [ ] Hard round limit (2-3). At the limit it hands off to a person; it does not pick a winner.
- [ ] No loop can run forever. How to check: force the audit to always reject and confirm it stops.

### Human approval
- [ ] The outreach lead can approve or edit the plan.
- [ ] Her edits are re-audited. How to check: swap in a low-need tract (the "councilman" stop) and
      confirm the audit flags it.

### Explainer (LLM)
- [ ] Sees only the approved plan, never raw data.
- [ ] Produces English and Spanish flyers plus a text message.
- [ ] Reading level is measured by a tool (Flesch-Kincaid), not claimed.
- [ ] Unverified service details are left as `[CONFIRM]`.
- [ ] Lists a human contact, not a chat agent.

### Reviewer (LLM)
- [ ] Rejects claims that cross scales (e.g. "respiratory illness is rising in your neighborhood").
- [ ] Rejects anything that sounds like medical advice.
- [ ] Hard round limit (1-2), then hands off to a person.

## 3. The four judge questions (answer each in the deck or video, in these words)

- [ ] **Delete any one agent: does the answer get worse?** Run the system without the auditor and
      without the reviewer, and record what goes wrong. Bring numbers.
- [ ] **A state key written by one agent and read by another.** `plan_data`: written by the
      planner, read by the auditor. Screenshot from the `adk web` State tab.
- [ ] **Two agents whose instructions conflict.** Explainer versus Reviewer, both prompts shown.
- [ ] **The point where the system rejected its own first answer.** A real run where plan v1 fails
      the audit and plan v2 passes (or a human edit is rejected). Recorded, not described.

## 4. Rigor and honesty

- [ ] Nothing is ranked on air quality (12 km grid).
- [ ] No neighborhood claim from `disease_weekly` (multi-county region).
- [ ] Distances are described as straight-line upper bounds from federal sites only.
- [ ] Shortage areas: says Fulton has no live designations (all 99 proposed for withdrawal), as a finding.
- [ ] Does not claim the van improves health outcomes; claims only that it targets measured access gaps.
- [ ] Every number in the output is labelled measured or modeled.
- [ ] Validation WARN rows from the notebook were read and at least one is reported.
- [ ] The weights and the catchment radius are justified from Fulton data, not left as placeholders.
- [ ] (If built) Robustness check: which stops survive every reasonable weighting.

## 5. Safety and data rules

- [ ] No individual-level or personal data anywhere.
- [ ] Race and ethnicity are never planner inputs, only audit inputs.
- [ ] No agent talks to residents or answers health questions.
- [ ] Any data we added is license-clean (no NC, ND, share-alike, or unstated license).
- [ ] No secrets in the repo; logs contain no credentials.

## 6. The Clinic Plan output (all four parts present)

- [ ] **Where the van goes, and why:** four Saturdays, named tracts, reasons and numbers.
- [ ] **What we could not tell you:** limits named and counted, each claim tagged with its scale.
- [ ] **Who this plan lands on:** the audit numbers.
- [ ] **The flyer:** English, Spanish and a text message.

## 7. Tests and deployment

- [ ] `make test` passes.
- [ ] `uv run pytest tests/unit tests/integration` passes.
- [ ] At least one eval case per judge question runs in `agents-cli eval run`.
- [ ] `make deploy` succeeds and the deployed agent answers a full plan request.

## 8. Submission

- [ ] Repo link, Google Skills project link, pitch deck, pitch video uploaded to the portal.
- [ ] Deck states the track (Siting) early.
- [ ] Deck opens with one surprising number from our data (e.g. 41% of tracts have a safety-net gap).
- [ ] The sentence "the rule against using race forces the architecture" is said in the video.
- [ ] One honest limitations line.
- [ ] Demo rehearsed, timed, and backed up with screenshots.
