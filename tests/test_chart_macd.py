from datetime import datetime, timedelta
import unittest

from src.backend.chart_macd import NY, calendar_macd, period_bounds


class CalendarMacdTests(unittest.TestCase):
    def fixture(self):
        start = datetime(2023, 1, 2, tzinfo=NY)
        rows = []
        for i in range(900):
            day = (start + timedelta(days=i)).date()
            if day.weekday() >= 5:
                continue
            beginning, end = period_bounds(day, '1d')
            rows.append(dict(session_date=day.isoformat(), bar_start=beginning.isoformat(),
                             bar_end=end.isoformat(), bar_family='trade', close=10 + i / 100, is_closed=True))
        return dict(bars=rows, coverage_status='ready', split_adjusted=True, split_adjustments=[], source='canonical-test')

    def test_calendar_boundaries_and_dst(self):
        from datetime import date
        begin, end = period_bounds(date(2024, 3, 6), '1w')
        self.assertEqual((end.timestamp() - begin.timestamp()) / 3600, 167)
        self.assertEqual(period_bounds(date(2024, 2, 12), '1mo')[1].day, 1)
        self.assertEqual(period_bounds(date(2024, 2, 12), '1y')[1].year, 2025)

    def test_prefix_causality_all_calendar_timeframes(self):
        fixture = self.fixture()
        cutoff = datetime(2024, 6, 12, 12, tzinfo=NY)
        for timeframe in ['1d', '1w', '1mo', '1y']:
            output = calendar_macd(fixture, timeframe, cutoff)
            altered = dict(fixture, bars=[dict(row, close=999999) if row['session_date'] >= '2024-06-12' else row for row in fixture['bars']])
            self.assertEqual(output, calendar_macd(altered, timeframe, cutoff))
            self.assertTrue(all(row['end'] <= cutoff.timestamp() for row in output['rows']))
            for row in output['rows']:
                self.assertAlmostEqual(row['line'], row['fast']-row['slow'])

    def test_known_ema_vector_and_single_period_seed(self):
        fixture = self.fixture()
        fixture['bars'] = fixture['bars'][:3]
        for row, value in zip(fixture['bars'], [10, 12, 8]):
            row['close'] = value
        output = calendar_macd(fixture, '1d', datetime(2024, 1, 1, tzinfo=NY))
        self.assertEqual(output['rows'][0]['line'], 0)
        expected = (2 / 13 - 2 / 27) * 2
        self.assertAlmostEqual(output['rows'][1]['line'], expected)
        self.assertAlmostEqual(output['rows'][1]['signal'], .2 * expected)

    def test_missing_or_invalid_authority_rejected(self):
        fixture = self.fixture()
        cursor = datetime(2025, 6, 30, tzinfo=NY)
        for patch in [dict(coverage_status='partial'), dict(split_adjusted=False), dict(bars=fixture['bars'][:1]*2), dict(bars=[dict(fixture['bars'][0], close=float('nan'))])]:
            with self.assertRaises(ValueError):
                calendar_macd(dict(fixture, **patch), '1d', cursor)
