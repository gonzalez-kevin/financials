import unittest
from datetime import date
from decimal import Decimal

from jobs.daily import build_summary, render_html, render_subject, render_text


def tx(d, amount, cat='FOOD_AND_DRINK', name='Shop', merchant=None, pending=False):
    return {'date': d, 'amount': Decimal(str(amount)), 'category_primary': cat,
            'name': name, 'merchant_name': merchant, 'pending': pending}


EXCLUDED = ('TRANSFER_OUT', 'LOAN_PAYMENTS', 'INCOME')


class BuildSummaryTest(unittest.TestCase):
    def test_yesterday_and_week_to_date(self):
        txs = [
            tx(date(2026, 9, 27), 100, merchant='Costco'),
            tx(date(2026, 10, 2), 40, merchant='Chipotle'),
            tx(date(2026, 10, 2), 15, merchant='Starbucks', pending=True),
            tx(date(2026, 10, 2), 500, cat='LOAN_PAYMENTS', name='Card payment'),
            tx(date(2026, 10, 2), 250, cat='TRAVEL', merchant='United'),
        ]
        s = build_summary(txs, date(2026, 10, 2), Decimal('1200'), EXCLUDED, Decimal('200'))
        self.assertEqual(s['day_total'], Decimal('305'))
        self.assertEqual([t['merchant_name'] for t in s['day_txs']], ['United', 'Chipotle', 'Starbucks'])
        self.assertEqual(s['week']['total'], Decimal('405'))
        self.assertEqual(s['week']['days_elapsed'], 6)
        self.assertEqual([t['merchant_name'] for t in s['large']], ['United'])

    def test_saturday_report_covers_full_week(self):
        # Email sent Sunday morning reports on Saturday, the last day of the previous week.
        txs = [tx(date(2026, 9, 27), 1300), tx(date(2026, 10, 4), 99)]
        s = build_summary(txs, date(2026, 10, 3), Decimal('1200'), EXCLUDED, Decimal('200'))
        self.assertEqual(s['week']['start'], date(2026, 9, 27))
        self.assertEqual(s['week']['total'], Decimal('1300'))
        self.assertTrue(s['week']['over_budget'])
        self.assertEqual(s['day_total'], Decimal('0'))

    def test_render_includes_key_facts_and_escapes_html(self):
        txs = [tx(date(2026, 10, 2), 40, merchant='<Tom & Jerry>')]
        s = build_summary(txs, date(2026, 10, 2), Decimal('1200'), EXCLUDED, Decimal('200'))
        subject = render_subject(s)
        text = render_text(s, ['Chase failed'], 'https://example.run.app')
        body = render_html(s, ['Chase failed'], 'https://example.run.app')
        self.assertIn('$40.00', subject)
        self.assertIn('$1,160.00 left', subject)
        self.assertIn('Chase failed', text)
        self.assertIn('https://example.run.app', text)
        self.assertIn('&lt;Tom &amp; Jerry&gt;', body)
        self.assertNotIn('<Tom & Jerry>', body)


if __name__ == '__main__':
    unittest.main()
