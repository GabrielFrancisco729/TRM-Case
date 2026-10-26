# Quillon LeadLoop

**GitHub and Render reviewer deployment:** see [Render deployment guide](docs/DEPLOY_RENDER.md).


**An investigation-to-action workspace for the fictional Quillon Labs marketing funnel.**

The application uses all **14 provided CSV files** (Q2–Q3 2026). A guided investigation explains what the evidence supports; an AD can then open, act on, and reassign MQLs, while Marketing Ops can audit feedback, review qualification candidates and adjust prioritization rules. It is a **local, executable prototype**, not a connected production CRM.

## Run locally

**Windows 10/11:** Extract this ZIP completely to a writable folder. Double-click **`ABRIR_LEADLOOP.cmd`**, approve Python installation if requested, and open **http://127.0.0.1:7860**. The launcher finds compatible Python 3.10–3.13, creates `.venv`, installs `requirements.txt` when required, and keeps a readable startup log at `logs/startup_last.log` (generated at runtime). Leave the terminal open while using the app.

**macOS/Linux:** Install Python 3.10–3.13, then run `bash start_mac_linux.sh` inside the project folder. A Windows-specific Python environment is *not* included in the ZIP.

**Manual start:**

```bash
python -m venv .venv
# Activate .venv, then:
python -m pip install -r requirements.txt
python server.py
```

**Demo roles:** After finishing the investigation, select an AD by name, or choose Marketing / RevOps Manager (demo PIN: `quillon-demo`). The identity chooser and PIN are **not production authentication**. For a hosted reviewer demonstration, see `docs/DEPLOY_RENDER.md`; the selectable AD role and demo PIN are not production authentication.

## Suggested demonstration route

| Screen | What to demonstrate |
|---|---|
| Investigation | Reveal the drop and reconcile all 173 missing MQL events, then investigate adoption by eight mutually exclusive evidence groups. |
| AD workspace | Select an AD, filter by quarter, state or workstream; show the score explanation, evidence flags and lead history by clicking a row. |
| Lead actions | Log outreach, change status, reassign to another active AD in the territory, flag a data issue, or return a lead to Marketing for human review. Disqualification and Marketing returns require both a structured reason category and a note. |
| Contextual next action | The lead drawer shows a deterministic next-action recommendation at the top. An AD may click to request a read-only Claude suggestion and an editable email draft. The AD prepares, checks, and explicitly saves actions; no message is sent. |
| Lead journey | Each lead drawer shows seven evidence-backed checkpoints: capture, qualification, ownership, notification, context validation, seller action and Marketing feedback. The **Workflow** tab explains the intended system-wide flow. |
| My metrics | Individual Q2/Q3 or combined-quarter trend, assigned leads, status-worked, own activity within SLA, first recorded response latency and demo action counts. Historical extract is anchored to Oct 8, 2026. |
| Next steps | A separate roadmap for future agentic SDR enrichment, research, drafting and orchestration. It is explicitly **not implemented** or claimed as production. |
| Manager overview | Inspect status, ownership, seller-action evidence and descriptive regional mix adjustment. Assign initially unowned leads. |
| Qualification review | Review existing-customer and unmatched-account candidates, preserve hand-raisers, record a human decision and export the qualification audit. |
| Priority rules | Tune scoring weights **and disposition reason categories** without code; justify, audit, and undo edits. |
| Data guard | Inspect eight evidence-backed health checks without mutating or guessing source values. |
| Integrations & alerts | Inspect the Slack outbox and retry behavior in simulation mode; inspect the Salesforce connector boundary. |

## Optional Claude API integration

The app supports **read-only, human-triggered** AI qualification and AD next-action suggestions. To enable it:

1. Copy `.env.example` to `.env` in the same folder as `server.py`.
2. Set `ANTHROPIC_API_KEY` to your Claude API key and `LEADLOOP_LLM_MODEL` to an accessible model ID, e.g. `claude-sonnet-4-6`.
3. Restart the server with the usual Windows / macOS / Linux launcher. The Windows launcher automatically installs the new `python-dotenv` dependency if necessary.
4. Sign in as the demo Manager and open **Qualification Review** for a qualification suggestion; or sign in as an AD, click a lead and choose **Ask Claude for suggestion**. Both requests are opt-in, read-only and require human review. The AD may copy an email draft, but the application never sends it.

