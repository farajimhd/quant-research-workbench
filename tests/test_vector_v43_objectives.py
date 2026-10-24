import numpy as np
import pytest
from research.vectorized_backtest.v43.objectives import local_panel, score, positive_removal


def metrics(profits, tickers=None):
    total = sum(profits)
    return dict(net_pnl=[total], closed_positions=[len(profits)], drawdown=[0.],
        maximum_position_risk=[100.], maximum_stop_distance=[.1], terminal_valid=[True],
        stop_risk_dollar_seconds=[0.], capital_dollar_seconds=[0.],
        episode_profits=[profits], ticker_pnl=[tickers or {str(i): p for i, p in enumerate(profits)}])


def test_fraction_counts_only_positive_contributors_and_rounds_up():
    assert positive_removal([10., 20., 30., -100., 0.], .1) == (30., 1)
    assert positive_removal([-10., 0.], .1) == (0., 0)
    assert positive_removal([1.] * 11, .1) == (2., 2)


def test_same_profit_but_distributed_winners_score_higher_and_keep_losses():
    concentrated = local_panel([metrics([120., -10.])])
    distributed = local_panel([metrics([10.] * 12 + [-10.])])
    assert concentrated['pnl'][0, 0] == distributed['pnl'][0, 0] == 110.
    assert concentrated['values'][0, 0] == -10.
    assert distributed['values'][0, 0] == 90.


def test_single_day_does_not_remove_its_only_session():
    m = metrics([10.] * 12 + [-10.])
    result = score([m], [10])
    assert result['T_sessions'][0] == 0.
    assert result['session_penalty_available'] is False
    assert result['score'][0] == local_panel([m])['values'][0, 0] == 90.
    assert result['cost'][0] == -90.


def test_multiple_sessions_remove_largest_profitable_session():
    a = metrics([100. / 20] * 20, {str(i): 5. for i in range(20)})
    b = metrics([-10.], {'loss': -10.})
    result = score([a, b], [10])
    assert result['session_penalty_available'] is True
    assert result['T_sessions'][0] == 100.
    assert result['score'][0] == -10.


def test_ticker_concentration_is_diagnostic_only():
    result = local_panel([metrics([10.] * 12 + [-10.], {'one': 120., 'loss': -10.})])
    assert result['T_trades'][0, 0] == 20.
    assert result['T_tickers'][0, 0] == 120.
    assert result['values'][0, 0] == 90.


def test_preserves_original_financial_feasibility():
    m = metrics([5., -1.])
    m['maximum_position_risk'] = [301.]
    assert not local_panel([m])['eligible'][0, 0]


def test_rejects_episode_profit_or_count_mismatch():
    m = metrics([5., -1.])
    m['net_pnl'] = [100.]
    m['ticker_pnl'] = [{'x': 100.}]
    with pytest.raises(ValueError, match='Episode P&L'):
        local_panel([m])
    m = metrics([5., -1.])
    m['closed_positions'] = [3]
    with pytest.raises(ValueError, match='count'):
        local_panel([m])
