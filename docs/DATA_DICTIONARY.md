# Supplied dataset reference

The following dataset description and definitions were provided with the fictional Quillon Labs case package.

---

# Quillon Labs data package

Everything here is invented: the company, its products, the accounts, the people and the campaigns. It behaves like a real marketing database, including the inconvenient parts.

**Window:** 2026-04-01 to 2026-09-30 (two full quarters: Q2 = Apr to Jun, Q3 = Jul to Sep). Pulled 2026-10-08, so every MQL has passed its response window. **Timestamps:** UTC.

## Quillon Labs in one paragraph

Quillon sells two products. **Sentinel** is an investigations and compliance platform used by banks, crypto businesses and public-sector agencies to trace funds and manage cases. **Beacon** launched on 2026-08-14: threat intelligence for security teams. Quillon sells into four regions (NA, EMEA, APAC, LATAM). Account directors (ADs) are the regional sellers who work the leads marketing generates. Account executives (AEs) own the account.

## How MQL works today

- **MQL** is set automatically when a person submits a qualifying form: demo request, contact us, partner request, gated content, webinar registration, newsletter sign-up.
- Demo requests and partner requests are hand-raisers with a 24-hour response expectation. Every other MQL has 5 business days.
- A notification goes to the AD in a regional Slack channel. Unassigned MQLs go to #mql-unassigned.
- **Worked**, as the weekly report counts it: the MQL's status moved off "MQL" within the response window, done by anyone.
- Event badge scans are imported as lists. They are loaded as MQLs.

## Files

| file | what it holds |
|---|---|
| accounts.csv | CRM accounts: segment, region, fit_tier (A best to D, blank = not scored), customer_status, account_owner (AE), ad_owner, cs_owner |
| contacts.csv | CRM people: account_id (blank if unmatched), email, country, title, seniority, created_date, current lifecycle_stage (Lead, MQL, SQL, Customer) |
| form_submissions.csv | Every form fill: contact, timestamp, form_id, form_name, page, campaign_id, source, product_interest |
| mql_events.csv | One row per MQL event: mql_id, contact_id, mql_timestamp, trigger_form_id, owner_at_mql, current_owner, priority, status_today. A person can MQL more than once. |
| mql_status_history.csv | Every status change on an MQL: timestamp, old and new status, changed_by (marketing_automation, import, ad, ae, lead_routing_tool), note |
| routing_log.csv | Who each MQL was assigned to and when, and the reason |
| notifications_log.csv | Every alert sent to ADs about an MQL: channel, sent_at, delivered (true/false), error |
| ad_activity.csv | Calls, emails, meetings and tasks logged by sellers: contact, timestamp, type, user |
| campaigns.csv | Campaigns with type, channel, product, region, dates, spend (USD) and location for events |
| opportunities.csv | Open and recent closed opportunities, with the primary contact |
| team.csv | Sellers and customer success by role and region, with an active flag and end date |
| territory_changes.csv | Account moves between ADs with effective dates |
| marketing_ops_changelog.csv | The marketing ops team's own log of system and rule changes. Kept informally. May be incomplete. |
| weekly_report.csv | The weekly funnel report as the team runs it today: MQLs created and MQLs worked |
