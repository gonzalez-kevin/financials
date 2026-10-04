import unittest
from datetime import date
from decimal import Decimal

from dashboard.finance import summarize_week, week_start, weekly_totals


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


if __name__ == '__main__':
    unittest.main()
