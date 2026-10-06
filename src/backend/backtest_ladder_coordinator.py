"""Bounded columnar ladder preparation and sequential native financial routing.

Preparation observes certified source prefixes; it never changes cash or fills.
Only compact source decisions survive each bounded batch. Execution consumes
all completed broker rows before ranked proposals at that same global clock.
"""
from collections import Counter, defaultdict
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from math import isfinite

import numpy as np

from src.backend.backtest_squeeze_ladder_entry import propose_ladder_breakout
from src.backend.backtest_squeeze_ladder_setup import bind_ladder_setups


@dataclass(frozen=True, slots=True)
class PreparedLadderProposal:
    decision: object
    eligible_notional: float

    @property
    def ticker(self):
        return self.decision.setup.ticker

    @property
    def boundary_ms(self):
        return self.decision.boundary_ms


@dataclass(frozen=True, slots=True)
class PreparedLadderCampaign:
    proposals: tuple[PreparedLadderProposal, ...]
    counts: tuple[tuple[str, int], ...]
    boundaries: tuple[int, ...] = field(init=False)

    def __post_init__(self):
        boundaries = tuple(row.boundary_ms for row in self.proposals)
        if boundaries != tuple(sorted(boundaries)):
            raise ValueError('Ladder compact proposals moved backward')
        object.__setattr__(self, 'boundaries', boundaries)

    def at(self, boundary_ms):
        return self.proposals[bisect_left(self.boundaries, boundary_ms):
            bisect_right(self.boundaries, boundary_ms)]


def completed_cross_indices(gate, source, setup, *, tick_int, break_buffer_ticks):
    """Vectorize price-cross operands; the structural proof still runs per survivor."""
    if setup.reason != 'setup_qualified':
        return np.array([], dtype=np.int64)
    close = source['close_int'].to_numpy()
    valid = source['price_valid'].to_numpy()
    start = setup.source_row_index
    if not 0 <= start < len(close) or len(close) != len(gate.boundary_ms):
        raise ValueError('Ladder compact crossing source differs from qualification')
    reference = setup.resistance.upper_comparison_int
    indices = np.flatnonzero(
        (valid[1:] == 1) & (valid[:-1] == 1)
        & (np.diff(gate.boundary_ms) == 100)
        & (close[:-1] <= reference)
        & (close[1:] > reference + tick_int * break_buffer_ticks)) + 1
    return indices[(indices > start)
        & (gate.market_rejection[indices] == 0)
        & (gate.admission_boundary_ms[indices] == setup.admission_boundary_ms)]


def prepare_ladder_campaign(authority, *, start_boundary_ms, maximum_proposals=250_000):
    """Retain compact decisions, never full-day Arrow tables for the universe."""
    if (type(start_boundary_ms) is not int or start_boundary_ms % 100
            or not 0 <= start_boundary_ms < authority.source_end
            or type(maximum_proposals) is not int or not 1 <= maximum_proposals <= 250_000):
        raise ValueError('Ladder campaign requires a bounded declared session')
    counts, proposals, keys = Counter(), [], set()
    from datetime import datetime
    from src.backend.backtest_market_data import market_day_boundary
    origin = market_day_boundary(authority.session_date, 0)
    admitted = set()
    for occurrence in authority.certified_scan['occurrences']:
        delta = datetime.fromisoformat(occurrence['available_at']) - origin
        boundary = delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
        counts['certified_scan_admissions'] += 1
        if not start_boundary_ms < boundary <= authority.source_end:
            counts['admission_outside_requested_session'] += 1
            continue
        if occurrence['ticker'] not in authority.tickers:
            raise ValueError('Certified ladder admission escaped fenced ticker membership')
        admitted.add(occurrence['ticker'])
    counts['no_session_admission_tickers'] = len(authority.tickers - admitted)
    symbols = sorted(admitted)
    for offset in range(0, len(symbols), 8):
        contexts = authority.contexts_for(tuple(symbols[offset:offset + 8]))
        for context in contexts:
            observations = context.observations
            counts['observed_tickers'] += 1
            counts['source_rows'] += len(observations.gate.boundary_ms)
            counts['market_survivors'] += int(np.count_nonzero(observations.gate.market_rejection == 0))
            setups = bind_ladder_setups(observations, market=context.market,
                v7=context.v7, pivots=context.pivots, tick_int=context.tick_int,
                stop_buffer_ticks=context.stop_buffer_ticks,
                geometry_policy=context.geometry_policy, gate_policy=context.gate_policy)
            for setup in setups:
                counts[setup.reason] += 1
                if setup.reason != 'setup_qualified':
                    continue
                indices = completed_cross_indices(observations.gate,
                    observations.completed_source, setup, tick_int=context.tick_int,
                    break_buffer_ticks=context.break_buffer_ticks)
                counts['price_cross_survivors'] += len(indices)
                for index in indices:
                    boundary = int(observations.gate.boundary_ms[index])
                    if boundary <= start_boundary_ms:
                        counts['outside_requested_session'] += 1
                        continue
                    decision = propose_ladder_breakout(observations, setup,
                        v7=context.v7, boundary_ms=boundary, tick_int=context.tick_int,
                        break_buffer_ticks=context.break_buffer_ticks, target_count=3,
                        allocation='equal')
                    counts[decision.reason] += 1
                    if decision.reason != 'entry_proposed':
                        continue
                    key = (boundary, setup.ticker)
                    if key in keys:
                        # One ticker at a clock uses the earliest qualified
                        # frozen setup, preserving source qualification order.
                        counts['later_setup_at_same_clock'] += 1
                        continue
                    score = float(observations.completed_source['cumulative_notional'][int(index)].as_py())
                    if not isfinite(score) or score < 0:
                        raise ValueError('Ladder rank lacks completed eligible notional')
                    if len(proposals) >= maximum_proposals:
                        raise RuntimeError('Ladder compact proposal budget exhausted; no truncation allowed')
                    keys.add(key)
                    proposals.append(PreparedLadderProposal(decision, score))
        del contexts
    proposals.sort(key=lambda row: (row.boundary_ms, -row.eligible_notional, row.ticker))
    return PreparedLadderCampaign(tuple(proposals), tuple(sorted(counts.items())))


