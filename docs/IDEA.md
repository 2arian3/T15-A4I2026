# Idea: a van-siting system that audits and overrules itself

**County:** Fulton GA. **Track:** Siting (with the audit and explanation tracks built in).

> Our agent helps the Fulton County outreach lead decide where to park one mobile clinic on four Saturdays, and the thing it knows that a spreadsheet does not is **which of its own claims hold at tract scale and which do not**, because a second agent that never saw the planner's reasoning audits every plan.

## The one-paragraph idea
A planner agent picks four Saturday stops from measured need and access data, with no race input. An independent equity auditor then checks that plan against demographics the planner never saw. A reviewer rejects any claim that crosses data scales. The planner revises until the deterministic audit approves or the loop ends. Only the approved plan reaches the explainer, which writes a plain-language English and Spanish flyer. Every number carries the scale it came from.

---

## Step-by-step reasoning

### 1. Scope: place-based, no individuals
**Choice:** Plan where the van goes, never who to treat.
**Why:** The event prohibits individual-level data. It also keeps us out of clinical advice, which the README says is a line "that has no version that scores well."

### 2. Evidence gathering: three parallel agents, three state keys
**Choice:** `burden_agent`, `access_agent`, `context_agent`, each writing a distinct `output_key`.
**Why:** The three lookups are independent, so running them in parallel costs one step instead of three. ADK parallel branches share one state dict, and the README warns that shared keys fail silently. Distinct keys prevent that.
**Why the third agent exists:** its job is to say what air quality and the regional respiratory trend *cannot* tell us. The PM2.5 data is a 12 km grid and the disease trend is a multi-county region, so neither can rank neighborhoods.

### 3. Need index: measured data leads, modeled data is a tiebreaker
**Choice:** Weight measured inputs (uninsured share, no-vehicle share, access gap) above modeled ones (PLACES chronic-disease prevalence). Treat tracts whose scores are inside each other's margin as ties.
**Why:**
- CDC's PLACES FAQ says smaller areas have broader confidence intervals. It says comparing point estimates alone is inadequate. It also says that because the estimates are modeled, they "should not be used to rank" tracts. Our own ranking therefore leans on measured inputs.
- Transportation matters as an input. An Urban Institute analysis reported that about 21% of adults without a vehicle or public transit went without needed care, against about 5% nationally (survey-based, observational).
- **Change needed in the current code:** `config.py` weights modeled chronic burden at 25%. Lower it (suggest 10 to 15%) and use the `_crude95ci` columns to define ties instead of a fixed margin.

### 4. Stop selection: maximum coverage with an equity weight, not a heat map
**Choice:** Greedy maximum coverage. Each stop maximizes uncovered, need-weighted population within a catchment, and later stops must be spread apart.
**Why:**
- A 2025 medRxiv preprint on mobile clinic placement reports that sites are often chosen for convenience rather than systematic analysis, and that fixed-site models favor dense urban areas. A coverage model addresses both.
- An equity-weighted coverage proposal for rural clinics maximizes coverage weighted by households without vehicles. That is our weighting idea, applied to a fixed-clinic problem.
- **Optional upgrade:** two-step floating catchment area (2SFCA), which discounts a tract's need by nearby supply. It was built for primary care access. An enhanced version adds distance decay. For us, supply could be safety-net sites weighted by `TOT_OPER_HR_PER_WEEK`.
- **Caveat:** the catchment radius (3 km) is a placeholder. Choose it from your own data and say so.

### 5. Equity audit: a separate agent that sees what the planner could not
**Choice:** The auditor has a tool the planner lacks and compares the plan to the county **and to a need-matched baseline** (equally needy tracts).
**Why:**
- Removing a sensitive column does not remove bias. Correlated features carry it (fairness literature on unawareness).
- Auditing therefore needs the demographic data, used for assessment only. That matches the README's rule.
- The need-matched baseline separates "the skew is explained by need" (disclose it) from "a proxy may be at work" (revise).
- **Caveat:** the fairness literature notes that imputing protected class from proxies can distort disparity estimates. We would use actual tract-level data, so we avoid imputation. The data source is still unverified.

### 6. Reviewer: an independent critic with a conflicting prompt
**Choice:** A reviewer whose whole prompt is "hedge, cite, refuse." It rejects scale-crossing claims.
**Why:**
- A model critiquing its own output in the same context tends to defend it. The evidence is indirect: Huang et al. (2023) found models often struggle to self-correct without external feedback, and Stechly et al. (2024) found independent external verification can significantly improve reasoning accuracy. I did not find a head-to-head test of same-context versus separate critic.
- SycEval (Stanford, 2025) reported sycophantic behavior in 58.19% of cases across three major models, which is the failure the separate critic is meant to catch.
- A non-LLM approval gate decides when the loop stops, so model text cannot override the audit.

