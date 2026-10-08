import unittest
from datetime import date
from decimal import Decimal

from dashboard.finance import credit_limit, summarize_accounts, summarize_week, week_start, weekly_totals


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



def acct(type_, current, available=None, limit=None, name='Acct', subtype=None):
    return {'type': type_, 'subtype': subtype, 'name': name, 'current_balance': Decimal(str(current)),
            'available_balance': None if available is None else Decimal(str(available)),
            'credit_limit': None if limit is None else Decimal(str(limit))}


class SummarizeAccountsTest(unittest.TestCase):
    def test_net_worth_cash_and_credit(self):
        s = summarize_accounts([
            acct('depository', 5000, 4900, name='Checking'),
            acct('depository', 10000, name='Savings'),
            acct('investment', 2500),
            acct('credit', 1200, 8800, 10000, name='Amex'),
            acct('credit', 300, 4700, name='Chase'),        # no limit from Plaid: inferred 5000
            acct('loan', 7000),
        ])
        self.assertEqual(s['cash'], Decimal('15000'))
        self.assertEqual(s['assets'], Decimal('17500'))
        self.assertEqual(s['credit_owed'], Decimal('1500'))
        self.assertEqual(s['liabilities'], Decimal('8500'))
        self.assertEqual(s['net_worth'], Decimal('9000'))
        self.assertEqual(s['credit_limit'], Decimal('15000'))
        self.assertEqual(s['credit_available'], Decimal('13500'))
        self.assertAlmostEqual(s['utilization'], 10.0)
        self.assertEqual([g['type'] for g in s['groups']], ['depository', 'investment', 'credit', 'loan'])
        cards = s['groups'][2]['accounts']
        self.assertEqual([c['name'] for c in cards], ['Amex', 'Chase'])
        self.assertAlmostEqual(cards[0]['pct'], 12.0)

    def test_card_without_any_limit_is_left_out_of_utilization(self):
        s = summarize_accounts([acct('credit', 400), acct('credit', 100, 900, name='B')])
        self.assertEqual(s['credit_owed'], Decimal('500'))
        self.assertEqual(s['credit_limit'], Decimal('1000'))
        self.assertAlmostEqual(s['utilization'], 10.0)
        self.assertIsNone(s['groups'][0]['accounts'][0]['pct'])

    def test_empty_and_unknown_types(self):
        self.assertIsNone(summarize_accounts([])['utilization'])
        s = summarize_accounts([{'type': None, 'name': 'X', 'current_balance': None}])
        self.assertEqual(s['groups'][0]['type'], 'other')
        self.assertEqual(s['net_worth'], Decimal('0'))

    def test_credit_limit_prefers_plaid_limit(self):
        self.assertEqual(credit_limit(acct('credit', 100, 50, 2000)), Decimal('2000'))
        self.assertEqual(credit_limit(acct('credit', 100, 900)), Decimal('1000'))
        self.assertIsNone(credit_limit(acct('credit', 0, 0)))


if __name__ == '__main__':
    unittest.main()
