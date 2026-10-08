"""Pure helpers for weekly budget math (no Flask / DB dependencies, easy to test)."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

# Plaid personal_finance_category.primary values that are money movement, not spending.
# Credit-card payments show up as LOAN_PAYMENTS / TRANSFER_OUT on the checking side and
# would otherwise double-count purchases already recorded on the card.
DEFAULT_EXCLUDED_CATEGORIES = ('INCOME', 'TRANSFER_IN', 'TRANSFER_OUT', 'LOAN_PAYMENTS', 'OTHER', 'LOAN_PAYMENTS')

DAY_NAMES = ('Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')


def week_start(d: date) -> date:
    """Return the Sunday that starts the week containing ``d``."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def week_end(start: date) -> date:
    return start + timedelta(days=6)


def counts_toward_budget(tx: dict, excluded=DEFAULT_EXCLUDED_CATEGORIES) -> bool:
    return (tx.get('category_primary') or '') not in excluded


def _display_name(tx: dict) -> str:
    return tx.get('merchant_name') or tx.get('name') or 'Unknown'


def summarize_week(transactions, start: date, today: date, budget: Decimal,
                   excluded=DEFAULT_EXCLUDED_CATEGORIES, top_n: int = 5) -> dict:
    """Build the weekly budget summary from a list of transaction dicts.

    Spend is net: refunds (negative amounts in spending categories) reduce the total.
    """
    budget = Decimal(budget)
    end = week_end(start)
    spend_txs = [t for t in transactions
                 if start <= t['date'] <= end and counts_toward_budget(t, excluded)]

    total = sum((Decimal(t['amount']) for t in spend_txs), Decimal('0'))

    daily = [Decimal('0')] * 7
    by_category = defaultdict(Decimal)
    by_merchant = defaultdict(Decimal)
    merchant_counts = defaultdict(int)
    for t in spend_txs:
        amt = Decimal(t['amount'])
        daily[(t['date'] - start).days] += amt
        by_category[t.get('category_primary') or 'UNCATEGORIZED'] += amt
        name = _display_name(t)
        by_merchant[name] += amt
        merchant_counts[name] += 1

    if today < start:
        days_elapsed = 0
    elif today > end:
        days_elapsed = 7
    else:
        days_elapsed = (today - start).days + 1
    days_left = 7 - days_elapsed

    remaining = budget - total
    pct = float(total / budget * 100) if budget else 0.0
    avg_per_day = total / days_elapsed if days_elapsed else Decimal('0')
    projected = avg_per_day * 7 if days_elapsed else Decimal('0')
    per_day_left = (remaining / days_left) if days_left and remaining > 0 else Decimal('0')

    top_expenses = sorted((t for t in spend_txs if Decimal(t['amount']) > 0),
                          key=lambda t: Decimal(t['amount']), reverse=True)[:top_n]
    categories = sorted(((c, v) for c, v in by_category.items() if v > 0),
                        key=lambda kv: kv[1], reverse=True)
    merchants = sorted(((m, v, merchant_counts[m]) for m, v in by_merchant.items() if v > 0),
                       key=lambda kv: kv[1], reverse=True)[:top_n]

    biggest_day_idx = max(range(7), key=lambda i: daily[i]) if spend_txs else None

    return {
        'start': start,
        'end': end,
        'budget': budget,
        'total': total,
        'remaining': remaining,
        'pct': pct,
        'over_budget': total > budget,
        'days_elapsed': days_elapsed,
        'days_left': days_left,
        'avg_per_day': avg_per_day,
        'projected': projected,
        'per_day_left': per_day_left,
        'transaction_count': len(spend_txs),
        'daily': [{'day': DAY_NAMES[i], 'date': start + timedelta(days=i), 'amount': daily[i]}
                  for i in range(7)],
        'biggest_day': DAY_NAMES[biggest_day_idx] if biggest_day_idx is not None and daily[biggest_day_idx] > 0 else None,
        'top_expenses': top_expenses,
        'categories': categories,
        'merchants': merchants,
    }


def weekly_totals(transactions, starts, excluded=DEFAULT_EXCLUDED_CATEGORIES):
    """Net spend per week for each Sunday in ``starts``."""
    totals = {s: Decimal('0') for s in starts}
    for t in transactions:
        if not counts_toward_budget(t, excluded):
            continue
        s = week_start(t['date'])
        if s in totals:
            totals[s] += Decimal(t['amount'])
    return [{'start': s, 'total': totals[s]} for s in starts]


# Plaid account types: depository/investment balances are assets; credit/loan balances are amounts owed.
ACCOUNT_GROUPS = (('depository', 'Cash'), ('investment', 'Investments'), ('credit', 'Credit cards'),
                  ('loan', 'Loans'), ('other', 'Other'))
LIABILITY_TYPES = ('credit', 'loan')


def _dec(value):
    return Decimal(value) if value is not None else None


def credit_limit(acct: dict):
    """Card limit from Plaid, else inferred as owed + available (Plaid omits the limit for some issuers)."""
    limit = _dec(acct.get('credit_limit'))
    if limit is None and acct.get('available_balance') is not None:
        limit = Decimal(acct.get('current_balance') or 0) + Decimal(acct['available_balance'])
    return limit if limit else None


def summarize_accounts(accounts) -> dict:
    """Group accounts by type and compute net worth, cash vs credit, and credit utilization."""
    known = {t for t, _ in ACCOUNT_GROUPS}
    groups = {t: [] for t, _ in ACCOUNT_GROUPS}
    for a in accounts:
        groups[a.get('type') if a.get('type') in known else 'other'].append(a)

    def total(rows):
        return sum((Decimal(a.get('current_balance') or 0) for a in rows), Decimal('0'))

    cards = []
    for a in sorted(groups['credit'], key=lambda a: Decimal(a.get('current_balance') or 0), reverse=True):
        owed, limit = Decimal(a.get('current_balance') or 0), credit_limit(a)
        cards.append({**a, 'owed': owed, 'limit': limit,
                      'pct': float(owed / limit * 100) if limit else None})

    assets = sum((total(rows) for t, rows in groups.items() if t not in LIABILITY_TYPES), Decimal('0'))
    liabilities = sum((total(groups[t]) for t in LIABILITY_TYPES), Decimal('0'))
    limited = [c for c in cards if c['limit']]
    credit_used = sum((c['owed'] for c in limited), Decimal('0'))
    credit_limit_total = sum((c['limit'] for c in limited), Decimal('0'))

    return {
        'groups': [{'type': t, 'label': label, 'total': total(groups[t]), 'liability': t in LIABILITY_TYPES,
                    'accounts': cards if t == 'credit' else sorted(
                        groups[t], key=lambda a: Decimal(a.get('current_balance') or 0), reverse=True)}
                   for t, label in ACCOUNT_GROUPS if groups[t]],
        'assets': assets,
        'liabilities': liabilities,
        'net_worth': assets - liabilities,
        'cash': total(groups['depository']),
        'credit_owed': total(groups['credit']),
        'credit_used': credit_used,
        'credit_limit': credit_limit_total,
        'credit_available': credit_limit_total - credit_used,
        'utilization': float(credit_used / credit_limit_total * 100) if credit_limit_total else None,
        'account_count': len(accounts),
    }
