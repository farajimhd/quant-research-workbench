from research.rl_trading.v1.train_v5 import _qualified


def _report(**changes):
    report = dict(valid_terminal=True, buy_fills=2,
                  completed_position_rows=2, net_profit=100., max_drawdown=.1)
    report.update(changes)
    return report


def test_development_checkpoint_requires_real_trading_and_positive_net():
    assert _qualified([_report(net_profit=-20.), _report(net_profit=30.)])
    assert not _qualified([_report(net_profit=-120.), _report(net_profit=30.)])
    assert not _qualified([_report(), _report(buy_fills=0)])
    assert not _qualified([_report(), _report(valid_terminal=False)])
    assert not _qualified([_report(max_drawdown=float('nan'))])
