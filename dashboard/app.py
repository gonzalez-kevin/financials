"""Personal spending dashboard.

Run from the project root:
    python -m dashboard.app
then open http://127.0.0.1:5050
"""
import csv
import io
import os
import secrets
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg2.errors
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, url_for

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / 'connections' / '.env')
load_dotenv(ROOT / '.env')
sys.path.insert(0, str(ROOT))

import main as sync  # noqa: E402  (reuses DB connection + Plaid sync logic)
from dashboard.finance import (  # noqa: E402
    DEFAULT_EXCLUDED_CATEGORIES, counts_toward_budget, summarize_accounts, summarize_week,
    week_end, week_start, weekly_totals,
)

WEEKLY_BUDGET = Decimal(os.getenv('WEEKLY_BUDGET', '1200'))
EXCLUDED_CATEGORIES = tuple(
    c.strip() for c in os.getenv('EXCLUDED_CATEGORIES', ','.join(DEFAULT_EXCLUDED_CATEGORIES)).split(',')
    if c.strip()
)
HISTORY_WEEKS = 8
HISTORY_MONTHS = 12

app = Flask(__name__)
# Only used to sign flash messages. Set DASHBOARD_SECRET_KEY in production; the random
# fallback is safe but resets on every restart.
app.secret_key = os.getenv('DASHBOARD_SECRET_KEY') or secrets.token_hex(32)

TX_SQL = """
    SELECT t.id, t.date, t.datetime, t.amount, t.name, t.merchant_name,
           t.category_primary, t.category_detailed, t.payment_channel, t.pending,
           t.iso_currency_code, a.name AS account_name, a.mask AS account_mask,
           p.institution_name
    FROM transactions t
    LEFT JOIN accounts a ON a.id = t.account_id
    LEFT JOIN plaid_items p ON p.id = a.item_id
    WHERE t.date BETWEEN %s AND %s
    ORDER BY t.date DESC, t.amount DESC
"""


def fetch_transactions(start: date, end: date):
    conn = sync.get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(TX_SQL, (start, end))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

ACCOUNTS_SQL = """
    SELECT a.id, a.name, a.mask, a.type, a.subtype, a.current_balance, a.available_balance,
           a.credit_limit, a.iso_currency_code, a.updated_at, p.institution_name
    FROM accounts a
    LEFT JOIN plaid_items p ON p.id = a.item_id
    ORDER BY p.institution_name, a.name
"""


def fetch_accounts():
    conn = sync.get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(ACCOUNTS_SQL)
            except psycopg2.errors.UndefinedColumn:  # credit_limit is added by the next sync
                conn.rollback()
                cur.execute(ACCOUNTS_SQL.replace('a.credit_limit', 'NULL AS credit_limit'))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def last_synced():
    conn = sync.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(updated_at) FROM plaid_items")
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def parse_date(value, default: date) -> date:
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else default
    except ValueError:
        return default


@app.template_filter('money')
def money(value):
    value = Decimal(value or 0)
    sign = '-' if value < 0 else ''
    return f"{sign}${abs(value):,.2f}"


@app.template_filter('category')
def category_label(value):
    if value == OTHER_SPEND:
        return 'All other'
    return (value or 'Uncategorized').replace('_', ' ').capitalize()


@app.context_processor
def inject_globals():
    return {'counts': lambda t: counts_toward_budget(t, EXCLUDED_CATEGORIES),
            'today': date.today()}


@app.route('/')
def index():
    today = date.today()
    start = week_start(parse_date(request.args.get('week'), today))
    history_start = start - timedelta(weeks=HISTORY_WEEKS - 1)
    txs = fetch_transactions(history_start, week_end(start))

    summary = summarize_week(txs, start, today, WEEKLY_BUDGET, EXCLUDED_CATEGORIES)
    prev = summarize_week(txs, start - timedelta(weeks=1), today, WEEKLY_BUDGET, EXCLUDED_CATEGORIES)
    history = weekly_totals(txs, [history_start + timedelta(weeks=i) for i in range(HISTORY_WEEKS)],
                            EXCLUDED_CATEGORIES)
    avg_week = sum((h['total'] for h in history[:-1]), Decimal('0')) / (HISTORY_WEEKS - 1)

    return render_template(
        'index.html',
        s=summary, prev=prev, history=history, avg_week=avg_week,
        max_daily=max([d['amount'] for d in summary['daily']] + [Decimal('1')]),
        max_week=max([h['total'] for h in history] + [WEEKLY_BUDGET]),
        is_current=start == week_start(today),
        prev_week=start - timedelta(weeks=1), next_week=start + timedelta(weeks=1),
        last_synced=last_synced(),
    )


