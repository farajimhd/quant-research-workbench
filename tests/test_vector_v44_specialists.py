import unittest
import numpy as np
from research.vectorized_backtest.v44.objectives import local_panel, panel_from_cases
from research.vectorized_backtest.v44.specialists import breeding_merit, specialist_order, admit


def metrics():
    return dict(net_pnl=[80., 0.], closed_positions=[3, 0],
        ticker_pnl=[{'a': 100., 'b': -20.}, {}], drawdown=[25., 0.],
        maximum_position_risk=[300., 0.], maximum_stop_distance=[.2, 0.],
        terminal_valid=[True, True], stop_risk_dollar_seconds=[0., 0.],
        capital_dollar_seconds=[0., 0.], episode_profits=[[100., -50., 30.], []],
        episode_listings=[[0, 1, 1], []], episode_exit_clocks=[[1, 2, 2], []])


class SpecialistTests(unittest.TestCase):
    def test_inactive_case_is_zero(self):
        panel = panel_from_cases(np.zeros((10, 2)), np.zeros((10, 2)))
        np.testing.assert_array_equal(panel['broad'], 0)
        np.testing.assert_array_equal(panel['specialist'], 0)

    def test_big_winner_is_preserved_but_broad_contribution_capped(self):
        panel = panel_from_cases([[1000., 10000.], [0., 0.]], np.zeros((2, 2)))
        self.assertEqual(panel['specialist'].tolist(), [1., 10.])
        self.assertEqual(panel['broad'].tolist(), [.5, .5])

    def test_combining_strengths_cannot_hide_large_other_losses(self):
        panel = panel_from_cases([[5854.], [868.], [-91398.]], np.zeros((3, 1)))
        self.assertLess(panel['broad'][0], 0)

    def test_merge_venues_and_aggregate_simultaneous_exits(self):
        panel = local_panel([metrics()], case_maps=[dict(day='2026-08-13',
            listing_ids=['a', 'b'], security_ids=['same', 'same'])])
        self.assertEqual(panel['case_pnl'].tolist(), [[80., 0.]])
        self.assertEqual(panel['case_drawdown'].tolist(), [[20., 0.]])
        self.assertAlmostEqual(panel['specialist'][0], .079)

    def test_original_financial_eligibility_is_mandatory(self):
        m = metrics(); m['maximum_position_risk'][0] = 301.
        p = local_panel([m], case_maps=[dict(day='d', listing_ids=['a', 'b'], security_ids=['a', 'b'])])
        self.assertEqual(p['eligible'].tolist(), [[False, True]])

    def test_missing_or_mismatched_cases_fail_closed(self):
        with self.assertRaises((ValueError, KeyError)):
            local_panel([metrics()], case_maps=[dict(day='d', listing_ids=['a', 'c'], security_ids=['a', 'b'])])

    def test_specialist_elites_cover_distinct_cases_first(self):
        self.assertEqual(specialist_order([10., 9., 2.], ['a', 'a', 'b'], [0, 1, 2], [True]*3), [0, 2, 1])

    def test_both_selection_routes_receive_merit(self):
        merit = breeding_merit([10., 0., 2.], [0., 10., 2.], [0, 1, 2])
        self.assertEqual(merit.tolist(), [3., 3., 2.])

    def test_bad_metric_reconciliation_fails_closed(self):
        m = metrics(); m['episode_profits'][0][0] += 1
        with self.assertRaises(ValueError):
            local_panel([m], case_maps=[dict(day='d', listing_ids=['a', 'b'], security_ids=['a', 'b'])])


if __name__ == '__main__':
    unittest.main()
