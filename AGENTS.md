# AGENTS.md

This is a quick-reference guide for coding agents. Read it before exploring the repo; it should save you from
reading every file. Keep it updated when you change the architecture, env vars, or conventions.

## What this is
A single-user personal finance app:
1. `main.py` syncs Plaid accounts and transactions into Postgres (Supabase).
2. `dashboard/` is a Flask UI for a weekly budget.
3. `jobs/daily.py` runs the sync and then sends a Gmail summary email.
4. It is deployed by hand in the Google Cloud Console to Cloud Run (see "Deployment" below).

The stack is Python 3.12 with Flask, psycopg2 (raw SQL, no ORM), plaid-python and python-dotenv,
served by gunicorn in Docker.

## File map (what lives where)
- `main.py`: the sync module, imported elsewhere as `import main as sync`.
  - It creates the Plaid client at import time from `PLAID_CLIENT_ID`, `PLAID_SECRET` and `PLAID_ENV`
    (default `production`).
  - `get_db_connection()` reads `DATABASE_URL` and strips an accidental `DATABASE_URL=` prefix.
  - `init_db(conn)` runs the inline DDL. It **duplicates `schema.sql`**, so keep the two in sync.
  - `sync_accounts(conn, item_id, token, institution_name)` upserts `plaid_items` and then `accounts`.
  - `sync_transactions(...)` pages through `transactions_sync` from the stored cursor. It upserts added and
    modified rows, deletes removed ones, and saves `next_cursor`.
  - `main() -> list[str]` returns error messages, and an empty list means success. Items are **hardcoded**:
    Amex (`AMEX_ITEM_ID` / `AMEX_ACCESS_TOKEN`) and Chase (`CHASE_ITEM_ID` / `CHASE_ACCESS_TOKEN`).
- `schema.sql`: tables `plaid_items(id, institution_name, access_token, next_cursor, …)`,
  `accounts(id, item_id→plaid_items, name, mask, type, subtype, current_balance, available_balance,
  credit_limit, …)` and
  `transactions(id, account_id→accounts, amount, date, datetime, name, merchant_name, category_primary,
  category_detailed, payment_channel, pending, iso_currency_code, …)`.
- `dashboard/finance.py`: **pure functions** with no Flask or DB code, so it is easy to test.
  - `week_start` (the Sunday on or before the date) and `week_end` (+6 days).
  - `counts_toward_budget(tx, excluded)`.
  - `summarize_week(txs, start, today, budget, excluded)` returns a dict with total, remaining, pct, daily,
    categories, merchants, top expenses, projection and related fields.
  - `weekly_totals`.
  - `month_start`, `add_months` and `monthly_by_category(txs, months, excluded, top_n=7)`, which returns
    per-category monthly series (smaller categories fold into `OTHER_SPEND`), monthly totals and an average.
  - `summarize_accounts(accounts)` groups accounts by Plaid type and returns net worth, cash and credit card
    balances owed. Limits and utilization are deliberately not shown (some cards have no hard limit).
  - `DEFAULT_EXCLUDED_CATEGORIES`, which lists Plaid PFC primary values for money movement such as
    transfers, loan payments, income and `OTHER`.
- `dashboard/app.py`: the Flask `app`.
  - Config: `WEEKLY_BUDGET` (default 1200), `EXCLUDED_CATEGORIES` (env CSV), `HISTORY_WEEKS = 8` and
    `HISTORY_MONTHS = 12`.
  - `fetch_transactions(start, end)` joins transactions, accounts and plaid_items and returns a list of
    dicts, using `RealDictCursor`.
  - Jinja filters: `money` and `category`. Context globals: `counts(t)` and `today`.
  - Routes:
    - `/` (`?week=YYYY-MM-DD`)
    - `/monthly`: the last 12 months by category, with Chart.js charts and a table
    - `/transactions` (`?week=`, `?all=1`)
    - `/export` (a form, or `?start=&end=` returns CSV)
    - `/accounts` (balances per account, net worth, cash vs card balances)
    - `POST /sync`, which calls `sync.main()` and flashes the result
- `dashboard/templates/`: `base.html` (has a `head` block for page scripts), `index.html`, `monthly.html`,
  `transactions.html`, `accounts.html`, `export.html`, and the shared partial `_week_nav.html`. CSS is in
  `dashboard/static/style.css`. Chart.js 4.4.1 is vendored at `dashboard/static/vendor/` (no CDN), so any page
  can load it via `{% block head %}`.
- `jobs/daily.py`: reports on **yesterday** plus week-to-date.
  - It reuses `fetch_transactions`, `money`, `category_label`, `WEEKLY_BUDGET` and `EXCLUDED_CATEGORIES`
    from `dashboard.app`.
  - `build_summary`, `render_subject`, `render_text`, `render_html` (HTML-escaped) and `send_email`
    (Gmail SMTP_SSL on port 465).
  - `run()` reports sync errors **inside the email** and still exits 0, so a job retry doesn't send a
    duplicate email.
  - Flags: `--no-sync`, `--dry-run`.
