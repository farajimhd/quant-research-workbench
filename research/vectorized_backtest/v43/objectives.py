"""Profit surviving the largest winning contributors; never discard losses."""
from dataclasses import dataclass, asdict
import math
import numpy as np
from research.vectorized_backtest.v42 import objectives as base


@dataclass(frozen=True)
class Objective:
    trade_trim_fraction: float = .10
    ticker_trim_fraction: float = .10
    session_trim_fraction: float = .10
    drawdown_weight: float = .05
    complexity_dollars_per_node: float = 0.

    def validate(self):
        if any(not math.isfinite(x) or x < 0 for x in asdict(self).values()):
            raise ValueError('Finite nonnegative objective required')
        if any(not 0 < x < 1 for x in (self.trade_trim_fraction,
                                       self.ticker_trim_fraction, self.session_trim_fraction)):
            raise ValueError('Trim fractions must be between zero and one')
        if self.complexity_dollars_per_node != 0:
            raise ValueError('No complexity penalty')
        return self


def positive_removal(values, fraction):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError('Finite one-dimensional contributor profits required')
    positive = values[values > 0]
    k = math.ceil(fraction * len(positive))
    if not k:
        return 0., 0
    return float(np.partition(positive, len(positive) - k)[-k:].sum()), k


def _episodes(metrics, candidate, expected):
    profits = metrics['episode_profits'][candidate]
    if len(profits) != metrics['closed_positions'][candidate]:
        raise ValueError('Complete position episode count does not reconcile')
    if not np.isfinite(profits).all() or not np.isclose(sum(profits), expected, rtol=1e-10, atol=1e-7):
        raise ValueError('Episode P&L does not reconcile')
    return profits


def local_panel(ordered, config=Objective()):
    config.validate()
    # Reuse all original terminal, P&L reconciliation and financial risk checks.
    result = base.local_panel(ordered, base.Objective())
    pnl = result['pnl']
    trades = np.zeros_like(pnl)
    tickers = np.zeros_like(pnl)
    trade_counts = np.zeros_like(pnl)
    ticker_counts = np.zeros_like(pnl)
    for d, metrics in enumerate(ordered):
        for i in range(pnl.shape[1]):
            trades[d, i], trade_counts[d, i] = positive_removal(
                _episodes(metrics, i, pnl[d, i]), config.trade_trim_fraction)
            tickers[d, i], ticker_counts[d, i] = positive_removal(
                list(metrics['ticker_pnl'][i].values()), config.ticker_trim_fraction)
    deduction = np.maximum(trades, tickers)
    result.update(values=pnl - deduction - config.drawdown_weight * result['drawdown'],
                  trimmed=pnl - deduction, T_trades=trades, T_tickers=tickers,
                  removed_trade_count=trade_counts, removed_ticker_count=ticker_counts,
                  concentration_deduction=deduction, session_penalty_available=False)
    return result


def score(ordered, complexity, config=Objective()):
    local = local_panel(ordered, config)
    result = base.score(ordered, complexity, base.Objective())
    pnl = local['pnl']
    days, count = pnl.shape
    trades = np.zeros(count)
    tickers = np.zeros(count)
    sessions = np.zeros(count)
    for i in range(count):
        all_trades = [p for m in ordered for p in m['episode_profits'][i]]
        ticker_totals = {}
        for m in ordered:
            for ticker, value in m['ticker_pnl'][i].items():
                ticker_totals[ticker] = ticker_totals.get(ticker, 0.) + value
        trades[i], _ = positive_removal(all_trades, config.trade_trim_fraction)
        tickers[i], _ = positive_removal(list(ticker_totals.values()), config.ticker_trim_fraction)
        if days > 1:
            sessions[i], _ = positive_removal(pnl[:, i], config.session_trim_fraction)
    deduction = np.maximum.reduce([trades, tickers, sessions])
    drawdown = config.drawdown_weight * local['drawdown'].sum(0)
    values = result['total_pnl'] - deduction - drawdown
    result.update(score=values, cost=-values, trimmed_profit=result['total_pnl'] - deduction,
                  concentration_deduction=deduction, T_trades=trades, T_tickers=tickers,
                  T_sessions=sessions, session_penalty_available=days > 1,
                  components=dict(net_profit=result['total_pnl'], concentration_penalty=deduction,
                                  drawdown_penalty=drawdown), _local=local)
    return result
