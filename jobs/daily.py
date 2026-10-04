"""Daily job: pull new transactions from Plaid, then email a spending summary via Gmail SMTP.

Run from the project root:
    python -m jobs.daily              # sync + email
    python -m jobs.daily --no-sync    # email only
    python -m jobs.daily --dry-run    # sync + print the email instead of sending it

Required env vars for email: GMAIL_ADDRESS, GMAIL_APP_PASSWORD.
Optional: SUMMARY_TO (defaults to GMAIL_ADDRESS), DASHBOARD_URL, LARGE_TX_THRESHOLD (default 200),
WEEKLY_BUDGET, EXCLUDED_CATEGORIES. Dates use the container's TZ (America/Los_Angeles).
"""
import argparse
import html
import os
import smtplib
import ssl
import sys
from datetime import date, timedelta
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as sync  # noqa: E402
from dashboard.app import EXCLUDED_CATEGORIES, WEEKLY_BUDGET, category_label, fetch_transactions, money  # noqa: E402
from dashboard.finance import counts_toward_budget, summarize_week, week_start  # noqa: E402

SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 465


def build_summary(txs, report_day: date, budget: Decimal, excluded, large_threshold: Decimal) -> dict:
    """Summary for ``report_day`` (normally yesterday) plus its week-to-date."""
    week = summarize_week(txs, week_start(report_day), report_day, budget, excluded)
    day_txs = sorted((t for t in txs if t['date'] == report_day and counts_toward_budget(t, excluded)),
                     key=lambda t: Decimal(t['amount']), reverse=True)
    large = sorted((t for t in txs
                    if week['start'] <= t['date'] <= report_day
                    and counts_toward_budget(t, excluded)
                    and Decimal(t['amount']) >= large_threshold),
                   key=lambda t: Decimal(t['amount']), reverse=True)
    return {
        'day': report_day,
        'day_total': sum((Decimal(t['amount']) for t in day_txs), Decimal('0')),
        'day_txs': day_txs,
        'week': week,
        'large': large,
        'large_threshold': large_threshold,
    }


def _tx_name(t) -> str:
    return t.get('merchant_name') or t.get('name') or 'Unknown'


def render_subject(s: dict) -> str:
    w = s['week']
    status = 'OVER budget' if w['over_budget'] else f"{money(w['remaining'])} left"
    return f"Spending {s['day']:%a %b %-d}: {money(s['day_total'])} · week {money(w['total'])} ({status})"


def render_text(s: dict, sync_errors=(), dashboard_url: str = '') -> str:
    w = s['week']
    lines = []
    if sync_errors:
        lines += ['⚠ Sync problems:'] + [f'  - {e}' for e in sync_errors] + ['']
    lines += [
        f"Yesterday ({s['day']:%A %b %-d}): {money(s['day_total'])} across {len(s['day_txs'])} transactions",
        '',
        f"Week of {w['start']:%b %-d}: {money(w['total'])} of {money(w['budget'])} ({w['pct']:.0f}%)",
        f"  {'Over by ' + money(-w['remaining']) if w['over_budget'] else money(w['remaining']) + ' remaining'}"
        + (f", {money(w['per_day_left'])}/day for {w['days_left']} days" if w['days_left'] and not w['over_budget'] else ''),
        f"  Projected week: {money(w['projected'])}",
    ]
    if s['day_txs']:
        lines += ['', "Yesterday's transactions:"]
        lines += [f"  {money(t['amount']):>11}  {_tx_name(t)}{' (pending)' if t.get('pending') else ''}"
                  for t in s['day_txs']]
    if w['categories']:
        lines += ['', 'Top categories this week:']
        lines += [f"  {money(v):>11}  {category_label(c)}" for c, v in w['categories'][:5]]
    if w['merchants']:
        lines += ['', 'Top merchants this week:']
        lines += [f"  {money(v):>11}  {m} ({n}x)" for m, v, n in w['merchants']]
    if s['large']:
        lines += ['', f"Large transactions (≥ {money(s['large_threshold'])}) this week:"]
        lines += [f"  {money(t['amount']):>11}  {_tx_name(t)} on {t['date']:%a %b %-d}" for t in s['large']]
    if dashboard_url:
        lines += ['', f'Dashboard: {dashboard_url}']
    return '\n'.join(lines) + '\n'


