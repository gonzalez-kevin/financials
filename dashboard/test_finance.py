import unittest
from datetime import date
from decimal import Decimal

from dashboard.finance import (
    OTHER_SPEND, add_months, monthly_by_category, summarize_accounts, summarize_week,
    week_start, weekly_totals,
)


def tx(d, amount, cat='FOOD_AND_DRINK', name='Shop', merchant=None):
    return {'date': d, 'amount': Decimal(str(amount)), 'category_primary': cat,
            'name': name, 'merchant_name': merchant}


class WeekStartTest(unittest.TestCase):
    def test_sunday_is_its_own_start(self):
        self.assertEqual(week_start(date(2026, 9, 27)), date(2026, 9, 27))

    def test_saturday_maps_to_previous_sunday(self):
        self.assertEqual(week_start(date(2026, 10, 3)), date(2026, 9, 27))

    def test_thursday(self):
        self.assertEqual(week_start(date(2026, 10, 1)), date(2026, 9, 27))


class SummarizeWeekTest(unittest.TestCase):
    start = date(2026, 9, 27)

    def test_totals_exclude_transfers_and_net_refunds(self):
        txs = [
            tx(date(2026, 9, 27), 100, merchant='Costco'),
            tx(date(2026, 9, 28), 50, cat='ENTERTAINMENT'),
            tx(date(2026, 9, 28), -20, merchant='Costco'),        # refund
            tx(date(2026, 9, 29), 900, cat='LOAN_PAYMENTS'),     # card payment
            tx(date(2026, 9, 29), -3000, cat='INCOME'),          # paycheck
            tx(date(2026, 10, 4), 500),                          # next week
        ]
        s = summarize_week(txs, self.start, date(2026, 10, 1), Decimal('1200'))
        self.assertEqual(s['total'], Decimal('130'))
        self.assertEqual(s['remaining'], Decimal('1070'))
        self.assertEqual(s['days_elapsed'], 5)
        self.assertEqual(s['days_left'], 2)
        self.assertEqual(s['per_day_left'], Decimal('535'))
        self.assertEqual([t['amount'] for t in s['top_expenses']], [Decimal('100'), Decimal('50')])
        self.assertEqual(s['categories'][0], ('FOOD_AND_DRINK', Decimal('80')))
        self.assertEqual(s['merchants'][0], ('Costco', Decimal('80'), 2))
        self.assertEqual(s['daily'][0]['amount'], Decimal('100'))
        self.assertEqual(s['biggest_day'], 'Sun')
        self.assertFalse(s['over_budget'])

    def test_over_budget_and_past_week(self):
        s = summarize_week([tx(self.start, 1500)], self.start, date(2026, 10, 10), Decimal('1200'))
        self.assertTrue(s['over_budget'])
        self.assertEqual(s['days_left'], 0)
        self.assertEqual(s['per_day_left'], Decimal('0'))

    def test_empty_week(self):
        s = summarize_week([], self.start, date(2026, 9, 27), Decimal('1200'))
        self.assertEqual(s['total'], Decimal('0'))
        self.assertIsNone(s['biggest_day'])
        self.assertEqual(s['top_expenses'], [])

    def test_weekly_totals(self):
        txs = [tx(date(2026, 9, 26), 10), tx(date(2026, 9, 27), 20), tx(date(2026, 9, 30), 5, cat='TRANSFER_OUT')]
        res = weekly_totals(txs, [date(2026, 9, 20), date(2026, 9, 27)])
        self.assertEqual([r['total'] for r in res], [Decimal('10'), Decimal('20')])


