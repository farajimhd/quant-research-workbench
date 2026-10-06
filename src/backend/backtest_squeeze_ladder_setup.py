"""Bind prepared VWAP survivors to certified V7/pivot setup evidence.

This is source binding, not financial approval or durable decision publication.
Every survivor returns a typed result, including missing geometry rejections.
"""
from dataclasses import dataclass
import numpy as np
from src.trading_runtime.squeeze_ladder_columnar import LadderGatePolicy
from src.trading_runtime.squeeze_ladder_geometry import LadderGeometryBindingPolicy, LadderGeometryBindingWitness

from src.backend.backtest_market_data import CertifiedMarketDayPlan, market_day_boundary
from src.backend.backtest_squeeze_ladder_loader import PreparedLadderObservations
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan
from src.trading_runtime.squeeze_ladder_setup import (
    FrozenLadderResistance, FrozenLadderStop, freeze_ladder_resistance, freeze_ladder_stop,
)


@dataclass(frozen=True, slots=True)
class BoundLadderSetup:
    ticker: str
    source_row_index: int
    admission_boundary_ms: int
    qualification_boundary_ms: int
    market_plan_token: str
    scan_content_hash: str
    v7_plan_token: str
    pivot_plan_token: str
    reason: str
    resistance: FrozenLadderResistance | None
    stop: FrozenLadderStop | None
    qualification_mode: str = 'vwap_cross'
    geometry_binding: LadderGeometryBindingWitness | None = None


def bind_ladder_setups(observations: PreparedLadderObservations, *,
                       market: CertifiedMarketDayPlan, v7: CertifiedV7IntervalPlan,
                       pivots: CertifiedPivotPlan, tick_int: int,
                       stop_buffer_ticks: int,
                       geometry_policy: LadderGeometryBindingPolicy | None = None,
                       gate_policy: LadderGatePolicy | None = None) -> tuple[BoundLadderSetup, ...]:
    """Freeze only qualified survivors, from exact matching native attempts.

    The input plans must have passed their producer/preflight certificates,
    including prior-checkpoint lineage and completed RTH warmup for AH. Never
    build missing V7 or pivots here. A fresh timeline starts from its certified
    origin; no future pivot enters an earlier qualification.
    A separately declared waiting policy retains the first trigger per admission
    and freezes the earliest eligible complete pair, atomically. It never extends
    the original TTL, carries partial geometry, or acquires at the freeze clock.
    """
    if (not isinstance(observations, PreparedLadderObservations)
            or not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(v7, CertifiedV7IntervalPlan) or not isinstance(pivots, CertifiedPivotPlan)
            or len(market.sessions) != 1 or observations.market_plan_token != market.token
            or observations.source_build_id != market.build_id
            or v7.source_build_id != market.build_id or pivots.source_build_id != market.build_id
            or v7.session_date != market.sessions[0] or pivots.session_date != market.sessions[0]
            or type(tick_int) is not int or tick_int <= 0
            or type(stop_buffer_ticks) is not int or stop_buffer_ticks < 0):
        raise ValueError("Ladder setup source identity or geometry policy differs")
    ticker = observations.ticker
    bars = [unit.attempt_id for unit in market.units if unit.ticker == ticker and unit.stage == 'bars']
    v7_units = [unit for unit in v7.coverage if unit.ticker == ticker]
    pivot_units = [unit for unit in pivots.coverage if unit.ticker == ticker]
    if (len(bars) != 1 or len(v7_units) != 1 or len(pivot_units) != 1
            or v7_units[0].bars_attempt_id != bars[0] or pivot_units[0].bars_attempt_id != bars[0]):
        raise ValueError("Ladder setup dependencies do not pin the same bar attempt")
    table, gate = observations.completed_source, observations.gate
    if table.num_rows != len(gate.boundary_ms):
        raise ValueError("Ladder source rows differ from prepared gate")
    timeline = pivots.timeline(ticker)
    origin_ms = int(market_day_boundary(market.sessions[0], 0).timestamp()) * 1000
    result = []
    if (gate.qualification_mode not in {'vwap_cross', 'first_eligible_above_vwap'}
            or (gate.qualification_mode != 'vwap_cross' and gate.qualification_indices is None)):
        raise ValueError('Ladder setup qualification mode is unavailable')
    qualifications = (gate.qualification_indices if gate.qualification_indices is not None
                      else gate.vwap_cross_indices)
    waiting = geometry_policy is not None
    if waiting and (type(geometry_policy) is not LadderGeometryBindingPolicy
                    or type(gate_policy) is not LadderGatePolicy
                    or gate_policy.qualification_mode != gate.qualification_mode
                    or gate.certified_history_through_ms is None):
        raise ValueError('Waiting geometry requires declared policy and certified source prefix')
    if waiting:
        # One original trigger per admission, including crossing qualification.
        # Selection stays columnar; rich geometry is read only for market survivors.
        admissions = gate.admission_boundary_ms[qualifications]
        qualifications = qualifications[np.r_[True, np.diff(admissions) != 0]] if len(admissions) else qualifications
        close = table['close_int'].to_numpy()
        vwap_values = table['execution_vwap'].to_numpy()
        eligible = np.flatnonzero((gate.market_rejection == 0)
            & (close > vwap_values * 10000 + gate_policy.vwap_buffer_int))
    for raw_index in qualifications:
        index = int(raw_index)
        boundary = int(gate.boundary_ms[index])
        if (index < 0 or index >= table.num_rows or gate.market_rejection[index] != 0
                or table['boundary_ms'][index].as_py() != boundary
                or table['ticker'][index].as_py() != ticker):
            raise ValueError("Ladder qualification differs from source key")
        trigger_index, trigger_boundary = index, boundary
        admission = int(gate.admission_boundary_ms[index])
        expiry = admission + gate_policy.admission_ttl_ms if waiting else None
        if waiting:
            left = int(np.searchsorted(eligible, index))
            end_row = int(np.searchsorted(gate.boundary_ms, expiry))
            right = int(np.searchsorted(eligible, end_row))
            candidates = eligible[left:right]
            candidates = candidates[gate.admission_boundary_ms[candidates] == admission]
        else:
            candidates = (index,)
        resistance = stop = None
        for candidate in candidates:
            index = int(candidate)
            boundary = int(gate.boundary_ms[index])
            active = timeline.at(boundary)
            resistance = freeze_ladder_resistance(active_levels=v7.levels(ticker, boundary_ms=boundary),
                qualification_boundary_ms=boundary, qualification_epoch_ms=origin_ms + boundary,
                execution_vwap=float(table['execution_vwap'][index].as_py()))
            stop = freeze_ladder_stop(active_pivots=active, qualification_boundary_ms=boundary,
                entry_limit_int=int(table['ask_int'][index].as_py()), tick_int=tick_int,
                buffer_ticks=stop_buffer_ticks)
            if resistance is not None and stop is not None:
                break
        reason = ('v7_resistance_unavailable' if resistance is None else
                  'confirmed_swing_stop_unavailable' if stop is None else 'setup_qualified')
        witness = (LadderGeometryBindingWitness(geometry_policy.version, trigger_index,
            trigger_boundary, boundary, expiry) if waiting and reason == 'setup_qualified' else None)
        result.append(BoundLadderSetup(ticker, index, admission, boundary,
            market.token, observations.scan_content_hash, v7.token, pivots.token, reason, resistance, stop,
            gate.qualification_mode, witness))
    return tuple(result)