def render_html(s: dict, sync_errors=(), dashboard_url: str = '') -> str:
    w = s['week']
    esc = html.escape
    color = '#c0392b' if w['over_budget'] else '#27ae60'
    bar = min(w['pct'], 100)

    def table(rows):
        cells = ''.join(f'<tr><td style="padding:2px 12px 2px 0;text-align:right;white-space:nowrap">{esc(a)}</td>'
                        f'<td style="padding:2px 0">{esc(b)}</td></tr>' for a, b in rows)
        return f'<table style="border-collapse:collapse;font-size:14px">{cells}</table>'

    parts = ['<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:560px;color:#222">']
    if sync_errors:
        parts.append('<div style="background:#fdecea;border-radius:6px;padding:8px 12px;margin-bottom:12px">'
                     '<b>⚠ Sync problems</b><ul style="margin:4px 0">'
                     + ''.join(f'<li>{esc(e)}</li>' for e in sync_errors) + '</ul></div>')
    parts.append(f'<h2 style="margin:0 0 4px">{money(s["day_total"])} '
                 f'<span style="font-weight:normal;font-size:15px;color:#666">spent {s["day"]:%A %b %-d}</span></h2>')
    remaining = (f'over by {money(-w["remaining"])}' if w['over_budget'] else f'{money(w["remaining"])} remaining'
                 + (f' · {money(w["per_day_left"])}/day for {w["days_left"]} days' if w['days_left'] else ''))
    parts.append(f'<p style="margin:12px 0 4px">Week of {w["start"]:%b %-d}: <b>{money(w["total"])}</b> of '
                 f'{money(w["budget"])} ({w["pct"]:.0f}%) — <span style="color:{color}">{esc(remaining)}</span></p>'
                 f'<div style="background:#eee;border-radius:4px;height:8px"><div style="background:{color};'
                 f'width:{bar:.0f}%;height:8px;border-radius:4px"></div></div>'
                 f'<p style="margin:4px 0;color:#666;font-size:13px">Projected week: {money(w["projected"])}</p>')
    if s['day_txs']:
        parts.append("<h3 style=\"margin:16px 0 4px\">Yesterday</h3>")
        parts.append(table((money(t['amount']), _tx_name(t) + (' (pending)' if t.get('pending') else ''))
                           for t in s['day_txs']))
    if w['categories']:
        parts.append('<h3 style="margin:16px 0 4px">Top categories this week</h3>')
        parts.append(table((money(v), category_label(c)) for c, v in w['categories'][:5]))
    if w['merchants']:
        parts.append('<h3 style="margin:16px 0 4px">Top merchants this week</h3>')
        parts.append(table((money(v), f'{m} ({n}x)') for m, v, n in w['merchants']))
    if s['large']:
        parts.append(f'<h3 style="margin:16px 0 4px">Large transactions (≥ {money(s["large_threshold"])})</h3>')
        parts.append(table((money(t['amount']), f"{_tx_name(t)} · {t['date']:%a %b %-d}") for t in s['large']))
    if dashboard_url:
        parts.append(f'<p style="margin-top:20px"><a href="{esc(dashboard_url)}">Open dashboard →</a></p>')
    parts.append('</div>')
    return ''.join(parts)


def send_email(subject: str, text: str, html_body: str):
    sender = os.getenv('GMAIL_ADDRESS')
    password = os.getenv('GMAIL_APP_PASSWORD')
    if not sender or not password:
        raise ValueError('GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set to send the summary email.')
    recipient = os.getenv('SUMMARY_TO') or sender

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f'Financials <{sender}>'
    msg['To'] = recipient
    msg.set_content(text)
    msg.add_alternative(html_body, subtype='html')

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, password.replace(' ', ''))  # app passwords are often copied with spaces
        smtp.send_message(msg)
    print(f'Summary email sent to {recipient}.')


def run(do_sync: bool = True, dry_run: bool = False, today: date = None) -> int:
    """Sync failures are reported inside the email (exit 0) so a job retry doesn't send a duplicate;
    only a failure to build/send the email itself raises and fails the job."""
    today = today or date.today()
    sync_errors = []
    if do_sync:
        try:
            sync_errors = sync.main()
        except Exception as e:  # still send the email so the failure is visible
            sync_errors = [f'Sync crashed: {e}']

    report_day = today - timedelta(days=1)
    txs = fetch_transactions(week_start(report_day), report_day)
    summary = build_summary(txs, report_day, WEEKLY_BUDGET, EXCLUDED_CATEGORIES,
                            Decimal(os.getenv('LARGE_TX_THRESHOLD', '200')))
    dashboard_url = os.getenv('DASHBOARD_URL', '')

    subject = ('⚠ ' if sync_errors else '') + render_subject(summary)
    text = render_text(summary, sync_errors, dashboard_url)
    if dry_run:
        print(f'Subject: {subject}\n\n{text}')
    else:
        send_email(subject, text, render_html(summary, sync_errors, dashboard_url))
    if sync_errors:
        print('Sync finished with errors: ' + '; '.join(sync_errors), file=sys.stderr)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-sync', action='store_true', help='skip the Plaid sync, only send the email')
    parser.add_argument('--dry-run', action='store_true', help='print the email instead of sending it')
    args = parser.parse_args()
    sys.exit(run(do_sync=not args.no_sync, dry_run=args.dry_run))