async def submit_ladder_boundary(runtime, campaign, *, boundary_ms, assignments,
                                 source_authority, before_acquisition):
    """Portfolio/OMS chooses sequential admission after the caller's broker boundary."""
    from src.trading_runtime.squeeze_ladder_automatic import submit_automatic_ladder
    from src.backend.backtest_market_data import market_day_boundary
    if runtime.last_event_time != market_day_boundary(source_authority.session_date, boundary_ms):
        raise ValueError('Ladder acquisition preceded its completed global broker boundary')
    by_ticker = defaultdict(list)
    for assignment in assignments:
        by_ticker[assignment.ticker].append(assignment)
    results = []
    selected = campaign.at(boundary_ms)
    if any(not by_ticker.get(proposal.ticker) for proposal in selected):
        raise ValueError('Ladder proposal has no native assignment owner')
    selected = sorted(selected, key=lambda row: (-row.eligible_notional,
        min(owner.conid for owner in by_ticker[row.ticker]), row.ticker))
    for proposal in selected:
        context = source_authority.context_for(proposal.ticker)
        owners = by_ticker.get(proposal.ticker, ())
        if not owners:
            raise ValueError('Ladder proposal has no native assignment owner')
        for assignment in sorted(owners, key=lambda row: (row.account_id, row.assignment_id)):
            # Each prior accepted request may have changed cash, reservations
            # and the verified prefix. Never reuse a boundary-wide account image.
            await before_acquisition()
            outcomes = await submit_automatic_ladder(runtime, proposal.decision,
                assignment=assignment, market_context=context, policy=source_authority.policy)
            results.append((proposal.ticker, assignment.account_id, outcomes))
    return tuple(results)