@app.route('/transactions')
def transactions():
    today = date.today()
    start = week_start(parse_date(request.args.get('week'), today))
    end = week_end(start)
    txs = fetch_transactions(start, end)
    show_all = request.args.get('all') == '1'
    visible = txs if show_all else [t for t in txs if counts_toward_budget(t, EXCLUDED_CATEGORIES)]
    summary = summarize_week(txs, start, today, WEEKLY_BUDGET, EXCLUDED_CATEGORIES)
    return render_template(
        'transactions.html', txs=visible, s=summary, show_all=show_all,
        hidden_count=len(txs) - len(visible),
        prev_week=start - timedelta(weeks=1), next_week=start + timedelta(weeks=1),
        is_current=start == week_start(today),
    )


@app.route('/monthly')
def monthly():
    today = date.today()
    first = add_months(today, -(HISTORY_MONTHS - 1))
    months = [add_months(first, i) for i in range(HISTORY_MONTHS)]
    m = monthly_by_category(fetch_transactions(first, today), months, EXCLUDED_CATEGORIES)
    chart = {
        'labels': [d.strftime("%b '%y") for d in months],
        'series': [{'label': category_label(s['category']), 'other': s['category'] == OTHER_SPEND,
                    'data': [str(a) for a in s['amounts']], 'total': str(s['total'])} for s in m['series']],
        'totals': [str(t) for t in m['totals']],
    }
    full = m['totals'][:-1]
    return render_template('monthly.html', m=m, chart=chart, this_month=month_start(today),
                           full_avg=sum(full, Decimal('0')) / len(full) if full else Decimal('0'))


@app.route('/export')
def export():
    today = date.today()
    if 'start' not in request.args:
        return render_template('export.html', start=today.replace(day=1), end=today)

    start = parse_date(request.args.get('start'), today.replace(day=1))
    end = parse_date(request.args.get('end'), today)
    if start > end:
        start, end = end, start

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['date', 'name', 'merchant', 'amount', 'currency', 'category', 'category_detailed',
                     'institution', 'account', 'account_mask', 'payment_channel', 'pending',
                     'counts_toward_budget', 'transaction_id'])
    for t in sorted(fetch_transactions(start, end), key=lambda t: (t['date'], t['id'])):
        writer.writerow([
            t['date'].isoformat(), t['name'], t['merchant_name'] or '', t['amount'],
            t['iso_currency_code'] or '', t['category_primary'] or '', t['category_detailed'] or '',
            t['institution_name'] or '', t['account_name'] or '', t['account_mask'] or '',
            t['payment_channel'] or '', t['pending'], counts_toward_budget(t, EXCLUDED_CATEGORIES), t['id'],
        ])

    filename = f"transactions_{start.isoformat()}_to_{end.isoformat()}.csv"
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@app.route('/accounts')
def accounts():
    accts = fetch_accounts()
    return render_template('accounts.html', a=summarize_accounts(accts), last_synced=last_synced())


@app.route('/sync', methods=['POST'])
def run_sync():
    try:
        errors = sync.main()
        if errors:
            flash('Sync finished with errors: ' + '; '.join(errors), 'error')
        else:
            flash('Sync finished.', 'ok')
    except Exception as e:  # surface any failure in the UI instead of a 500
        flash(f'Sync failed: {e}', 'error')
    return redirect(request.referrer or url_for('index'))


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('DASHBOARD_PORT', 5050)),
            debug=os.getenv('FLASK_DEBUG') == '1')
