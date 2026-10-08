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


OTHER_SPEND = 'ALL_OTHER'


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    """First of the month ``n`` months after (or before, if negative) ``d``'s month."""
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def monthly_by_category(transactions, months, excluded=DEFAULT_EXCLUDED_CATEGORIES, top_n: int = 7) -> dict:
    """Net spend per category per month for each month start in ``months``.

    The ``top_n`` categories by total keep their own series; the rest fold into ``OTHER_SPEND``.
    """
    index = {m: i for i, m in enumerate(months)}
    by_cat = defaultdict(lambda: [Decimal('0')] * len(months))
    for t in transactions:
        i = index.get(month_start(t['date']))
        if i is None or not counts_toward_budget(t, excluded):
            continue
        by_cat[t.get('category_primary') or 'UNCATEGORIZED'][i] += Decimal(t['amount'])

    ranked = sorted(by_cat.items(), key=lambda kv: sum(kv[1]), reverse=True)
    top = [(c, v) for c, v in ranked if sum(v) > 0][:top_n]
    rest = [v for c, v in ranked if c not in dict(top)]
    series = list(top)
    if rest:
        series.append((OTHER_SPEND, [sum(col, Decimal('0')) for col in zip(*rest)]))

    totals = [sum(col, Decimal('0')) for col in zip(*by_cat.values())] if by_cat else [Decimal('0')] * len(months)
    total = sum(totals, Decimal('0'))
    return {
        'months': list(months),
        'series': [{'category': c, 'amounts': v, 'total': sum(v, Decimal('0'))} for c, v in series],
        'totals': totals,
        'total': total,
        'average': total / len(months) if months else Decimal('0'),
    }
