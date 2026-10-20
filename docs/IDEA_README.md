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
evidence (plain code) -> Planner -> Equity Auditor -> approval gate
                           ^______ revise (max 2-3 rounds) ____|
                                         |
                              [outreach lead approves]
                                         |
                              Explainer -> Reviewer -> flyer
```

| Step | What happens | Why |
|---|---|---|
| Evidence | Plain Python queries tract need, access gap and context. No model call. | Facts should not depend on a model's mood. |
| Planner (LLM) | Picks four Saturday stops with a coverage tool, and writes the calendar with each number tagged by scale. | Needs judgment to turn a ranked list into a plan. |
| Equity Auditor (LLM + tool) | The only agent that sees demographics the planner was forbidden to use. Reports the numbers verbatim and flags stops too close together or tied. | Removing race as an input does not remove bias. Auditing the output does. |
| Approval gate (plain code) | Ends the loop only when the audit approves. | Model text cannot override the audit. |
| Explainer (LLM) | Writes the flyer in English and Spanish, plus a text message, from the approved plan only. | Residents need plain language, not hedging. |
| Reviewer (LLM) | Rejects flyer or plan claims that cross data scales or sound like medical advice. | Its instructions (hedge, cite, refuse) conflict with the Explainer's on purpose. |

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