async def run_ladder_session(*, runtime, assignments, source_authority, prices,
                             client_factory, before_boundary, before_entry,
                             finish_boundary, stage_time=None):
    """Run compact proposals plus only financially active certified source tapes.

    The before_entry callback queues the exact current native financial capture
    ahead of intents. It must not manufacture a Strategy1 manager checkpoint.
    Source readers run off the event loop; fills and OCA remain sequential.
    """
    import asyncio
    from dataclasses import replace
    from time import perf_counter
    from src.backend.backtest_strategy_one_scheduler import (
        StrategyOneBoundaryScheduler, persisted_active_market_source)
    from src.backend.backtest_market_data import market_day_boundary
    from src.backend.backtest_strategy_one_financial import read_strategy_one_financial_view
    from src.trading_runtime.numbered_fixed_strategy import resolve_numbered_fixed_strategy

    contract = resolve_numbered_fixed_strategy(runtime.config.strategy_id,
        runtime.config.strategy_revision)
    if (getattr(contract, 'automatic_entry_policy', None) != source_authority.policy
            or runtime.run_id != source_authority.run_id
            or not callable(before_entry)
            or runtime.broker.financially_active_tickers() != ()):
        raise ValueError('Ladder execution requires a fresh exact declared native owner')
    end = source_authority.source_end
    start = 0 if end == 19_800_000 else 43_200_000
    began = perf_counter()
    campaign = await asyncio.to_thread(prepare_ladder_campaign, source_authority,
        start_boundary_ms=start)
    if stage_time is not None:
        stage_time('ladder_columnar_preparation', began)
    source = persisted_active_market_source(source_authority.market,
        price_plan=prices, through_boundary_ms=end, client_factory=client_factory,
        stage_time=stage_time)
    scheduler = StrategyOneBoundaryScheduler(
        session_date=source_authority.session_date.isoformat(), candidate_rows=iter(()),
        active_source=source, start_after_boundary_ms=start)
    policy_clocks = tuple(clock for clock in contract.session_clocks if start < clock <= end)
    scheduler.install_session_clocks(tuple(sorted(set(campaign.boundaries + policy_clocks))))

    def entry_rows(work):
        rows = dict(work.broker_rows)
        for proposal in campaign.at(work.boundary_ms):
            if proposal.ticker in rows:
                continue
            tape = source(proposal.ticker, work.boundary_ms - 100)
            try:
                clock, resolutions = next(tape)
                if clock != work.boundary_ms or 100 not in resolutions:
                    raise ValueError('Ladder proposal lacks its exact certified broker bucket')
                rows[proposal.ticker] = resolutions
            finally:
                tape.close()
        return replace(work, broker_rows=tuple(sorted(rows.items())))

    boundary_count, admission_counts = 0, Counter()
    try:
        while True:
            work = (await asyncio.to_thread(scheduler.pop_next)
                if scheduler.pop_next_may_block() else scheduler.pop_next())
            if work is None:
                break
            if campaign.at(work.boundary_ms):
                work = await asyncio.to_thread(entry_rows, work)
            await before_boundary(work)
            rows = [resolutions[100] for _, resolutions in work.broker_rows if 100 in resolutions]
            if rows:
                await runtime.process_liquidity_boundary(rows,
                    at=market_day_boundary(source_authority.session_date, work.boundary_ms))
            await runtime.advance_numbered_session_clock(work.boundary_ms)
            if contract.liquidation_due(work.boundary_ms):
                by_ticker = dict(work.broker_rows)
                for assignment in sorted(assignments, key=lambda row: (row.ticker, row.account_id)):
                    if assignment.ticker not in by_ticker:
                        continue
                    financial = await read_strategy_one_financial_view(assignment,
                        runtime.broker, runtime.order_manager)
                    if financial.position_quantity > 0 and not financial.pending_entry and not financial.pending_exit:
                        # Exit source quantity must bind the account image
                        # after this bucket's fills, before the next intent.
                        await before_entry(work)
                    await runtime.submit_numbered_session_exit(financial,
                        by_ticker[assignment.ticker], work.boundary_ms)
            if campaign.at(work.boundary_ms):
                async def capture_entry():
                    await before_entry(work)
                outcomes = await submit_ladder_boundary(runtime, campaign, boundary_ms=work.boundary_ms,
                    assignments=assignments, source_authority=source_authority,
                    before_acquisition=capture_entry)
                for _, _, owner_outcomes in outcomes:
                    for outcome in owner_outcomes:
                        status = outcome.get('decision', {}).get('status')
                        if not isinstance(status, str) or not status:
                            raise ValueError('Ladder native admission returned no explicit status')
                        admission_counts[status] += 1
            await finish_boundary(work)
            boundary_count += 1
            active = runtime.broker.financially_active_tickers()
            await asyncio.to_thread(scheduler.reconcile_financial_tickers, active)
            if scheduler.exhausted_tickers:
                raise RuntimeError('Ladder certified tape ended with active financial state: '
                    + repr(scheduler.exhausted_tickers))
            if work.boundary_ms == end and active:
                raise RuntimeError('Ladder session ended with residual exposure/orders: ' + repr(active))
    finally:
        scheduler.close()
    return dict(preparation_counts=dict(campaign.counts), proposals=len(campaign.proposals),
        admission_counts=dict(sorted(admission_counts.items())), processed_boundaries=boundary_count)
