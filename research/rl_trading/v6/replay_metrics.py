"""Readable quote-bound V6 model replay accounting and risk metrics.

Marks are causal completed-candle closes. Stale marks are retained with an
explicit count and maximum age; they never stand in for fresh fills. Order
and position ledgers originate from the quote-bound OMS account.
"""
from __future__ import annotations

from datetime import datetime
import math
from zoneinfo import ZoneInfo

from research.rl_trading.v6.oms import BracketAccount


NY = ZoneInfo('America/New_York')
VERSION = 'rl-trading-v6-quote-bracket-replay-metrics-v1'


def _period(clock_us: int) -> str:
    local = datetime.fromtimestamp(clock_us / 1_000_000, NY)
    minute = local.hour * 60 + local.minute
    return ('premarket' if minute < 570 else
            'regular' if minute < 960 else 'after_hours')


class ReplayJournal:
    def __init__(self, account: BracketAccount):
        if account.sweep_teacher_profits:
            raise ValueError('Model replay must compound cash, not sweep gains')
        self.account = account
        self.equity_marks: list[dict] = []
        self._max_open = 0

    def mark(self, clock_us: int, prices: dict[str, float],
             price_clocks: dict[str, int]) -> None:
        """Record marked equity with the age of each held listing's close."""
        if self.equity_marks and clock_us <= self.equity_marks[-1]['clock_us']:
            raise ValueError('Replay marks must be chronological')
        ages = []
        for ticker in self.account.positions:
            observed = price_clocks.get(ticker)
            if (type(observed) is not int or observed > clock_us or
                    observed <= 0):
                raise ValueError('Held listing lacks a causal mark clock')
            ages.append(clock_us - observed)
        equity = self.account.marked_equity(prices)
        if not math.isfinite(equity) or equity < 0:
            raise ValueError('Invalid modeled replay equity')
        self._max_open = max(self._max_open, len(self.account.positions))
        self.equity_marks.append({
            'clock_us': clock_us, 'period': _period(clock_us),
            'equity': equity, 'cash': self.account.cash,
            'open_positions': len(self.account.positions),
            'stale_held_marks': sum(age > 1_000_000 for age in ages),
            'oldest_held_mark_age_us': max(ages, default=0),
        })

    def summary(self) -> dict:
        if not self.equity_marks:
            raise ValueError('No causal equity marks for model replay')
        values = [self.account.initial_cash] + [row['equity']
                  for row in self.equity_marks]
        peak = values[0]
        drawdown = 0.
        for value in values[1:]:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
        period_net = {'premarket': 0., 'regular': 0., 'after_hours': 0.}
        for before, after in zip(values[:-1], self.equity_marks):
            period_net[after['period']] += after['equity'] - before
        # `values` is initial plus all marks. The prior-equity iterator must
        # advance one mark at a time, even when a period boundary is crossed.
        fills = [order for order in self.account.orders
                 if order['filled_shares'] > 0 and
                 order['action'] not in ('set_stop', 'set_target')]
        buy = [order for order in fills if order['action'] == 'enter_long']
        sell = [order for order in fills if order['action'] != 'enter_long']
        traded = sum(order['filled_shares'] * order['price'] for order in fills)
        weighted_seconds = sum(row['shares'] *
            (row['exit_us'] - row['entry_us']) / 1_000_000
            for row in self.account.closed)
        closed_shares = sum(row['shares'] for row in self.account.closed)
        realized = sum(row['net_pnl'] for row in self.account.closed)
        # Partial exits are tranches, not independent trades. Only count a
        # ticker/entry pair once its entire holding is closed.
        open_keys={(ticker,position.entry_us) for ticker,position in self.account.positions.items()}
        trades={}
        for row in self.account.closed:
            key=(row['ticker'],row['entry_us'])
            if key not in open_keys:
                trades.setdefault(key,[]).append(row['net_pnl'])
        trade_net=[math.fsum(parts) for parts in trades.values()]
        wins=sum(net>1e-8 for net in trade_net)
        losses=sum(net< -1e-8 for net in trade_net)
        breakeven=len(trade_net)-wins-losses
        final_equity = values[-1]
        result = {'version': VERSION,
            'execution_scenario': 'optimistic_displayed_quote_and_price_level_upper_bound',
            'initial_cash': self.account.initial_cash,
            'final_marked_equity': final_equity,
            'modeled_net_profit': final_equity - self.account.initial_cash,
            'realized_net_profit': realized,
            'unrealized_net_profit': final_equity - self.account.initial_cash - realized,
            'modeled_fees': self.account.fees,
            'max_drawdown_fraction': drawdown,
            'buy_fill_orders': len(buy), 'sell_fill_orders': len(sell),
            'completed_position_tranches': len(self.account.closed),
            'fully_closed_positions':len(trade_net),
            'winning_positions':wins,'losing_positions':losses,
            'breakeven_positions':breakeven,
            'win_rate':wins/len(trade_net) if trade_net else None,
            'open_positions': len(self.account.positions),
            'max_open_positions': self._max_open,
            'terminally_flat': not self.account.positions,
            'turnover_dollars': traded,
            'turnover_over_initial_cash': traded / self.account.initial_cash,
            'share_weighted_closed_hold_seconds':
                weighted_seconds / closed_shares if closed_shares else None,
            'period_marked_net_profit': period_net,
            'stale_equity_mark_count': sum(
                row['stale_held_marks'] > 0 for row in self.equity_marks),
            'oldest_held_mark_age_us': max(
                row['oldest_held_mark_age_us'] for row in self.equity_marks),
            'equity_mark_count': len(self.equity_marks)}
        if not math.isclose(sum(period_net.values()),
                            result['modeled_net_profit'], abs_tol=1e-6):
            raise ValueError('Market-period P&L does not reconcile to equity')
        return result
