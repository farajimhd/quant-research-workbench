import unittest
import numpy as np
from research.vectorized_backtest.v45.allocation import budgets
from research.vectorized_backtest.v45.objectives import local_panel, panel_from_cases
from research.vectorized_backtest.v45.specialists import rank_rounds, case_groups, case_quotas


def metrics():
    return dict(net_pnl=[80., 0.], closed_positions=[3, 0],
        ticker_pnl=[{'a': 100., 'b': -20.}, {}], drawdown=[25., 0.],
        maximum_position_risk=[300., 0.], maximum_stop_distance=[.2, 0.],
        terminal_valid=[True, True], stop_risk_dollar_seconds=[0., 0.],
        capital_dollar_seconds=[0., 0.], episode_profits=[[100., -50., 30.], []],
        episode_listings=[[0, 1, 1], []], episode_exit_clocks=[[1, 2, 2], []])


class SelectionTests(unittest.TestCase):
    def test_exact_approved_population(self):
        self.assertEqual(budgets(), dict(elites=102, fresh=205, global_crossovers=358, case_crossovers=359))
        for n in (10, 31, 1024, 65536):
            self.assertEqual(sum(budgets(n).values()), n)

    def test_case_score_preserves_winners_without_outside_penalty(self):
        p = panel_from_cases([[10000.], [-9000.]], [[100.], [9000.]])
        self.assertEqual(p['case_scores'].tolist(), [[9995.], [-9450.]])
        self.assertEqual(p['global_profit'].tolist(), [1000.])

    def test_inactive_is_zero(self):
        p = panel_from_cases(np.zeros((3, 2)), np.zeros((3, 2)))
        np.testing.assert_array_equal(p['case_scores'], 0)

    def test_case_merge_and_simultaneous_close_drawdown(self):
        p = local_panel([metrics()], case_maps=[dict(day='d', listing_ids=['a', 'b'], security_ids=['same', 'same'])])
        self.assertEqual(p['case_scores'].tolist(), [[79., 0.]])
        self.assertEqual(p['values'].tolist(), [[80., 0.]])

    def test_financial_gate_preserved(self):
        m = metrics(); m['maximum_position_risk'][0] = 301.
        p = local_panel([m], case_maps=[dict(day='d', listing_ids=['a', 'b'], security_ids=['a', 'b'])])
        self.assertEqual(p['eligible'].tolist(), [[False, True]])

    def test_reconciliation_fail_closed(self):
        m = metrics(); m['episode_profits'][0][0] += 1
        with self.assertRaises(ValueError):
            local_panel([m], case_maps=[dict(day='d', listing_ids=['a', 'b'], security_ids=['a', 'b'])])

    def test_each_case_first_before_deeper_ranks(self):
        scores = [[10, 9, 8, 0], [0, 0, 0, 1]]
        selected, _ = rank_rounds(scores, [True]*4, [0, 1, 2, 3], 3, 1)
        self.assertEqual(selected, [0, 3, 1])

    def test_duplicates_do_not_consume_slots_or_case_memberships(self):
        scores = [[10, 9, 8], [10, 9, 7]]
        selected, niches = rank_rounds(scores, [True]*3, [0, 1, 2], 2, 1, ['same', 'same', 'other'])
        self.assertEqual(selected, [0, 2])
        self.assertTrue(any(r['case_index'] == 1 and r['candidate'] == 0 for r in niches))

    def test_negative_and_ineligible_never_fill_case_archive(self):
        selected, _ = rank_rounds([[10, 0, -1]], [False, True, True], [0, 1, 2], 3, 1)
        self.assertEqual(selected, [])

    def test_positive_rank_shortage_does_not_fill_with_losers(self):
        selected, _ = rank_rounds([[10, 2, -1]], [True]*3, [0, 1, 2], 3, 1)
        self.assertEqual(selected, [0, 1])

    def test_case_quotas_are_equal_and_remainders_rotate(self):
        totals = {i: 0 for i in range(4)}
        for generation in range(1, 5):
            q = case_quotas(range(4), 359, generation)
            self.assertEqual(sum(q.values()), 359)
            self.assertLessEqual(max(q.values()) - min(q.values()), 1)
            for c, n in q.items(): totals[c] += n
        self.assertEqual(len(set(totals.values())), 1)

    def test_case_pool_does_not_include_lower_unvisited_ranks(self):
        scores = [[10, 9, 8], [1, 2, 10]]
        selected, niches = rank_rounds(scores, [True]*3, [0, 1, 2], 2, 1)
        groups = case_groups(scores, [True]*3, [0, 1, 2], set(selected), niches)
        self.assertEqual(groups[0].tolist(), [0])
        self.assertEqual(groups[1].tolist(), [2])


if __name__ == '__main__':
    unittest.main()