class MonthlyTest(unittest.TestCase):
    def test_add_months_wraps_years(self):
        self.assertEqual(add_months(date(2026, 10, 8), -11), date(2025, 11, 1))
        self.assertEqual(add_months(date(2026, 12, 31), 1), date(2027, 1, 1))
        self.assertEqual(add_months(date(2026, 1, 15), 0), date(2026, 1, 1))

    def test_monthly_by_category_nets_and_excludes(self):
        months = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
        txs = [
            tx(date(2026, 8, 31), 100),
            tx(date(2026, 9, 1), 40),
            tx(date(2026, 9, 2), -10),                       # refund
            tx(date(2026, 9, 3), 60, cat='ENTERTAINMENT'),
            tx(date(2026, 10, 1), 900, cat='LOAN_PAYMENTS'),  # card payment
            tx(date(2026, 7, 31), 500),                      # outside range
        ]
        m = monthly_by_category(txs, months)
        self.assertEqual([s['category'] for s in m['series']], ['FOOD_AND_DRINK', 'ENTERTAINMENT'])
        self.assertEqual(m['series'][0]['amounts'], [Decimal('100'), Decimal('30'), Decimal('0')])
        self.assertEqual(m['totals'], [Decimal('100'), Decimal('90'), Decimal('0')])
        self.assertEqual(m['total'], Decimal('190'))

    def test_small_categories_fold_into_other(self):
        months = [date(2026, 9, 1)]
        txs = [tx(date(2026, 9, 5), 30, cat='A'), tx(date(2026, 9, 5), 20, cat='B'), tx(date(2026, 9, 5), 5, cat='C')]
        m = monthly_by_category(txs, months, top_n=1)
        self.assertEqual([(s['category'], s['total']) for s in m['series']],
                         [('A', Decimal('30')), (OTHER_SPEND, Decimal('25'))])
        self.assertEqual(m['totals'], [Decimal('55')])
        self.assertEqual([(o['category'], o['total']) for o in m['other']], [('B', Decimal('20')), ('C', Decimal('5'))])

    def test_net_credit_category_is_listed_under_other(self):
        months = [date(2026, 8, 1), date(2026, 9, 1)]
        txs = [tx(date(2026, 8, 5), 100), tx(date(2026, 9, 5), -40, cat='TRAVEL')]   # e.g. a statement credit
        m = monthly_by_category(txs, months)
        self.assertEqual(m['series'][-1], {'category': OTHER_SPEND, 'amounts': [Decimal('0'), Decimal('-40')],
                                           'total': Decimal('-40')})
        self.assertEqual(m['other'], [{'category': 'TRAVEL', 'amounts': [Decimal('0'), Decimal('-40')],
                                       'total': Decimal('-40')}])

    def test_loan_disbursements_are_not_spending(self):
        m = monthly_by_category([tx(date(2026, 9, 5), 50), tx(date(2026, 9, 6), -2000, cat='LOAN_DISBURSEMENTS')],
                                [date(2026, 9, 1)])
        self.assertEqual(m['totals'], [Decimal('50')])
        self.assertEqual(m['other'], [])

    def test_empty(self):
        m = monthly_by_category([], [date(2026, 9, 1), date(2026, 10, 1)])
        self.assertEqual(m['series'], [])
        self.assertEqual(m['other'], [])
        self.assertEqual(m['totals'], [Decimal('0'), Decimal('0')])


def acct(type_, current, available=None, name='Acct', subtype=None):
    return {'type': type_, 'subtype': subtype, 'name': name, 'current_balance': Decimal(str(current)),
            'available_balance': None if available is None else Decimal(str(available))}


class SummarizeAccountsTest(unittest.TestCase):
    def test_net_worth_cash_and_credit(self):
        s = summarize_accounts([
            acct('depository', 5000, 4900, name='Checking'),
            acct('depository', 10000, name='Savings'),
            acct('investment', 2500),
            acct('credit', 300, 4700, name='Chase'),
            acct('credit', 1200, name='Amex'),
            acct('loan', 7000),
        ])
        self.assertEqual(s['cash'], Decimal('15000'))
        self.assertEqual(s['assets'], Decimal('17500'))
        self.assertEqual(s['credit_owed'], Decimal('1500'))
        self.assertEqual(s['liabilities'], Decimal('8500'))
        self.assertEqual(s['net_worth'], Decimal('9000'))
        self.assertEqual([g['type'] for g in s['groups']], ['depository', 'investment', 'credit', 'loan'])
        self.assertEqual([c['name'] for c in s['groups'][2]['accounts']], ['Amex', 'Chase'])
        self.assertNotIn('utilization', s)

    def test_empty_and_unknown_types(self):
        self.assertEqual(summarize_accounts([])['groups'], [])
        s = summarize_accounts([{'type': None, 'name': 'X', 'current_balance': None}])
        self.assertEqual(s['groups'][0]['type'], 'other')
        self.assertEqual(s['net_worth'], Decimal('0'))


if __name__ == '__main__':
    unittest.main()
