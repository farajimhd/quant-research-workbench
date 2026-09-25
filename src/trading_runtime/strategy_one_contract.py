"""Draft decision rules for the first user-facing Strategy number.

This module is deliberately not registered as an executable Strategy. The
required QMD major-swing and forming-MACD products and the disk-free fixed
Backtest path are not yet available. Registering it now would advertise a
strategy that cannot obey its declared input and execution contracts.

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
PUBLICATION_STATUS = "draft_missing_persisted_inputs_and_fixed_runtime"
EVALUATION_INTERVAL = "100ms"
REQUIRED_INPUTS = (
    "arte.bars_v1@100ms",
    "arte.bars_v1@1s",
    "arte.indicators_v1@100ms",
    "arte.liquidity_100ms_v1@100ms",
    "qmd.forming_macd@1s/evaluate_100ms",
    "qmd.forming_macd@5s/evaluate_100ms",
    "qmd.forming_macd@10s/evaluate_100ms",
    "qmd.forming_macd@30s/evaluate_100ms",
    "qmd.execution_vwap@100ms",
    "qmd.early_squeeze_occurrence@100ms",
    "qmd.prior_regular_close@session",
    "qmd.confirmed_local_swing_low@1s",
    "qmd.confirmed_major_swing_low@1s",
    "v7.causal_levels@1s",
)


def _confirmed_lows(rows: Sequence[Mapping], now: float, scale: str) -> list[Mapping]:
    found = []
    for row in rows:
        if row.get("scale") != scale or row.get("side") not in (1, "support"):
            continue
        if row.get("state", "active") != "active":
            continue
        value, pivot, confirmed = (row.get(key) for key in
                                    ("price", "pivot_at", "confirmed_at"))
        if (not all(type(item) in (int, float) and isfinite(item)
                    for item in (value, pivot, confirmed))
                or not 0 < value or not 0 < pivot < confirmed <= now):
            continue
        found.append(row)
    return found


def outside_swing_stop(
    *, local_swings: Sequence[Mapping], major_swings: Sequence[Mapping],
    now: float, tick: float,
) -> dict | None:
    """Select the first confirmed major low below the latest local low.

    An empty major-swings sequence means the QMD product certifies no outside
    low; absence of the product itself must fail dependency preflight. Neither
    unconfirmed nor future-confirmed pivots may influence this selection.
    """
    if not isfinite(tick) or tick <= 0:
        raise ValueError("Strategy 1 needs a positive tick")
    local = _confirmed_lows(local_swings, now, "local")
    if not local:
        return None
    inner = max(local, key=lambda row: (row["pivot_at"], row["confirmed_at"],
                                        row["price"]))
    outside = [row for row in _confirmed_lows(major_swings, now, "major")
               if row["price"] < inner["price"]]
    selected = (max(outside, key=lambda row: (row["price"], row["pivot_at"],
                                             row["confirmed_at"])) if outside else inner)
    reason = "first_outside_swing_low" if outside else "local_swing_low"
    return {"price": below(selected["price"], tick),
            "source": reason, "reason": reason,
            "local": dict(inner), "selected": dict(selected)}


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
