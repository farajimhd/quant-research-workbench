"""Local hindsight labels, represented exactly for all fractional action sizes.

No future reallocations, account simulation, learning, or broker execution.
Values are incremental profit above liquidating the current holdings now.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import polars as pl

VERSION = "hindsight-greedy-fractional-v3"
MODES = {"long": ("long",), "short": ("short",), "long_short": ("long", "short")}
DEFAULT_HALF_LIFE_BARS = 30.0


def discount_policy(macd_resolution_seconds=1., *, half_life_bars=None, gamma=None):
    """Use a fixed horizon in MACD bars, never future realized episode length."""
    if not math.isfinite(macd_resolution_seconds) or macd_resolution_seconds <= 0:
        raise ValueError('MACD resolution must be positive and finite')
    if gamma is not None:
        if half_life_bars is not None:
            raise ValueError('Use half-life-bars OR an explicit per-second gamma')
        if not math.isfinite(gamma) or not 0 < gamma <= 1:
            raise ValueError('gamma must be finite and in (0, 1]')
        return dict(mode='explicit_gamma_per_second',gamma_per_second=gamma,
            macd_resolution_seconds=macd_resolution_seconds,
            half_life_bars=None,half_life_seconds=None if gamma == 1 else math.log(.5)/math.log(gamma))
    bars = DEFAULT_HALF_LIFE_BARS if half_life_bars is None else half_life_bars
    seconds = bars*macd_resolution_seconds
    if not math.isfinite(bars) or bars <= 0 or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Discount half-life must be positive and finite')
    per_second = math.exp(math.log(.5)/seconds)
    if not 0 < per_second < 1:
        raise ValueError('Discount half-life is outside supported floating-point precision')
    return dict(mode='macd_bar_half_life',gamma_per_second=per_second,
        macd_resolution_seconds=macd_resolution_seconds,half_life_bars=bars,half_life_seconds=seconds)


def coefficients(frame: pl.DataFrame, gamma: float | None = None,
                 cost_per_share: float = 0.0, *, valuation_basis: str = 'quotes',
                 macd_resolution_seconds: float = 1., half_life_bars: float | None = None) -> pl.DataFrame:
    """O(rows) vectorized compilation; keep unavailable outcomes explicitly null."""
    gamma = discount_policy(macd_resolution_seconds,half_life_bars=half_life_bars,gamma=gamma)['gamma_per_second']
    if not math.isfinite(cost_per_share) or cost_per_share < 0:
        raise ValueError("cost_per_share must be finite and nonnegative")
    if valuation_basis not in ('quotes','price_action'):
        raise ValueError('Unknown valuation basis')
    terminal = pl.col('session_terminal') if valuation_basis == 'price_action' and 'session_terminal' in frame.columns else pl.lit(False)
    out = []
    for side, sign in (("long", 1), ("short", -1)):
        price_only = valuation_basis == 'price_action'
        entry_observation = pl.col('decision_price' if price_only else 'ask' if side == 'long' else 'bid')
        close_observation = pl.col('decision_price' if price_only else 'bid' if side == 'long' else 'ask')
        target_observation = pl.col(f'{side}_target_price' if price_only else f'{side}_' + ('bid' if side == 'long' else 'ask'))
        entry = entry_observation + sign * cost_per_share
        close = close_observation - sign * cost_per_share
        target = target_observation - sign * cost_per_share
        capital = entry_observation + cost_per_share
        # Eligibility is based on current observations, never future profitability.
        observation_ok = ((pl.col('price_valid') & (entry_observation > 0) & entry_observation.is_finite()) if price_only else
            (pl.col('quote_valid') & (pl.col('ask_size') >= 1) & (pl.col('bid_size') >= 1))).fill_null(False)
        new_ok = (observation_ok & ~terminal & (entry > 0) & (capital > 0)
                  & entry.is_finite() & capital.is_finite()).fill_null(False)
        hold = pl.col(f"{side}_hold_seconds")
        future_ok = ((pl.col(f"{side}_status") == "available") & ~terminal
                     & hold.is_finite() & (hold > 0) & target.is_finite()
                     & target_observation.is_finite() & (target_observation > 0)).fill_null(False)
        available = future_ok & new_ok
        hold_available = future_ok & observation_ok & close.is_finite()
        discount = pl.lit(gamma).pow(hold)
        item = frame.select(
            "time_us", "ticker", "listing_id", pl.lit(side).alias("side"),
            pl.col(f"{side}_status").alias("phase1_status"),
            pl.col(f"{side}_target_us").alias("target_us"),
            pl.col(f"{side}_target_id").alias("target_id"),
            pl.col(f"{side}_available_us").alias("label_available_us"),
            observation_ok.alias("can_close"), new_ok.alias("can_open"),
            terminal.alias('session_terminal'),
            available.alias("value_available"),
            available.alias('open_value_available'), hold_available.alias('hold_value_available'),
            entry.alias("entry_price"), close.alias("close_price"),
            capital.alias("capital_per_share"), target.alias("target_price"),
            hold.alias("hold_seconds"),
            pl.when(available | hold_available).then(discount).alias("discount"),
            pl.when(available).then(sign * (target - entry)).alias("open_profit_per_share"),
            pl.when(hold_available).then(sign * (target - close)).alias("hold_profit_per_share"),
            pl.when(available).then(sign * (target - entry) * discount).alias("open_value_per_share"),
            pl.when(hold_available).then(sign * (target - close) * discount).alias("hold_value_per_share"),
        ).with_columns(
            (pl.col("open_value_per_share") / pl.col("capital_per_share")).alias("open_value_per_dollar"),
            pl.when(terminal).then(pl.lit('session_liquidation'))
            .when(~pl.col("can_open")).then(pl.lit('current_price_or_cost_unavailable' if price_only else 'current_quote_or_cost_unavailable'))
            .when(~pl.col("value_available") & (pl.col("phase1_status") == "available"))
            .then(pl.lit("invalid_target_or_duration"))
            .otherwise(pl.col("phase1_status")).alias("status"),
        )
        out.append(item)
    return pl.concat(out)


@dataclass(frozen=True)
class Position:
    ticker: str
    side: str
    quantity: float
    entry_price: float
    capital_per_share: float

    @property
    def key(self) -> str:
        return f"{self.ticker}:{self.side}"


class ActionTable:
    """One timestamp's complete piecewise-linear action-value function.

    A mapping key is ticker:side; its value is the change in shares, not a target
    holding. Missing keys mean hold. Reduction cannot exceed the current holding.
    Synthetic funding is applied ONCE before evaluating any alternatives.
    """

    def __init__(self, rows: list[dict], positions: list[Position], *,
                 mode: str = "long_short"):
        if mode not in MODES:
            raise ValueError("mode must be long, short, or long_short")
        self.rows = {}
        times = set()
        identities = {}
        self.mode = mode
        for row in rows:
            if row["side"] not in ("long", "short"):
                raise ValueError("Unknown direction")
            if row["side"] not in MODES[mode]:
                continue
            key = f"{row['ticker']}:{row['side']}"
            if key in self.rows:
                raise ValueError(f"Duplicate opportunity: {key}")
            times.add(row["time_us"])
            identity = row.get("listing_id", row["ticker"])
            if identities.setdefault(row["ticker"], identity) != identity:
                raise ValueError("Ambiguous ticker/listing identity")
            self.rows[key] = row
        if len(times) != 1:
            raise ValueError("Action table requires exactly one decision timestamp")
        self.time_us = next(iter(times))
        self.positions = {}
        held_tickers = set()
        for pos in positions:
            if pos.key in self.positions or pos.ticker in held_tickers:
                raise ValueError("Use one aggregated position per ticker; no simultaneous long/short")
            if pos.key not in self.rows:
                raise ValueError(f"Held position missing from snapshot: {pos.key}")
            if any(not math.isfinite(x) or x <= 0 for x in
                   (pos.quantity, pos.entry_price, pos.capital_per_share)):
                raise ValueError("Position size, entry price and capital must be positive and finite")
            if pos.side == "long" and not math.isclose(pos.entry_price, pos.capital_per_share):
                raise ValueError("Long capital must equal its all-in entry price")
            if pos.side == "short" and pos.capital_per_share < pos.entry_price:
                raise ValueError("Short reserve cannot be less than its net entry price")
            self.positions[pos.key] = pos
            held_tickers.add(pos.ticker)
        self.open_cost = math.fsum(p.quantity * p.capital_per_share for p in positions)
        self.max_new_price = max((r["capital_per_share"] for r in self.rows.values()
                                  if r["can_open"]), default=0.0)
        self.budget = max(self.open_cost, self.max_new_price)
        self.cash = self.budget - self.open_cost
        self._held_missing = [key for key in self.positions
                              if not self.rows[key].get('hold_value_available',self.rows[key]['value_available'])]
        self.baseline = None if self._held_missing else math.fsum(
            pos.quantity * self.rows[key]["hold_value_per_share"]
            for key, pos in self.positions.items())

    def evaluate(self, changes: Mapping[str, float]) -> dict:
        """Evaluate any joint allocation in O(held positions + changed positions)."""
        quantities = {key: p.quantity for key, p in self.positions.items()}
        cash = self.cash
        realized = 0.0
        acquired = {}
        legs = []
        reasons = []
        for key, delta in changes.items():
            if key not in self.rows:
                raise ValueError(f"Unknown action key: {key}")
            if not math.isfinite(delta):
                raise ValueError("Action sizes must be finite")
            if delta == 0:
                continue
            row = self.rows[key]
            old = quantities.get(key, 0.0)
            new = old + delta
            if new < -1e-10 or (delta < 0 and key not in self.positions):
                reasons.append(f"reduction_exceeds_position:{key}")
                continue
            if delta > 0:
                if not row["can_open"]:
                    reasons.append(f"entry_unavailable:{key}")
                    continue
                cash -= delta * row["capital_per_share"]
                acquired[key] = delta
                label = "add" if old else "enter"
            else:
                if not row["can_close"]:
                    reasons.append(f"exit_unavailable:{key}")
                    continue
                pos = self.positions[key]
                sign = 1 if pos.side == "long" else -1
                pnl = sign * (row["close_price"] - pos.entry_price)
                release = row["close_price"] if sign == 1 else pos.capital_per_share + pnl
                cash += -delta * release
                realized += -delta * pnl
                label = "exit" if new <= 1e-10 else "reduce"
            quantities[key] = max(0.0, new)
            legs.append(dict(key=key, action=label, change_shares=delta, resulting_shares=max(0.0, new)))
        active = [key.split(":")[0] for key, q in quantities.items() if q > 1e-10]
        for key,q in quantities.items():
            if q > 1e-10 and self.rows[key].get('session_terminal',False):
                reasons.append(f'terminal_requires_liquidation:{key}')
        if len(set(active)) != len(active):
            reasons.append("simultaneous_long_and_short")
        forced_flat = (bool(self.positions) and not acquired
            and all(self.rows[key].get('session_terminal',False) for key in self.positions)
            and not active)
        deficit = cash < -1e-8 * max(1.0, self.budget)
        if deficit and not forced_flat:
            reasons.append("insufficient_cash")
        feasible = not reasons
        missing = [key for key, q in quantities.items()
                   if (q-acquired.get(key,0.) > 1e-10 and not self.rows[key].get('hold_value_available',self.rows[key]['value_available']))
                   or (acquired.get(key,0.) > 0 and not self.rows[key].get('open_value_available',self.rows[key]['value_available']))]
        total = raw = None
        if feasible and not missing:
            total = raw = 0.0
            for key, q in quantities.items():
                if q <= 1e-10:
                    continue
                row = self.rows[key]
                added = acquired.get(key, 0.0)
                retained = q - added
                if retained > 1e-10:
                    total += retained * row['hold_value_per_share']
                    raw += retained * row['hold_profit_per_share']
                if added > 0:
                    total += added * row['open_value_per_share']
                    raw += added * row['open_profit_per_share']
        return dict(feasible=feasible, reasons=reasons, unavailable_values=missing,
                    value_status="infeasible" if not feasible else "unavailable" if missing else "available",
                    legs=legs, resulting_shares=quantities, cash_after=cash,
                    settlement_status='infeasible' if not feasible else 'insolvent' if deficit else 'solvent',
                    cash_deficit=max(0.,-cash),
                    undiscounted_future_profit=raw, discounted_future_value=total,
                    delta_vs_hold=None if total is None or self.baseline is None else total-self.baseline,
                    realized_pnl_now=realized)

    def describe(self) -> dict:
        return dict(time_us=self.time_us, open_position_cost=self.open_cost,
                    max_new_price=self.max_new_price, comparison_budget=self.budget,
                    available_cash=self.cash, baseline_hold_value=self.baseline,
                    unavailable_held_values=self._held_missing,
                    representation="Exact fractional changes; no enumeration or lookahead reallocations",
                    value_units="Discounted future profit above liquidation now; accrued P&L is separate",
                    mode=self.mode,
                    short_policy="synthetic_100_percent_reserve" if "short" in MODES[self.mode] else "disabled")
