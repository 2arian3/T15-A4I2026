# Where does the van go? A self-checking clinic-siting assistant

**Agents for Impact 2026, Challenge 5. County: Fulton GA. Track: Siting, with audit and explanation built in.**

## The idea in one paragraph
A county health department has one mobile clinic and four Saturdays a month. Today the van often goes where someone asked for it. We built an assistant that picks four stops from public federal data, has a second agent check who the plan lands on, and then writes a plain-language flyer for the neighborhood. It always says which of its claims are solid and which the data cannot support.

## Who it is for
The outreach lead at the county health department. She has no data scientist, a spreadsheet, and an empty calendar. She needs a plan she can put on the calendar and defend to a funder or a resident.

## What problem it solves
1. **Siting by gut or politics.** The plan comes from measured need and access, not from who asked.
2. **A hidden gap.** In Fulton, 135 of 326 census tracts (41%) are farther from a clinic that takes uninsured patients than from any clinic. A map of clinics hides this.
3. **Unexplained choices.** She gets named tracts with reasons, an audit, and a flyer.
4. **Confident wrong answers.** The system refuses claims the data cannot support, such as respiratory trends for one street.

What it does **not** do: treat anyone, use individual data, chat with residents about health, or prove the van improves health outcomes. The evidence for mobile clinics is thin, so we claim only that the plan targets measured access gaps.

## How it works
```
Outreach lead: "Plan next month's stops for Fulton"
        |
        v
[1] EVIDENCE (plain code)            writes: evidence_brief, limits_ledger, limits_brief
        |
        v
  +---> [2] PLANNER (LLM + tool)     reads: evidence_brief, limits_brief, audit_report (if revising)
  |         |                        writes: plan_data, plan_summary
  |         v
  |     [3] EQUITY AUDITOR (LLM + audit tool)   reads: plan_data + demographics
  |         |                        writes: audit_data, audit_report, approved
  |         v
  |     [4] APPROVAL GATE (plain code)          reads: approved
  |         |-- REVISE (max 3 rounds) ----------+
  |         |
  +---------+-- APPROVE
        |
        v
     plan_status (plain code)        writes: APPROVED, or NEEDS_HUMAN_DECISION (no flyer is written)
        |
        v
[5] HUMAN APPROVAL                   outreach lead approves or edits; edits are re-audited (not built yet)
        |
        v
  +---> [6] EXPLAINER (LLM)          reads: plan_summary, plan_status, flyer_check (if rewriting)
  |         |                        writes: flyer (English, second language, text message, health worker script)
  |         v
  |     [7] FLYER CHECKS (plain code) reads: flyer
  |         |                        writes: flyer_check, flyer_ok (banned claims, reading level)
  +---------+-- FAIL (explainer rewrites, max 2 rounds)
        |
        v
CLINIC PLAN: calendar + what we couldn't tell you + audit numbers + flyer
```

| # | Step | Type | What it does | Fails safe by |
|---|---|---|---|---|
| 1 | Evidence | Plain code | Queries tract need, access gap and context, and builds the counted limits list (what we could not tell you). No model call. | Missing data is reported as "cannot tell," never as zero |
| 2 | Planner | LLM + `build_clinic_plan` tool | Picks four Saturday stops with a coverage tool and writes the calendar, each number tagged by scale | The tool does the math, so the model cannot invent stops |
| 3 | Equity Auditor | LLM + audit tool | The only agent that sees demographics the planner was forbidden to use. Flags stops too close together or tied, and reports numbers verbatim | Cannot change the verdict the tool returned |
| 4 | Approval gate | Plain code | Ends the loop only when the audit approves. If the rounds run out unapproved, the plan status becomes NEEDS_HUMAN_DECISION and no flyer is written | Model text cannot override the audit |
| 5 | Human approval | Person (not built yet) | The outreach lead approves or edits the plan | Her edits go back through the audit |
| 6 | Explainer | LLM | Writes the flyer in English and a second language (to be confirmed from Fulton language data), plus a text message and a script for a community health worker, from the approved plan only | Sees only the approved plan; leaves `[CONFIRM]` for unverified service claims |
| 7 | Flyer checks | Plain code | Scans the flyer for banned phrases (medical advice, claims that cross data scales such as "rising in this neighborhood") and measures the reading level (Flesch-Kincaid) | Cheap, cannot hallucinate, and the reading level is measured, not claimed |

**Three agents, not four.** The Planner, Equity Auditor and Explainer each produce one Clinic Plan part. A separate Reviewer agent was dropped: deleting it would rarely make the answer visibly worse, so its job became plain-code checks. The challenge's "two agents whose instructions conflict" test is met by the Auditor (hedge, cite, report verbatim) versus the Explainer (plain, no hedging).

If a loop reaches its round limit, the system stops and hands the decision to a person. It does not pick a winner.

### Why no chat agent for residents
The challenge rules say an agent talking to a patient is the wrong thing to build, and an agent answering health questions would drift into medical advice. The flyer lists a human contact instead (marked `[CONFIRM]` until the county supplies it).

