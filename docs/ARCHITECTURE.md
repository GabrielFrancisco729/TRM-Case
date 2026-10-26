# System architecture & implementation notes

## Data and request path

```text
14 original CSVs (read only)
          |
  core.py / advanced.py  -- reproducible metrics + score evidence + diagnostic checks
          |
  FastAPI server.py ----- role-restricted REST endpoints ----- static HTML/CSS/JS
          |                                       |
          |                                AD queue + manager workbench
          |
  SQLite local ledger ---------------------------------------------+
      | actions (append only)        | qualification_reviews (append only)
      | scoring_config (append only) | seen_mqls + notification_outbox
      +-----------------------------+------------------------------+
                                     |
                             SlackAdapter (simulated/live)

SalesforceAdapter: REST scaffold, no automatic production read/write sync
AI assistant: optional, on explicit manager click, recommendation only
```

## Sources of truth

Historical funnel counts and SLA decisions use source CSV timestamps (UTC), never a count inferred from local actions. Pandas CSV loading preserves the region string `NA` (North America). A seller activity belongs to an MQL only when its contact/time falls after that MQL and before the next MQL for the same contact. Five business-day SLAs exclude weekends; regional holidays cannot be calculated from the package. The data was pulled 2026-10-08, so recency and stale-opportunity checks use the **extract date**, not the current calendar date.

## Funnel interpretation and quality safeguards

The 173-event Q2→Q3 bridge is reconciled from forms, MQL events, source imports and unlinked submission IDs. The 32 additional orphan forms share missing account associations, but an exact qualification-rule change requires marketing automation logs. Case hypotheses are explicitly distinct from evidence.

Operational categories have a mutually exclusive **priority**: (1) reported worked in SLA; (2) seller activity in SLA but no counted status transition; (3) existing customer; (4) system queue/unassigned; (5) no verified successful delivery; (6) owner inactive at extract; (7) late automated conversion; (8) unexplained no action. These categories are *descriptive priority bins*, not an experiment or proof of causal responsibility. Other concurrent failure signals may exist.

`risk_checks()` detects missed v2 MQL links, other orphans, fallback routing, undelivered notifications, inactive owner associations, stale opportunity dates, cross-account primary contacts and invalid `product_interest`. A zero count means no violation detected in that file, not that upstream systems are trustworthy. No check silently fixes the original CSVs.

## Queue & qualification

`evidence_score()` gives explainable points for observable, verifiable attributes. It only scores active opportunities where `contact_id` joins to the same `account_id` and the `close_date` is not before the extract. Unmatched account, personal email and absent title are *review signals*, not categorical grounds for rejection. Lanes distinguish hand-raisers, CS review, current ICP fit, low-signal nurture and aged candidates. Marketing Ops changes weights via validated integer bounds and an append-only config history; Undo creates another row restoring an earlier weight set.

`qualification_suggestion_row()` provides a **deterministic** draft: hand-raisers go straight to AD, customers are flagged to consider CS, missing account matches need enrichment and other leads remain AD-eligible. Manager-reviewed outcomes are appended to `qualification_reviews`. Locally, approved CS/enrichment/nurture cases are excluded from an AD's default active queue, and AD mutation attempts on these leads are rejected until the manager re-approves AD routing. The export records approvals, but it is not evidence of Salesforce or CS task completion.

A stable hashed ≈20% control flag is exposed to support an eventual controlled rollout. **No true experiment has been run.** Seller reject/accept statuses are selective, intervention-contaminated training labels: the qualification backtest reports only conditional historical seller decisions and must not be used to authorize auto-disqualification.

## AI boundaries

`ai_assist.py` is disabled by default. When `ANTHROPIC_API_KEY` and `LEADLOOP_LLM_MODEL` are configured, only a manager **explicitly clicking** a suggestion button can invoke the model. Fields sent are form type, fit, customer status, seniority, title, segment, region and priority — no contact email or identity. The response is checked for allowed dispositions and JSON structure, and contradictory hand-raiser recommendations are overridden by deterministic policy. It can only prefill an editable suggestion. A human must submit a separate reasoned approval to write local state. The model's self-reported confidence is not calibrated.

## Slack integration

The local outbox inserts idempotent keys for new MQLs and each explicit reassignment, avoiding duplicate sends on normal retries. A worker claims records with a processing lease, retries temporary failures with capped exponential backoff, respects `Retry-After` on rate limits and preserves final delivery status/error. At boot, historical CSV IDs are marked seen to prevent flooding a channel. No message is sent outside the laptop without supplied credentials.

**Production design:** durable hosted DB + dedicated worker, secure OAuth/bot secrets, channel validation, alert on dead-letter backlog and delayed events, a human escalation policy and a delivery latency SLA. Real polling/sync for Salesforce source IDs also requires a change-data-capture or delta-query cursor, rather than manually replacing CSVs.

## Salesforce integration boundary