### 7. Explainer: plain language, no hedging
**Choice:** A separate agent for the resident-facing flyer, English and Spanish, with a text-message version.
**Why:**
- Health materials are often written above the 10th-grade level (CHCS). A 6th-to-8th-grade target is widely cited but comes from secondary reporting, so verify it on cdc.gov before putting it on a slide.
- Its instructions directly contradict the reviewer's, which is the "two agents with conflicting prompts" judges look for.
- **Add:** a Flesch-Kincaid check tool so the reading level is measured, not claimed.

### 8. Honest limits are part of the product
- Distances are straight-line upper bounds from federal sites only.
- PM2.5 cannot separate tracts. The disease trend is regional.
- Fulton has no live shortage areas (all 99 are proposed for withdrawal).
- **Evidence for mobile clinics is thin.** A Cochrane review found only two US studies, with low-certainty evidence and no access outcomes. Do not claim the van "works." Claim only that the plan targets measured access gaps.

---

## Agents at a glance

| Agent | Sees | Forbidden | Writes | Prompt style |
|---|---|---|---|---|
| burden / access / context | its own table summary | demographics | `burden_findings`, `access_findings`, `context_findings` | factual, scale-tagged |
| planner | evidence + prior audit/review | demographics | `plan_data`, `plan_summary` | decisive |
| equity auditor | plan + demographics | planner's reasoning | `audit_data`, `audit_report` | adversarial, report-verbatim |
| reviewer | plan + audit | n/a | `review_report` | hedge, cite, refuse |
| approval gate (non-LLM) | `approved` flag | n/a | loop escalate | deterministic |
| explainer | approved plan only | raw data | `flyer` | warm, plain, no hedging |

## The four judge questions
1. **Delete one agent:** drop the auditor and a plan with clustered or tied stops goes out unchecked (show before and after).
2. **State key written then read:** `plan_data` is written by the planner and read by the auditor.
3. **Conflicting prompts:** the reviewer ("hedge") versus the explainer ("no hedging").
4. **Rejected its own first answer:** `plan_v1` fails the audit, then `plan_v2` passes. **Not yet shown live:** on mock data the first plan passes, so we need a real Fulton run that triggers a revision.

## Open items to verify
- The source and license of the tract-level demographic data for the auditor.
- The CDC reading-level guidance, on cdc.gov.
- That the catchment radius and index weights are defensible against Fulton data.
- Whether a real Fulton run triggers a revision.

## Sources
- [Optimizing Mobile Health Clinic Placement via Geospatial Modeling (medRxiv preprint, 2025)](https://www.medrxiv.org/content/10.64898/2025.12.29.25342286v1)
- [Equity in Stochastic Healthcare Facility Location (arXiv)](https://arxiv.org/pdf/2112.03760)
- [Two-step floating catchment area method (Wikipedia)](https://en.wikipedia.org/wiki/Two-step_floating_catchment_area_method)
- [McGrail 2012, 2SFCA improvements (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3520708/)
- [Fairness in AI: challenges in bridging the gap between algorithms and law (arXiv)](https://arxiv.org/pdf/2404.19371)
- [Fairness Under Unawareness: Assessing Disparity When Protected Class Is Unobserved (arXiv)](https://arxiv.org/pdf/1811.11154)
- [RealCritic: evaluating LM critiques (arXiv)](https://arxiv.org/pdf/2501.14492)
- [SycEval: Evaluating LLM Sycophancy](https://www.alphaxiv.org/abs/2502.08177v4)
- [CDC PLACES: using the data (FAQ)](https://www.cdc.gov/places/faqs/using-data/index.html)
- [CDC PLACES methodology](https://www.cdc.gov/places/methodology/index.html)
- [CHCS: Improving Written Communication to Promote Health Literacy](https://www.chcs.org/resource/improving-written-communication-to-promote-health-literacy/)
- [Healthcare Dive: transportation and missed care](https://www.healthcaredive.com/news/without-vehicle-good-public-transportation-miss-healthcare-appointment/648931/)
- [Cochrane review: mobile clinics for women's and children's health](https://cochranelibrary.com/cdsr/doi/10.1002/14651858.CD009677.pub2)
