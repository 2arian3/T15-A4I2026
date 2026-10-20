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
[1] EVIDENCE (plain code)            writes: burden_findings, access_findings, context_findings
        |
        v
  +---> [2] PLANNER (LLM + tools)    reads: evidence, audit_report (if revising)
  |         |                        writes: plan_data, plan_summary
  |         v
  |     [3] EQUITY AUDITOR (LLM + audit tool)   reads: plan_data + demographics
  |         |                        writes: audit_data, audit_report, approved
  |         v
  |     [4] APPROVAL GATE (plain code)          reads: approved
  |         |-- REVISE (max 2-3 rounds) --------+
  |         |
  +---------+-- APPROVE
        |
        v
[5] HUMAN APPROVAL                   outreach lead approves or edits; edits are re-audited
        |
        v
  +---> [6] EXPLAINER (LLM)          reads: approved plan only
  |         |                        writes: flyer (EN, ES, text message)
  |         v
  |     [7] REVIEWER (LLM)           reads: flyer + plan text
  |         |                        writes: review_report
  +---------+-- REJECT (max 1-2 rounds)
        |
        v
CLINIC PLAN: calendar + what we couldn't tell you + audit numbers + flyer
```

| # | Step | Type | What it does | Fails safe by |
|---|---|---|---|---|
| 1 | Evidence | Plain code | Queries tract need, access gap and context. No model call. | Missing data is reported as "cannot tell," never as zero |
| 2 | Planner | LLM + `build_clinic_plan` tool | Picks four Saturday stops with a coverage tool and writes the calendar, each number tagged by scale | The tool does the math, so the model cannot invent stops |
| 3 | Equity Auditor | LLM + audit tool | The only agent that sees demographics the planner was forbidden to use. Flags stops too close together or tied, and reports numbers verbatim | Cannot change the verdict the tool returned |
| 4 | Approval gate | Plain code | Ends the loop only when the audit approves | Model text cannot override the audit |
| 5 | Human approval | Person | The outreach lead approves or edits the plan | Her edits go back through the audit |
| 6 | Explainer | LLM | Writes the flyer in English and Spanish, plus a text message, from the approved plan only | Sees only the approved plan; leaves `[CONFIRM]` for unverified service claims |
| 7 | Reviewer | LLM | Rejects claims that cross data scales or sound like medical advice | Its "hedge, cite, refuse" prompt conflicts with the Explainer's on purpose |

If a loop reaches its round limit, the system stops and hands the decision to a person. It does not pick a winner.

### Why no chat agent for residents
The challenge rules say an agent talking to a patient is the wrong thing to build, and an agent answering health questions would drift into medical advice. The flyer lists a human contact instead (marked `[CONFIRM]` until the county supplies it).

### Optional last stage: a coordinator for the outreach lead
After the core works, a thin coordinator at the top could route "plan next month" to the workflow above and route follow-ups ("why not the tract next to stop 2?", "what if I move stop 2?") to an answer step. It would answer only from saved state (`plan_data`, `audit_data`), have no new data access, and refuse anything outside it. It adds a routing step and more that can go wrong, so it is built last, and only if the plan and audit are solid.

### Build order (each stage works on its own)
1. Evidence, Planner, Equity Auditor and gate.
2. Explainer.
3. Reviewer on the flyer.
4. Human approval step.
5. Coordinator (optional).

## What she gets back (the Clinic Plan)
- **Where the van goes, and why:** four Saturdays, each with a named tract, people covered, and two or three numbers.
- **What we could not tell you:** each claim tagged tract, 12 km grid, facility point or multi-county region.
- **Who this plan lands on:** the audit numbers, reported whether or not they are comfortable.
- **The flyer** for the neighborhood.

## Data we use (all public, from the challenge's `a4i_health` dataset)
`burden_tracts` (health burden, insurance, vehicles), `tract_access` (distance to care), `care_sites` (clinic points and hours), `exposure_tracts` (air quality, context only), `disease_weekly` (regional context only). We do **not** rank on air quality (12 km grid) or claim neighborhood disease trends (regional data). Fulton has no live shortage areas (all 99 are proposed for withdrawal), which we report as a finding.

The auditor's demographic data is not in those tables. It needs a separate source, kept apart so only the auditor's tool can read it. The source is not yet chosen.

## Guardrails
- No personal or individual-level data, and no agent talking to patients.
- Race and ethnicity are never inputs. They are used only to audit the output.
- Distances are straight-line upper bounds from federal sites only.
- When data is missing, the system says "we cannot tell," never "no problem."
- A person approves the plan before the flyer is produced.

## Where we are
- Built: agent skeleton and tool logic on mock data, with offline tests (see `agent/README_BUILD.md`).
- Not built: real BigQuery loading, the audit data source, the approval step, MCP Toolbox, deployment, and a real Fulton run.
- Design notes and evidence: [`IDEA.md`](IDEA.md).