`SalesforceAdapter` contains REST/SOQL paging and write primitives. A real implementation needs tenant-approved OAuth and scopes, tenant-specific schema, contact/MQL ID mapping, row-level owner checks, idempotent Task creation, async retries, write conflict resolution, user identity mapping, change capture, and reconciliation dashboards. No live writes are enabled. A selected AD in the demo is not a production authorization mechanism.

## Monitoring & rollout

Measure capture integrity of eligible high-intent forms, assigned ownership within 15 minutes, successful alert delivery within SLA, seller-activity evidence, time-to-first-human-action, reason-coded qualification coverage, SQL/opportunity creation and CS handoff completion. Avoid optimizing the original worked metric alone because status automation can inflate it. Release a qualification step to a small team with stable control comparison; monitor false-negative cost, disposition reversals and changes in source mix. Set freshness, completeness, outbox backlog, malformed opportunity and invalid-form-ID alerts; define an owner for each failure.

## Four operating-process changes

1. **Unified worked definition:** retain the historical SLA/status KPI while separately tracking first attributable seller activity. Align the reporting contract between Sales and Marketing.
2. **Governed rejection reasons:** reason codes are manager-controlled and audited. Require a note; review rejection mix and reversals jointly with Marketing.
3. **Failed-notification alerting:** retry unsuccessful Slack sends, surface aged/terminal failures, and escalate to an owner if delivery is not restored. The demo implements retries; production escalation needs a hosted scheduler/on-call route.
4. **Owner revalidation after territory changes:** periodically recheck active territory ownership and create a re-assignment + notification when necessary. The historical data and adapter establish the integration boundary; continuous territory CDC is a production follow-up.

CMS-to-MQL reconciliation, CS/enrichment review and Sales–Marketing feedback are complementary operating improvements, not substitutes for those four controls.

## Explicit exclusions

No production SSO, calibrated conversion model, automated rejection, proven SDR historical staffing change, verified outreach API or complete SF/Slack connected deployment exists. These are deliberate limits and honest follow-up requirements.


## Diagnostic metric definitions (rubric review)

- **−27.0%**: MQL *events* 640 → 467. This combines a Q2-only import, unmatched forms, and raw form-volume change; it is not automatically lost demand.
- **9% vs. 91% bridge:** of the **173** fewer MQLs, **16 (9.2%)** reflect the net fall in raw form volume, and **157 (90.8%)** reconcile to the non-repeated event import (77) and unmatched forms (48 + 32). Those 157 are not all verified defects: the import is a known one-off and eligibility for each unlinked form needs upstream confirmation.
- **−17.1% comparable MQL baseline:** form-generated MQL events **563 → 467** after excluding the Q2-only badge import. Separately, organic-search source-attributed form submissions were **123 → 113**, and organic + direct were **219 → 213**.
- **Paid social (supporting context):** attributed forms Jul/Aug/Sep = **33 / 2 / 18**; the August 3 budget reallocation is logged. This channel trend is separate from the **9% = 16 / 173** decomposition.
- **25.1% reported worked:** **117 / 467** under the status-transition-in-SLA rule. **51.0% adjusted action proxy:** **157 / 308** after excluding 159 first-match ownership/alert risk cases; **59.9% stricter proxy:** **157 / 262** when existing customers (38) and late automated status (8) are also removed. No proxy claims verified conversations.
- **14** automatic conversion status events and **9** contacts with 3+ Q3 MQLs are anomaly flags, not leads that should be removed automatically.

## Business-operated reason taxonomy

Marketing Ops edits a bounded list of mandatory rejection/return categories in **Priority rules**, with reason and audit history, and can undo the latest configuration. API routes: `GET /api/manager/rules`, `POST /api/manager/reason-codes`, `POST /api/manager/reason-codes/undo`; AD receives `GET /api/reason-codes`. The server validates against the current manager-approved list. Category changes do **not** rewrite prior reasons in the immutable action ledger.


## AD-facing intelligence & UX (current demo)

`ad_experience.py` builds three evidence-backed views: `next_action_rule` (a human-reviewed recommendation), `journey_for_lead` (per-MQL evidence checkpoints), and `ad_metrics` (individual activity within the event response window, separated from historical status-worked). The additional routes are `GET /api/ad/metrics` and `POST /api/ad/next-action`, with AD-scoped session authorization. Lead details include `journey` and `next_action` in the same response.

When the AD explicitly asks for a Claude suggestion, `ai_assist.next_best_action` sends only minimized structured attributes (not identity or email). The LLM response is JSON-validated, the action type cannot override the deterministic policy, and any proposed email copy remains an unsent draft. The existing local action endpoint remains the only AD mutation path. The frontend uses native JavaScript, CSS and the existing FastAPI server; no additional packages were introduced.

The **Workflow** tab explains the intended current end-to-end flow. The **Next steps** tab is purely prospective: no SDR automation, data enrichment or autonomous outbound sending is implemented. The visual palette is independently authored and draws inspiration from the navy/cyan investigative aesthetic on TRM Labs' public website; it is not an official TRM product or TRM-branded asset.
