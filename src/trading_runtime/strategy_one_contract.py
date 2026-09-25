"""Draft decision rules for the first user-facing Strategy number.

This module is deliberately not registered as an executable Strategy. The
disk-free fixed Backtest path and its integration tests are not yet complete.
Registering it now would advertise a strategy that cannot obey its declared
input and execution contracts.

STRATEGY CREATION RULES (also enforced at publication/preflight boundaries):
* A trading-behavior change creates the next Strategy number. Never alter the
  rules, defaults, or precedence of an already published number. Old numbers
  remain executable only for exact historical reproduction.
* A Strategy declares every QMD/ARTE input, resolution, freshness bound, and
  completed-boundary clock. It must not create bars, indicators, or market
  structure, or substitute an unrequested timeframe when an input is absent.
* Compile reusable stateless rule sets over typed columnar batches. Only
  surviving candidates enter causal, per-position state machines. A portfolio
  coordinator alone may mutate shared cash, orders, and liquidity budgets.
* Pin the strategy number and content digest, rule-set/data contracts, engine
  and broker contracts, and evaluation interval in each run and decision.
  A digest is an integrity seal, not a claim of external cryptographic signing.
* New numbers require causal, live/backtest-parity, order/fill, missing-input,
  worker-count, and checkpoint/recovery tests before publication.

This module contains pure selection rules. It does not query or persist data.
"""
from __future__ import annotations

from math import isfinite
from typing import Mapping, Sequence

from . import early_squeeze_breakout as breakout
from . import early_squeeze_price as price_rules
from .early_squeeze_fast import below


STRATEGY_NUMBER = 1
STRATEGY_ID = "early-squeeze-strategy"
PUBLICATION_STATUS = "draft_fixed_runtime_not_integrated"
EVALUATION_INTERVAL = "100ms"
REQUIRED_INPUTS = (
    "arte.bars_v1@100ms",
    "arte.bars_v1@1s",
    "arte.bars_v1@30s",
    "arte.indicators_v1@100ms",
    "arte.indicators_v1@1s",
    "arte.indicators_v1@5s",
    "arte.indicators_v1@10s",
    "arte.indicators_v1@30s",
    "arte.liquidity_100ms_v1@100ms",
    "arte.liquidity_100ms_v1.execution_vwap@100ms",
    "arte.indicators_v1.previous_close@session",
    "arte.structural_levels_v7@as_of_1s",
)


def closed_macd_candidate_mask(lines, signals, sample_boundaries_ms,
                               evaluation_boundaries_ms):
    """Vectorized 1s/5s/10s/30s completed-MACD gate.

    The loader joins pinned indicator rows as-of each completed 100ms boundary
    within one session. Missing, stale, future, or nonfinite values reject the
    candidate. This gate never computes an EMA or a forming MACD in Strategy.
    """
    import numpy as np

    line = np.asarray(lines, dtype=np.float64)
    signal = np.asarray(signals, dtype=np.float64)
    source = np.asarray(sample_boundaries_ms, dtype=np.int64)
    boundary = np.asarray(evaluation_boundaries_ms, dtype=np.int64)
    if (line.ndim != 2 or line.shape[1] != 4 or signal.shape != line.shape
            or source.shape != line.shape or boundary.shape != (line.shape[0],)):
        raise ValueError("Strategy 1 completed MACD needs aligned N x 4 arrays")
    age = boundary[:, None] - source
    resolution = np.array([1_000, 5_000, 10_000, 30_000], dtype=np.int64)
    return np.all(np.isfinite(line) & np.isfinite(signal)
                  & (line > signal) & (source >= 0)
                  & (age >= 0) & (age < resolution), axis=1)


def completed_30s_low_stop(*, low_int: int, boundary_ms: int,
                           now_ms: int, tick: float,
                           price_valid: bool, extremes_valid: bool) -> dict | None:
    """Use only the immediately preceding completed, price-bearing 30s bar.

    The fixed market loader owns row certification and the 1/10000 price unit.
    No prior bar is carried across an empty bucket. No forming 30s low may
    enter a 100ms decision. An unavailable bar defers entry/ratcheting.
    """
    if (type(now_ms) is not int or type(boundary_ms) is not int
            or type(low_int) is not int or not isinstance(tick, (int, float))
            or not isfinite(tick) or tick <= 0):
        raise ValueError("Strategy 1 needs typed completed-bar inputs")
    if boundary_ms <= 0 or boundary_ms % 30_000:
        raise ValueError("Strategy 1 stop needs a completed 30s boundary")
    if boundary_ms > now_ms or now_ms - boundary_ms >= 30_000:
        return None
    if not price_valid or not extremes_valid or low_int <= 0:
        return None
    low = low_int / 10_000
    return {"price": below(low, tick), "source": "completed_30s_bar_low",
            "reason": "completed_30s_bar_low", "low_int": low_int,
            "boundary_ms": boundary_ms}


def ordinal_target(*, rows: Sequence[Mapping], ask: float, tick: float,
                   broken_count: int, previous_target: float | None = None) -> dict | None:
    """Reuse the prior 3/2/1 overhead-resistance target rule exactly.

    Position-owned distinct resistance breaks select third overhead at 0-3,
    second at 4-5, and first at 6+. A target amendment may only move upward.
    """
    if type(broken_count) is not int or broken_count < 0:
        raise ValueError("Strategy 1 needs a nonnegative distinct break count")
    ordinal = price_rules.target_ordinal(broken_count)
    overhead = sorted((row for row in rows if price_rules.is_resistance(row)
        and breakout.target_price(row, tick, breakout.CONTRACT) > ask),
        key=lambda row: (price_rules.midpoint(row), row["unified_level_id"]))
    if len(overhead) < ordinal:
        return None
    selected = overhead[ordinal - 1]
    candidate = breakout.target_price(selected, tick, breakout.CONTRACT)
    if previous_target is not None and candidate <= previous_target:
        return None
    return {"price": candidate, "ordinal": ordinal, "level": dict(selected),
            "broken_count": broken_count}


def resistance_group_stop(*, accepted_levels: Sequence[Mapping],
                          applied_groups: int, tick: float) -> dict | None:
    """Each disjoint group of three breaks earns its lowest resistance floor."""
    if type(applied_groups) is not int or applied_groups < 0:
        raise ValueError("Invalid applied resistance group count")
    earned = len(accepted_levels) // 3
    if earned <= applied_groups:
        return None
    group = accepted_levels[(earned - 1) * 3:earned * 3]
    if len(group) != 3 or any(not price_rules.eligible(row) for row in group):
        raise ValueError("Resistance stop requires three qualified breaks")
    selected = min(group, key=lambda row: (row["lower"], row["unified_level_id"]))
    return {"price": below(selected["lower"], tick),
            "source": "three_resistance_step_stop", "groups": earned,
            "level": dict(selected),
            "group_level_ids": [row["unified_level_id"] for row in group]}


def upward_stop_update(*, current: float, executable_bid: float,
                       swing: Mapping | None, resistance: Mapping | None) -> dict | None:
    """Only raise protection; simultaneous qualifying paths favor resistance."""
    for proposal in (resistance, swing):
        if proposal is None:
            continue
        value = proposal["price"]
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError("Invalid stop proposal")
        if current < value < executable_bid:
            return dict(proposal)
    return None
