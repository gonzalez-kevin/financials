# financials

Personal spending tracker. It pulls bank and credit-card transactions from **Plaid** into a
**Supabase PostgreSQL** database, then shows them in a small **Flask dashboard** with a weekly budget.
Every morning it also emails a **daily spending summary** through Gmail.

```
Plaid (Amex, Chase) ──► main.py (sync) ──► Postgres (Supabase)
                                              │
                         dashboard/ (Flask) ◄─┤──► jobs/daily.py (email via Gmail SMTP)
```

### Features
- Incremental sync with Plaid `transactions/sync`. The cursor is stored per item in `plaid_items.next_cursor`.
- Weekly budget view (weeks run **Sunday–Saturday**). Shows spend, amount remaining, projection, daily bars,
  top categories and merchants, and an 8-week history.
- A transactions list per week, a CSV export for any date range, and a "Sync now" button.
- Daily email covering yesterday's spend, week-to-date budget status and large transactions, plus any sync errors.
- One Docker image used for both the Cloud Run dashboard (kept private behind IAP) and the Cloud Run daily job,
  which Cloud Scheduler triggers at 5 AM Pacific.

### Project layout
| Path | Purpose |
|---|---|
| `main.py` | Plaid → Postgres sync (`init_db`, `sync_accounts`, `sync_transactions`, `main()`). |
| `schema.sql` | Database schema (the same DDL that `main.init_db` runs). |
| `dashboard/app.py` | Flask app: routes `/`, `/transactions`, `/export`, `/sync` (POST). |
| `dashboard/finance.py` | Pure budget math (week boundaries, summaries, weekly totals). |
| `dashboard/templates/`, `dashboard/static/` | Jinja templates and CSS. |
| `jobs/daily.py` | Daily sync and email job. |
| `connections/server.py` | Plaid Quickstart backend, used once to link accounts and get access tokens. |
| `Dockerfile` | Image for the dashboard (gunicorn) and the job (`python -m jobs.daily`). |

### Setup
Requires Python 3.12 (the Dockerfile uses 3.12; `pyproject.toml` says `>=3.9`).

```bash
python -m venv .venv
.venv/bin/pip install -e .        # or install the dependencies listed in pyproject.toml
```

Create `connections/.env` and/or a root `.env` (both are git-ignored, and the root `.env` is loaded second):

```dotenv
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_ENV=production            # or sandbox
DATABASE_URL=postgresql://...   # Supabase connection string

AMEX_ITEM_ID=...
AMEX_ACCESS_TOKEN=...
CHASE_ITEM_ID=...
CHASE_ACCESS_TOKEN=...

# Optional
WEEKLY_BUDGET=1200
EXCLUDED_CATEGORIES=INCOME,TRANSFER_IN,TRANSFER_OUT,LOAN_PAYMENTS,OTHER
DASHBOARD_SECRET_KEY=...
DASHBOARD_PORT=5050
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=...          # Gmail app password, not your account password
SUMMARY_TO=you@gmail.com
DASHBOARD_URL=https://...
LARGE_TX_THRESHOLD=200
```

#### Getting Plaid access tokens
`connections/server.py` is the Plaid Quickstart backend. Run it from inside `connections/` with
`./start.sh`; it listens on `PORT`, default 8000. Then go through Plaid Link with the Quickstart frontend.
The item ID and access token come back from `/api/set_access_token`. Copy them into the `*_ITEM_ID` and
`*_ACCESS_TOKEN` variables.

### Usage
Run all commands from the project root:

```bash
.venv/bin/python main.py                      # sync now
.venv/bin/python -m dashboard.app             # dashboard at http://127.0.0.1:5050
.venv/bin/python -m jobs.daily --dry-run      # sync and print the email
.venv/bin/python -m jobs.daily --no-sync      # email only
.venv/bin/python -m jobs.daily                # sync and send the email
```

### Tests
```bash
.venv/bin/python -m unittest dashboard.test_finance jobs.test_daily
```
The tests cover the pure logic only and need no database or network access.

### Deploy (Google Cloud)
Deployment is done by hand in the Google Cloud Console (region `us-west1`):

1. Enable the Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Cloud Scheduler and
   Identity-Aware Proxy APIs.
2. Create a `financials-runtime` service account.
3. In Secret Manager, add `PLAID_CLIENT_ID`, `PLAID_SECRET`, `DATABASE_URL`, the `*_ITEM_ID` /
   `*_ACCESS_TOKEN` pairs, `DASHBOARD_SECRET_KEY` and `GMAIL_APP_PASSWORD`. Grant `financials-runtime` the
   Secret Manager Secret Accessor role. Use Supabase's pooler connection string for `DATABASE_URL`.
4. Create the `financials-dashboard` Cloud Run service with continuous deployment from this GitHub repo
   (build type: Dockerfile). Require authentication with IAP, set `TZ=America/Los_Angeles`, `PLAID_ENV` and
   `WEEKLY_BUDGET`, and reference every secret except `GMAIL_APP_PASSWORD` as an env var of the same name.
5. In Identity-Aware Proxy, give your Google account the IAP-secured Web App User role.
6. Create the `financials-daily` Cloud Run job from the same image with command `python` and args
   `-m jobs.daily`. Give it the same env vars plus `GMAIL_ADDRESS`, `SUMMARY_TO`, `LARGE_TX_THRESHOLD` and
   `DASHBOARD_URL`, and all secrets including `GMAIL_APP_PASSWORD`.
7. Add a scheduler trigger to the job: `0 5 * * *`, time zone `America/Los_Angeles`.

Pushing to GitHub redeploys the dashboard. The job does not follow automatically, so update it to the
newest image after code changes.