The file `.env` is **not** included in this ZIP and is ignored by Git. Never email, upload, screenshot, or commit your API key. Model requests send only minimized attributes, not email or full contact name; Anthropic API usage may incur charges. Shell environment variables override `.env` when both are present. You do not need to change `ai_assist.py`.

## AD copilot, visual workflow and roadmap

The application retains its fictional **Quillon LeadLoop** identity. Its visual design is **inspired by the navy/cyan investigative style of TRM Labs** (https://www.trmlabs.com/), without copying branding assets, using the TRM logo or claiming affiliation. The design is implemented with local CSS and no remotely downloaded images.

- **Next Best Action:** deterministic guidance is always available in the lead detail view. Where `ANTHROPIC_API_KEY` and `LEADLOOP_LLM_MODEL` are set, a seller may explicitly request an additional Claude opinion and an optional editable email draft. The model receives minimized structured lead attributes (no name, email, raw notes or contact ID). Backend policy prevents the model from overriding the deterministic action type or introducing outreach for customer/Marketing-held leads. Results never directly alter CRM, email, owner or status. An AD can choose to prefill the local action form, review it and save manually.
- **Lead journey:** the seven-step trail draws on `form_submissions.csv`, `mql_events.csv`, `routing_log.csv`, `notifications_log.csv`, account/contact context, activity records and the local SQLite feedback ledger. Missing information is labeled, not inferred. A full **Workflow** tab explains the business process without pretending all steps are externally integrated.
- **AD metrics:** served by `GET /api/ad/metrics`, scoped to the signed-in seller. Own historical activity must fall after the MQL and before the same contact's next MQL event. Within-SLA and first-activity measures use seller activity logs, **not** unverified activity from unrelated users; the original historical "worked" status metric is reported separately. Local demo actions are separately labeled and are not backfilled into Q2/Q3 metrics. Historical aging uses the October 8 extract date, not the current clock.
- **Roadmap only:** proposed autonomous SDR enrichment, candidate qualification and outreach orchestration are presented under **Next steps**, not as shipped capabilities. Additional safeguards would be needed for consent, content approval, real CRM integration, observability and evaluation.

## Key forensic findings

| Metric / evidence | Reconstructed value |
|---|---:|
| MQL events, Q2 → Q3 | **640 → 467 (−27.0%)** |
| Form submissions, Q2 → Q3 | **563 → 547 (−2.8%)** |
| Q2-only event badge import | **77** |
| Q3 new-CMS demo/contact form submissions with no linked MQL | **48** |
| Other Q3 submissions with no linked MQL | **32** (20 newsletter + 12 gated-content; no CRM account match) |
| Net change in raw forms | **−16** |
| Form volume share of the 173-event MQL decline | **16 / 173 = 9.2% (~9%)** |
| Import + form-to-MQL bridge effects | **157 / 173 = 90.8% (~91%)** (= 77 + 48 + 32) |
| Comparable form-generated MQLs (excluding Q2 badge import) | **563 → 467 (−17.1%)** |
| Organic-search form submissions | **123 → 113 (−8.1%)**, source-attributed volume only |
| Paid-social form submissions (July / August / September) | **33 / 2 / 18**; Q2 **50** vs Q3 **53** |
| Reported worked Q3 | **117 / 467 = 25.1%** |
| Unworked Q3 | **350** |

**The headline finding:** only **16 / 173 (9%)** of the reported MQL decline tracks the net reduction in form submissions. The other **157 / 173 (91%)** consists of the non-recurring Q2 event import (77), unmatched new CMS forms (48) and other unmatched Q3 forms (32). Excluding the one-time import, the comparable form-generated MQL series is **563 → 467 (−17.1%)**. The 157 are **reconciliation/pipeline effects, not 157 verified software failures**: confirming which unlinked forms should have qualified still requires trigger, account-matching and suppression logs.

Q3's **467** MQLs split by *first applicable observed condition*, with no double counting:

| Evidence label | Count |
|---|---:|
| Worked by the report's SLA/status definition | 117 |
| Seller activity within SLA but status not updated | 40 |
| Existing customer: coordinate CS vs. AD | 38 |
| Unassigned at creation/system queue | 70 |
| No verified delivered notification | 61 |
| Current owner inactive at time of data extract | 28 |
| Automated status change after SLA | 8 |
| Assigned/delivered without recorded in-SLA action | 105 |

These labels are **operational classifications, not a causal decomposition or a measured true adoption rate**. An alert can be delivered without being read; a seller's logged activity may not prove a conversation; today's inactive roster does not prove the AD was inactive when a lead arrived. All historical worked metrics remain unchanged by local demo actions.

## Design choices and what is implemented

- **Deterministic rules, not predictive claims:** Scores combine form intent, account fit, priority, seniority, *validated* opportunity context, product interest, recency and transparent trust penalties. Every lead has a component-level explanation. Stale or mislinked opportunities are not treated as valid buying signals.
- **Manager-owned rules:** Bounded integer weights can be edited, audited and undone locally. Marketing Ops can also edit and undo the allowed structured disposition reason categories, with required justification and an append-only history. The AD sees the current options; the backend rejects categories no longer approved. Historical actions retain their original reason values. These are *ranking points*, not probabilities.
- **Human-in-the-loop qualification:** Hand-raisers stay in the AD lane. Existing customers are flagged for CS review; unmatched contacts can be held for enrichment **only with a manager decision**. A stable ≈20% deterministic control assignment supports a *future* comparison; no lift is claimed from this historical data. Approved dispositions remove leads from the default AD queue locally, but do not make external CRM changes.
- **Optional LLM second opinion:** If **both** `ANTHROPIC_API_KEY` and `LEADLOOP_LLM_MODEL` are set, a manager can explicitly request a model-generated qualification suggestion, based on minimized structured fields. Output is validated and remains **an editable draft**. It never auto-rejects, rewrites data, routes leads or updates the database. No key means **no API call** and rules-only guidance. Model confidence labels are not calibrated probabilities.
- **Data guard:** Recomputed form, owner, delivery, opportunity, automated-conversion, repeat-MQL and product-quality checks are shown with evidence and a next action. The source CSVs are read-only.
- **Actions & feedback:** Local actions and qualification decisions are append-only SQLite records with actor/time/structured reason/notes, with separate CSV exports for sales feedback and qualification reviews.
- **Slack:** Durable outbox with idempotency keys, retries, failure states and a local simulation mode. Actual delivery requires a bot token and channel IDs in environment variables.
- **Salesforce:** A separate REST adapter shows future SOQL pagination and writeback points, but **no live Salesforce read/write sync** is active. Real OAuth, schema IDs, mapping, conflict resolution and reconciliation are required.

### Review-driven refinements

- **159** Q3 MQLs fall into the first-match delivery/ownership risk group: 70 initially unassigned, 61 without a verified delivered alert and 28 linked to an owner inactive at extract. These are an operational triage count, not 159 independently verified failed Slack messages.
- **157 / 308 = 51.0%** is the **primary adjusted recorded-action proxy** among Q3 MQLs remaining after removing those 159 routing/alert/owner risk cases (467 − 159 = 308). The numerator is 117 reported worked + 40 additional in-SLA seller activities. It is a proxy, not proof of a successful conversation.
- **157 / 262 = 59.9%** is the **stricter sensitivity view**, also removing 38 existing-customer leads and 8 late automated-status cases. The unadjusted report worked rate remains **117 / 467 = 25.1%** by the CRM status definition.
- **14** Q3 conversions came from the routing tool, with **8** falling in the exclusive late-automation bucket; **9 contacts** had 3+ Q3 MQL events, contributing to **27 additional MQL events across 17 repeated contacts**. These are signals to review, not rows to remove blindly.
- **Acquisition context:** form-generated MQLs declined **17.1% (563 → 467)** excluding the one-off import; this is the comparable MQL-process measure, not an organic-search channel metric. Organic-search submissions were **123 → 113**, while organic + direct submissions were **219 → 213**. Paid-social submissions were **33 in July, 2 in August, and 18 in September**; the August 3 change log records budget reallocation (Q2 **50**; Q3 **53**). These channel signals support investigation; **9% always refers to 16 / 173 in the MQL reconciliation**, never a claimed paid-social impact.
- **Four core process changes:** (1) one shared definition of “worked” distinguishing status updates and human activities; (2) mandatory structured rejection reasons maintained by Marketing Ops; (3) monitoring/retry/escalation on failed lead alerts; (4) reassess ownership upon territory or roster changes. Complementary fixes include form-trigger reconciliation, human CS/enrichment routing, and a regular Sales–Marketing feedback review.
- **Marketing return** is a local two-step workflow: AD requests a review with category and note; the manager may return it to its current active AD, which creates a notification event. Salesforce remains disconnected.
- **Claude requests** use the Anthropic Messages API version `2026-10-21`, without deprecated sampling arguments. We retain a narrowly scoped HTTP adapter because the advisory call is intentionally minimal; malformed or policy-conflicting output is rejected locally.

### Hypothesis boundaries

The team roster doesn't list any SDRs, but it does **not** show whether an SDR function was previously removed. “No qualification layer may overwhelm ADs” is a **hypothesis to test**, not a verified cause. Seller decisions in the history are selective labels and are insufficient evidence for autonomous rejection. Regional comparisons exclude delivery-defective and customer MQLs, standardize descriptively by limited observed mix factors, and should **not** be treated as causal AD performance rankings.

### Operational limits

No production login/SSO, live Salesforce environment, verified outreach sending, regional holiday calendar, third-party enrichment provider, genuine randomized rollout or hosted monitoring are implemented. The app is designed for a technical live walkthrough **on localhost**. The local background poll simulates new-file ingestion and Slack dispatch, but it does not provide 24/7 production operations.

## Tests and clean reset

```bash
python -m pytest -q
```

To reset **local demo state**, stop the server, delete `state/leadloop.sqlite3` and restart. It will be regenerated. Original CSV files remain untouched. There are no tokens, runtime logs, pre-populated databases, Python virtual environments or machine-local paths inside this submission.

Environment variable placeholders are in `.env.example`. A local `.env` is loaded automatically at startup; keep credentials out of submitted artifacts.

## Architecture & file map

| Path | Purpose |
|---|---|
| `static/` | Guided investigation + interactive AD/manager interface |
| `core.py` | Historical file loading, SLAs, action ledger |
| `advanced.py` | Auditable scoring, qualification, risk checks and adoption analysis |
| `ai_assist.py` | Explicit optional qualification and next-action Claude adviser; read-only |
| `ad_experience.py` | AD metric definitions, evidence-backed journey and deterministic next actions |
| `server.py` | REST API, role guardrails, Slack outbox and local manager controls |
| `integration.py` | Slack sender + Salesforce REST scaffold |
| `data/` | The 14 untouched source CSVs |
| `tests/`, `test_core.py` | Deterministic/API regression tests |
| `docs/ARCHITECTURE.md` | Architecture and productionization outline |
| `docs/DATA_DICTIONARY.md` | Original package field and definition context |

**Principle:** Separate *observed data*, *inferred hypotheses*, *human-controlled actions*, and *unconnected future integrations*.

## LeadLoop workflow (end-to-end demo)

The sidebar follows the story: **Investigation → Workflow → Workspace → Roadmap**.

- **Workflow** replays the 547 raw Q3 form submissions through nine stages (Capture, Identity & dedupe, Enrichment, Qualification, Validation, Routing, Notify, AD workspace, Feedback loop). Each box shows only the stage and who runs it (integration, workflow rule, AI, human); click it for the full mechanics, Q3 counts and real example leads. "Run the pipeline" builds every AD's list; "Trace one lead" animates a single lead through the boxes.
- The run compares Q3 as it happened with the LeadLoop outcome, lists the alerts that would have fired, and sizes the pipeline at risk (order of magnitude).
- ADs act from the **LeadLoop inbox** (Workspace) or the simulated Slack card; reject/return require a reason. Marketing sees decisions in the manager **LeadLoop feedback** tab.
- **Roadmap**: V1 (this build) → V2 Connect → V3 Qualify → V4 Prepare → V5 Learn, each with a gate, plus the V1 rollout and what a control group can prove.
- AI steps call Claude when `ANTHROPIC_API_KEY` is set (minimal fields, validated JSON, advisory only, cached per lead); otherwise an offline stand-in with the same contract runs and is labelled as such.

Code: `pipeline.py` (stages + replay), `/api/pipeline/*` in `server.py`, `static/workflow.js`, tests in `tests/test_pipeline.py`.
