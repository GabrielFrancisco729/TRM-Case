
## Pipeline replay

`pipeline.run` processes `form_submissions` chronologically. Every stage records `{stage, executor, status, title, detail}` per lead, so the UI can show who acted (integration / workflow rule / AI / human) and why. Validation uses territory change dates to apply owner revalidation after territory changes; routing never falls back to a system queue (no territory → manager triage). Runs, AD decisions and AI results are stored in SQLite (`pipeline_runs`, `pipeline_decisions`, `pipeline_ai_cache`); source CSVs stay read-only.
