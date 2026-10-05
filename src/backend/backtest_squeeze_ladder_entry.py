"""Prepared structural breakout proposals; Portfolio/OMS remains authority."""
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from src.backend.backtest_squeeze_ladder_loader import PreparedLadderObservations
from src.backend.backtest_squeeze_ladder_setup import BoundLadderSetup
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.execution_policies import ProtectionProfile
from src.trading_runtime.squeeze_ladder_protection import structural_ladder, ladder_profile


@dataclass(frozen=True, slots=True)
class LadderBreakoutDecision:
    reason: str
    boundary_ms: int
    setup: BoundLadderSetup
    previous_close_int: int | None = None
    close_int: int | None = None
    entry_limit_int: int | None = None
    target_level_ids: tuple[str, ...] = ()
    protection: ProtectionProfile | None = None


def propose_ladder_breakout(observations: PreparedLadderObservations, setup: BoundLadderSetup, *,
                            v7: CertifiedV7IntervalPlan, boundary_ms: int, tick_int: int,
                            break_buffer_ticks: int, target_count: int,
                            allocation: str) -> LadderBreakoutDecision:
    """Read only the causal prefix; never replace invalidated frozen geometry.

    This is not account admission. The coordinator must persist the decision,
    enforce the acknowledged-session lock and current financial permissions,
    and route its single aggregate capital request through Portfolio and OMS.
    """
    if (not isinstance(observations, PreparedLadderObservations) or not isinstance(setup, BoundLadderSetup)
            or not isinstance(v7, CertifiedV7IntervalPlan) or setup.ticker != observations.ticker
            or setup.market_plan_token != observations.market_plan_token
            or setup.scan_content_hash != observations.scan_content_hash
            or setup.qualification_mode != observations.gate.qualification_mode
            or setup.v7_plan_token != v7.token or v7.source_build_id != observations.source_build_id
            or type(boundary_ms) is not int or boundary_ms % 100
            or not setup.qualification_boundary_ms < boundary_ms <= 57_600_000
            or type(tick_int) is not int or tick_int <= 0
            or type(break_buffer_ticks) is not int or break_buffer_ticks < 0
            or type(target_count) is not int or target_count not in (2, 3, 5)
            or allocation not in {'equal', 'decreasing', 'increasing'}):
        raise ValueError("Ladder breakout source identity or policy differs")
    def reject(reason):
        return LadderBreakoutDecision(reason, boundary_ms, setup)
    if setup.reason != 'setup_qualified' or setup.resistance is None or setup.stop is None:
        return reject('setup_not_qualified')
    gate, source = observations.gate, observations.completed_source
    end = int(np.searchsorted(gate.boundary_ms, boundary_ms))
    start = setup.source_row_index
    if (end >= len(gate.boundary_ms) or int(gate.boundary_ms[end]) != boundary_ms
            or not 0 <= start < end or int(gate.boundary_ms[start]) != setup.qualification_boundary_ms):
        return reject('completed_observation_unavailable')
    # Reject expired/new admissions before reading a setup prefix. The compiled
    # policy bounds an eligible admission to at most 300 seconds (3,001 rows).
    if gate.admission_boundary_ms[end] != setup.admission_boundary_ms:
        return reject('setup_admission_changed')
    if gate.market_rejection[end] != 0:
        return reject('current_market_gate_failed')
    clocks = gate.boundary_ms[start:end + 1]
    certified_sparse = (gate.certified_history_through_ms is not None
                        and gate.certified_history_through_ms >= boundary_ms)
    if np.any(np.diff(clocks) != 100) and not certified_sparse:
        return reject('setup_observation_continuity_lost')
    if clocks[-1] - clocks[-2] != 100:
        return reject('completed_price_crossing_unavailable')
    ticker_index = v7._tickers.index(setup.ticker)
    seconds = v7.valid_seconds[ticker_index][1]
    first = bisect_right(seconds, setup.qualification_boundary_ms) - 1
    last = bisect_right(seconds, boundary_ms) - 1
    if (first < 0 or last < first or boundary_ms - seconds[last] > 1000
            or any(right - left != 1000 for left, right in zip(seconds[first:last], seconds[first + 1:last + 1]))):
        return reject('setup_v7_continuity_lost')
    # Confirmation clocks describe producer role/fit segments, not identity
    # creation. Keep the entry threshold frozen while proving a forward path
    # on that same identity; a refit never moves the entry threshold.
    path = sorted((row for row in v7.intervals[ticker_index][1]
              if row.level_id == setup.resistance.level_id
              and row.valid_to_ms > seconds[first] and row.valid_from_ms <= seconds[last]),
              key=lambda row: row.valid_from_ms)
    if (not path or path[0].valid_from_ms > seconds[first]
            or path[-1].valid_to_ms <= seconds[last]
            or path[0].lower != setup.resistance.lower or path[0].upper != setup.resistance.upper
            or path[0].role != 'resistance'
            or path[0].confirmed_at_ms != setup.resistance.confirmed_at_ms):
        return reject('frozen_resistance_invalidated')
    origin_ms = int(market_day_boundary(v7.session_date, 0).timestamp()) * 1000
    previous_role, previous_confirmation = 0, setup.resistance.confirmed_at_ms
    for ordinal, row in enumerate(path):
        role = {'resistance':0, 'transition':1, 'support':2}.get(row.role, -1)
        if (role < previous_role or role < 0
                or (row.role == 'transition' and row.transition_from != 'resistance')
                or row.confirmed_at_ms < previous_confirmation
                or row.confirmed_at_ms > origin_ms + max(seconds[first], row.valid_from_ms)
                or (ordinal and path[ordinal-1].valid_to_ms != row.valid_from_ms)):
            return reject('frozen_resistance_invalidated')
        previous_role, previous_confirmation = role, row.confirmed_at_ms
    completed = source.slice(start, end - start + 1)
    prices = completed['close_int'].to_numpy()
    valid = completed['price_valid'].to_numpy()
    vwaps = completed['execution_vwap'].to_numpy()
    if (np.any(~np.isfinite(vwaps)) or np.any(vwaps <= 0)
            or (np.any(valid != 1) and not certified_sparse)
            or valid[0] != 1 or np.any(valid[-2:] != 1)):
        return reject('setup_price_evidence_lost')
    # Quote-only buckets contain no candle. Compare each persisted VWAP with
    # the last observed valid completed close for setup retention only; never
    # manufacture that close as a crossing operand or execution bar.
    observed_price_indices = np.maximum.accumulate(np.where(valid == 1, np.arange(len(valid)), 0))
    if np.any(prices[observed_price_indices] <= vwaps * 10000):
        return reject('qualified_vwap_lost')
    previous, current = int(prices[-2]), int(prices[-1])
    reference = setup.resistance.upper_comparison_int
    if np.any(prices[:-1][valid[:-1] == 1] > reference + tick_int * break_buffer_ticks):
        return reject('earlier_frozen_break_observed')
    if not previous <= reference < current - tick_int * break_buffer_ticks:
        return reject('frozen_resistance_not_broken')
    levels = v7.levels(setup.ticker, boundary_ms=boundary_ms)
    if sum(row['unified_level_id'] == setup.resistance.level_id for row in levels) != 1:
        return reject('frozen_resistance_invalidated')
    entry = int(source['ask_int'][end].as_py())
    if entry % tick_int or setup.stop.stop_int >= entry:
        return reject('entry_or_frozen_stop_geometry_invalid')
    tick, entry_price = Decimal(tick_int) / 10000, Decimal(entry) / 10000
    overhead = sorted((row for row in levels if row['role'] == 'resistance'
                       and row['unified_level_id'] != setup.resistance.level_id
                       and Decimal(str(row['lower'])) > entry_price + tick),
                      key=lambda row: (row['lower'], row['upper'], row['unified_level_id']))
    if len(overhead) < target_count:
        return reject('complete_structural_targets_unavailable')
    selected = overhead[:target_count]
    identities = tuple(row['unified_level_id'] for row in selected)
    try:
        targets = structural_ladder(entry_price, identities,
            tuple(Decimal(str(row['lower'])) for row in selected), tick)
    except ValueError:
        return reject('structural_target_ticks_invalid')
    profile = ladder_profile(entry_price, Decimal(setup.stop.stop_int) / 10000, targets, allocation)
    return LadderBreakoutDecision('entry_proposed', boundary_ms, setup, previous, current,
                                 entry, identities, profile)