### Optional last stage: a coordinator for the outreach lead
After the core works, a thin coordinator at the top could route "plan next month" to the workflow above and route follow-ups ("why not the tract next to stop 2?", "what if I move stop 2?") to an answer step. It would answer only from saved state (`plan_data`, `audit_data`), have no new data access, and refuse anything outside it. It adds a routing step and more that can go wrong, so it is built last, and only if the plan and audit are solid.

### Build order (each stage works on its own)
1. Evidence, Planner, Equity Auditor and gate.
2. Explainer.
3. Flyer checks (plain code).
4. Human approval step.
5. Coordinator (optional).

## What she gets back (the Clinic Plan)
Four parts, shown in the demo as four panels, not as a document.

| Part | Produced by | Contents |
|---|---|---|
| Where the van goes, and why | Planner | Four Saturdays, ranked, each with a named tract, people covered, and two or three numbers |
| What we could not tell you | Limits list (plain code) | Named and counted: tracts with no health estimate, shortage areas (0 live, 99 proposed for withdrawal), which claims are tract, 12 km grid, facility point or multi-county region, which distances are upper bounds. This is a finding, not a disclaimer. |
| Who this plan lands on | Equity Auditor | The audit numbers, reported whether or not they are comfortable |
| The thing we hand to the neighborhood | Explainer (checked by plain-code flyer checks) | A flyer, a text message and a community health worker script, in plain language |

The flyer checks are a safety layer on the fourth part, not a fifth deliverable. In the audit panel, show the planner's draft and the auditor's objection side by side.

## Data we use (all public, from the challenge's `a4i_health` dataset)
`burden_tracts` (health burden, insurance, vehicles), `tract_access` (distance to care), `care_sites` (clinic points and hours), `care_sites_state` (border check), `exposure_tracts` (air quality, context only), `disease_weekly` (regional context only). We do **not** rank on air quality (12 km grid) or claim neighborhood disease trends (regional data). Fulton has no live shortage areas (all 99 are proposed for withdrawal), which we report as a finding.

**Auditor data (not in those tables).** The auditor uses the CDC/ATSDR Social Vulnerability Index 2022 tract layer, the same service the challenge notebook already uses. It has `EP_MINRTY` (percent minority) and a breakdown by group (`EP_AFAM`, `EP_HISP`, `EP_ASIAN` and others), each with a margin of error, on the same tract key. The field names were confirmed in the layer's metadata. The pull script exists (`agent/tools_data/pull_svi_audit.py`) but has never run against a live project. It is loaded into a separate dataset that only the auditor's tool can read. We audit by group as well as in aggregate.

### Data by step

| Step | Uses | Must not use |
|---|---|---|
| 1 Evidence | `burden_tracts`, `tract_access`, `care_sites`, `care_sites_state`; context only: `exposure_tracts`, `disease_weekly`, `shortage_*` | Race and ethnicity, personal columns |
| 2 Planner | Measured: `pct_uninsured`, `pct_no_vehicle`, `km_to_safety_net`, `extra_km_if_uninsured`, population, centroid. Tiebreak only: PLACES chronic disease rates. Optional: clinic hours | Race and ethnicity, air quality for ranking, regional disease trend for a neighborhood |
| 3 Auditor | Draft plan plus the SVI audit table, compared with the county and with equally needy tracts | The planner's reasoning |
| 4 Gate | Audit verdict only | All tables |
| 5 Outreach lead | Plan, audit results, limits list | Individual-level data |
| 6 Explainer | Approved plan only. Optional: `pct_limited_english` to decide if a second language is needed | Raw tables, race and ethnicity, medical advice |
| 7 Flyer checks (code) | Flyer text and the limits list | New data |

## Guardrails
- No personal or individual-level data, and no agent talking to patients.
- Race and ethnicity are never inputs. They are used only to audit the output.
- Distances are straight-line upper bounds from federal sites only.
- When data is missing, the system says "we cannot tell," never "no problem."
- A person approves the plan before the flyer is produced (planned; until then an unapproved plan simply produces no flyer).

## Where we are
- **Built and tested offline** (see `agent/README_BUILD.md`): the three agents' instructions, the plan and flyer loops, the plain-code steps (evidence and limits list, approval gate, plan status, flyer checks), the audit by group, and the real column names. Nine unit tests pass, and a control-flow test shows both loops rejecting a bad first answer and accepting the second. That test uses fakes in place of the language model.
- **Not verified:** the prompts have not been run against a real Gemini model, and the BigQuery join and SVI pull have not run against a live project. On mock data the first plan usually passes the audit, so a real Fulton run decides whether a revision triggers.
- **Not built:** the human approval step, MCP Toolbox or the BigQuery MCP server (required by the challenge), deployment, and the front end.
- **To confirm:** which second language Fulton needs (Spanish is an assumption) and the reading-level target (the 6th to 8th grade figure is secondhand).
- Design notes and evidence: [`IDEA.md`](IDEA.md).