- `connections/`: the Plaid Quickstart backend, used only to link accounts and obtain access tokens.
  - It is **not** part of the app or the Docker image.
  - It defaults to `PLAID_ENV=sandbox` and loads `.env` from the current directory (`connections/.env`).
  - Its Plaid Quickstart endpoints are mostly irrelevant here; avoid editing it unless asked.
- `Dockerfile`: `python:3.12-slim` with `TZ=America/Los_Angeles`. It installs the deps from
  `pyproject.toml` and copies **only** `main.py`, `dashboard/` and `jobs/`. Its default CMD is gunicorn
  `dashboard.app:app`.
- `.github/workflows/tests.yml`: GitHub Actions runs the unittest suite on Python 3.12 for pushes to `main` and PRs.
- `.idea/`, `__pycache__/` and `.venv/` are IDE and runtime artifacts. They are git-ignored; ignore them.

## Deployment
There is no deploy script; everything is set up manually in the Google Cloud Console (region `us-west1`).
- `financials-dashboard`: a Cloud Run service behind IAP, built from the `Dockerfile` via continuous
  deployment from GitHub.
- `financials-daily`: a Cloud Run job on the same image, with command `python -m jobs.daily`, triggered by a
  Cloud Scheduler trigger at `0 5 * * *` `America/Los_Angeles`.
- Secrets live in Secret Manager and are exposed as env vars of the same name. `GMAIL_APP_PASSWORD` goes
  only to the job.
- After a code change the job does not pick up the new image automatically; it must be updated to the newest
  image.

## Commands (run from repo root)
```bash
.venv/bin/python -m unittest dashboard.test_finance jobs.test_daily   # tests: 16, no DB/network needed
.venv/bin/python main.py                                              # sync
.venv/bin/python -m dashboard.app                                     # http://127.0.0.1:5050
.venv/bin/python -m jobs.daily --dry-run                              # print email
```
Modules use `sys.path.insert(ROOT)` plus `import main`, so always run them as modules from the repo root.

## Domain conventions (important)
- **Plaid amount sign:** positive means money out (spending) and negative means money in (refunds or credits).
  Spend totals are **net**, so refunds in spending categories reduce the total.
- **Weeks run Sunday–Saturday** (`week_start`).
- Budget filtering uses `category_primary` (Plaid personal_finance_category.primary) against
  `EXCLUDED_CATEGORIES`. Card payments are excluded so purchases aren't double-counted between checking
  and the card.
- Money is handled as `Decimal` everywhere; never use floats for amounts (`pct` is the only float).
- Dates use local TZ (`America/Los_Angeles` in the container).

## Environment
Env vars are loaded from `connections/.env` and then the root `.env` (both git-ignored and containing
**real secrets**). Never print, commit, or copy their values.
- Required: `PLAID_CLIENT_ID`, `PLAID_SECRET`, `DATABASE_URL`, plus at least one item/token pair.
- Optional: `PLAID_ENV`, `WEEKLY_BUDGET`, `EXCLUDED_CATEGORIES`, `DASHBOARD_SECRET_KEY`, `DASHBOARD_PORT`,
  `FLASK_DEBUG`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `SUMMARY_TO`, `DASHBOARD_URL`, `LARGE_TX_THRESHOLD`.

## Common change recipes
- **Add an institution:**
  1. Add an env-var pair in `main.main()`.
  2. Create the same names in Secret Manager and reference them in both the Cloud Run service and job.
  3. Update the README env list.
- **Change the schema:** edit both `schema.sql` and `main.init_db`. `CREATE TABLE IF NOT EXISTS` won't alter
  existing tables, so add `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. Then update `TX_SQL` in
  `dashboard/app.py` and the CSV export columns if needed.
- **Budget math:** change `dashboard/finance.py` and add a test to `dashboard/test_finance.py`.
- **Email content:** change `render_text` and `render_html` in `jobs/daily.py` (keep both in sync, and
  escape HTML), then test in `jobs/test_daily.py`.
- **New dashboard page:** add a route in `dashboard/app.py` and a template that extends `base.html`. Reuse
  `_week_nav.html` for week navigation.

## Style
- Python: single quotes, 4-space indents, compact code, few comments, and short module docstrings that
  show how to run the module.
- Ruff is configured in the IDE.
- Tests use `unittest` and plain dict fixtures (see the `tx()` helper in the tests).
- Keep `finance.py` pure, with no DB or Flask imports.

## Known quirks
- `DEFAULT_EXCLUDED_CATEGORIES` lists `'LOAN_PAYMENTS'` twice. This is harmless.
- `main.py` creates the Plaid client at import time. Importing it without credentials works, but API calls
  will fail.
- `jobs.daily` imports `dashboard.app`, which creates the Flask app on import.
