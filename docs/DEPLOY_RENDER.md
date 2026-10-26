# Reviewer demo deployment — Render + GitHub

This project is a prototype with fictional case-study data. The hosted deployment is **not a production service**, identity provider, or connected CRM.

## Publish the repository

Create a GitHub repository and upload **the files inside the `LeadLoop` folder** to the repository root. Keep `.env`, `.venv`, SQLite files, logs and any real API secrets out of Git. The `.gitignore` excludes them. Prefer a public repository only because the case data is explicitly fictional.

## Render deployment (Web Service)

You can use **New > Blueprint** to load `render.yaml`, or **New > Web Service** and select your repo. For a manual Web Service, use:

- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Health check path: `/healthz`
- Python: version `3.12.11` via `.python-version`

Set these **Render Environment** variables (not committed to Git):

- `SESSION_SECRET`: unique long random value (generated automatically if using `render.yaml`).
- `MANAGER_DEMO_PIN`: choose a new, non-default demo PIN.
- `LEADLOOP_SITE_USER`: `reviewer` (or another username).
- `LEADLOOP_SITE_PASSWORD`: a unique long password. The browser will request it before showing any page or API, and you must give it to the interviewers separately.
- `LEADLOOP_SECURE_COOKIES`: `true` for Render HTTPS.

Optional:

- `ANTHROPIC_API_KEY` and `LEADLOOP_LLM_MODEL`: if enabled, the website can make **paid** Claude API calls, including during pipeline replay. Prefer leaving unset for the public reviewer instance. The app clearly labels its offline AI stand-in. Demonstrate real Claude locally if needed.
- Slack and Salesforce credentials: leave unset; the adapters run in demo/simulation mode.

After deployment, confirm `/healthz` responds with `{"status":"ok"}` and visit the Render URL in a private browser window to verify the site password challenge and investigation page. **Do not send a placeholder URL; verify the actual live link first.**

## Critical limits

- Render Free web service filesystems are ephemeral: the SQLite audit database, pipeline runs, AI cache and decisions **are lost on a restart, redeploy or spin-down**. To preserve actions, move to a paid Render web service with a persistent disk and set `LEADLOOP_STATE_DIR` to the mounted path, or implement a durable Postgres backend. Keep to one worker while using SQLite.
- Render Free services spin down after 15 minutes with no requests; the first load may be slow. Before a live interview, open the link and confirm it is responsive.
- The AD picker and manager PIN inside the app are **demonstration identities**, not production authentication. The outer HTTP Basic password only restricts access to trusted reviewers. Never deploy real customer data or real CRM write credentials with these controls.
- Real SMTP/Slack/Salesforce connections and an always-on pipeline require further development.

## Validation

After unpacking: `python -m pytest -q`
For a local run: `python server.py` (localhost:7860).

## Submission links

Include both your verified `https://...onrender.com` URL and `https://github.com/...` repository link in the email. If you use the shared site-password gate, email the credentials separately.
