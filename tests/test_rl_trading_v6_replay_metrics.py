from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from research.rl_trading.v6.oms import BracketAccount, Quote
from research.rl_trading.v6.replay_metrics import ReplayJournal


def _clock(hour, minute, second):
    return int(datetime(2026, 8, 5, hour, minute, second,
                        tzinfo=ZoneInfo('America/New_York')).timestamp()
               * 1_000_000)


def test_replay_period_pnl_fees_stale_marks_and_terminal_reconcile():
    account = BracketAccount()
    journal = ReplayJournal(account)
    start = _clock(9, 29, 57)
    journal.mark(start, {}, {})
    enter = _clock(9, 29, 58)
    bought = account.enter_long('ABC', decision_us=enter - 100_000,
        decision_close=10., budget=1000.,
        quote=Quote(enter, enter - 50_000, 9.9, 10., 100., 100., True))
    assert bought > 0
    journal.mark(enter, {'ABC': 10.}, {'ABC': enter})
    stale = _clock(9, 30, 0)
    journal.mark(stale, {'ABC': 10.}, {'ABC': enter})
    exit_clock = _clock(9, 30, 2)
    sold = account.exit_long('ABC', decision_us=exit_clock - 100_000,
        quote=Quote(exit_clock, exit_clock - 50_000,
                    11., 11.1, 100., 100., True))
    assert sold == bought
    journal.mark(exit_clock, {}, {})
    report = journal.summary()
    assert report['terminally_flat'] and report['open_positions'] == 0
    assert report['modeled_net_profit'] == pytest.approx(
        report['realized_net_profit'])
    assert report['modeled_fees'] > 0
    assert report['stale_equity_mark_count'] == 1
    assert report['buy_fill_orders'] == report['sell_fill_orders'] == 1
    assert report['win_rate']==1 and report['winning_positions']==1
    assert sum(report['period_marked_net_profit'].values()) == pytest.approx(
        report['modeled_net_profit'])


def test_win_rate_combines_partial_exits_and_excludes_open_remainder():
    from types import SimpleNamespace
    account=BracketAccount()
    account.closed=[{'ticker':ticker,'entry_us':entry,'exit_us':entry+1_000_000,
                    'shares':1,'net_pnl':net}
        for ticker,entry,net in [('A',1,4.),('A',1,-5.),('B',2,2.),
                                 ('C',3,0.),('D',4,50.)]]
    account.positions={'D':SimpleNamespace(entry_us=4)}
    journal=ReplayJournal(account)
    # Isolate trade grouping from the existing marked-equity accounting tests.
    journal.equity_marks=[{'equity':10000.,'period':'regular','stale_held_marks':0,
                          'oldest_held_mark_age_us':0}]
    result=journal.summary()
    assert result['completed_position_tranches']==5
    assert result['fully_closed_positions']==3
    assert result['winning_positions']==result['losing_positions']==result['breakeven_positions']==1
    assert result['win_rate']==pytest.approx(1/3)


def test_win_rate_is_undefined_without_fully_closed_positions():
    account=BracketAccount()
    journal=ReplayJournal(account)
    journal.mark(_clock(9,30,0),{}, {})
    assert journal.summary()['win_rate'] is None
