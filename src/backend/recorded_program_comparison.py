"""Compose recorded app measures through existing verified read contracts.

No writer, feature producer, broker execution, strategy registration or local
journal participates. The prepared QMD blocks and typed Signal Stream must
already have been obtained through the app's certified preparation path.
"""
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from types import MappingProxyType
from threading import Lock
from datetime import datetime, timedelta
from collections.abc import Mapping

import polars as pl

from .qmd_columnar_history_transport import _owned_worker
from .backtest_program_journal_startup import _settle_owned_task
from .backtest_program_inputs import PreparedProgramBlock
from .signal_stream_runtime_service import SignalStreamRuntime
from .program_policy_recovery import RecoveredProgramPolicy, clone_recovered_program_actor, program_policy_fingerprint
from .typed_backtest_financial_review import load_typed_backtest_financial_evidence
from .rule_decision_recovery import read_rule_decision_observations_async
from .filled_session_measures import project_filled_session_measures
from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
from .signal_stream_typed_readback import _normalize
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_prepared_source
from src.trading_runtime.optimized_strategy_reference import OptimizationReference, ReferenceClockWindow, compare_sessions
from src.trading_runtime.journal_contract import canonical_json

_COMPARISON_PROOF = object()


def _comparison_hash(value):
    def plain(item):
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [plain(child) for child in item]
        return item
    return sha256(canonical_json(plain(value)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RecordedProgramSessionMetrics:
    day: str
    app: object
    _hash: str = field(repr=False)
    _proof: object = field(repr=False)


def _clock(value):
    return datetime.fromisoformat(_normalize(value, "DateTime64(6, 'UTC')", occurrence=False))


def verify_frozen_program_release(reference, release):
    """Recompile the sealed winner through the same shared specification adapter."""
    candidate = getattr(reference, 'candidate_envelope', None)
    if candidate is not None:
        from .optimized_strategy_import import verify_imported_release
        if ((reference.winner_sha256, reference.generation, reference.candidate)
                != (candidate.program_hash, candidate.generation, candidate.candidate)
                or dict(reference.program) != dict(candidate.program)
                or dict(reference.execution) != dict(candidate.execution)):
            raise ValueError('Recorded candidate reference identity differs from its envelope')
        verify_imported_release(candidate, release)
        return
    from src.trading_runtime.optimized_program_specification import compile_frozen_winner, STAGES
    from src.trading_runtime.columnar_strategy_release import POLICY
    rule = release.universe
    expected = compile_frozen_winner(reference, release.fields, universe_identity=dict(
        version='v6-volume-market-blocks-v1', window=rule.window_seconds, top_n=rule.top_count,
        minimum_close=rule.minimum_prior_close, maximum_close=rule.maximum_prior_close))
    if (release.programs != expected.programs
            or dict(release.minimum_age_seconds) != {stage: expected.minimum_age_seconds.get(stage, 0) for stage in STAGES}
            or any(release.header[name] != expected.policy[name] for name in POLICY)):
        raise ValueError('Recorded comparison programs or management policy differ from the frozen winner')


def _check_financial_ledger(evidence, actor, prefix):
    ledger = actor.execution_state
    if (evidence['status'] != 'completed' or evidence['complete'] is not True
            or evidence['requested_window_completed'] is not True
            or evidence['committed_sequence'] != prefix.last_sequence
            or evidence['run']['run_id'] != prefix.run_id
            or (evidence['run']['strategy_id'], evidence['run']['strategy_revision'], evidence['run']['code_hash'])
               != (actor.strategy_id, actor.revision, actor.program_release.header['code_hash'])
            or not {row.account_id for row in actor.assignments()} <= set(evidence['run']['account_ids'])):
        raise ValueError('Recorded comparison differs from its completed financial identity')
    fills, fees = evidence['fills'], evidence['commissions']
    if (len(fills) != len(ledger._fills) or len(fees) != len(ledger._fees)
            or {row['execution_id'] for row in fills} != set(ledger._fills)
            or {row['execution_id'] for row in fees} != set(ledger._fees)):
        raise ValueError('Recorded comparison ledger does not cover exact committed executions and fees')
    for row in fills:
        fact = ledger._fills[row['execution_id']]
        buying = fact.action in {'enter_long', 'add_long'}
        if (row['account_id'] != fact.account_id or row['ticker'] != fact.ticker
                or str(row['broker_order_id']) != str(fact.order_id)
                or row['side'].upper() not in ({'B', 'BUY'} if buying else {'S', 'SELL'})
                or Decimal(str(row['quantity'])) != fact.quantity
                or float(row['price']).hex() != float(fact.price).hex()
                or _clock(row['source_event_time']) != fact.filled_at):
            raise ValueError('Recorded comparison fill differs from its replayed actual ledger')
    for row in fees:
        fact = ledger._fees[row['execution_id']]
        if (row['status'] != 'final' or row['currency'] != fact.currency
                or Decimal(str(row['commission'])) != fact.commission
                or row['account_id'] != ledger._fills[fact.execution_id].account_id):
            raise ValueError('Recorded comparison fee differs from its replayed actual ledger')


async def read_recorded_program_session_metrics(reader_factory, prefix, packet, blocks, source, reference, *,
        day, financial_page_size, maximum_financial_rows, decision_page_size,
        maximum_decisions, maximum_observation_children, maximum_measure_rows):
    """Return measures from one bounded, verified completed native session."""
    metrics, _, _ = await _read_recorded_program_session(reader_factory, prefix, packet, blocks, source,
        reference, day=day, financial_page_size=financial_page_size,
        maximum_financial_rows=maximum_financial_rows, decision_page_size=decision_page_size,
        maximum_decisions=maximum_decisions, maximum_observation_children=maximum_observation_children,
        maximum_measure_rows=maximum_measure_rows)
    return metrics


async def _read_recorded_program_session(reader_factory, prefix, packet, blocks, source, reference, *,
        day, financial_page_size, maximum_financial_rows, decision_page_size,
        maximum_decisions, maximum_observation_children, maximum_measure_rows):
    """Join one completed recorded session; never launch or re-execute it.

    Financial facts and observation pages read concurrently on distinct owned
    clients. Page cursors, row/memory bounds and the stable V4 prefix are checked
    by the existing readers. Source linkage and columnar calculation run off-loop.
    The optimizer's first clock is an explicit baseline, excluded from measures.
    """
    if (not callable(reader_factory) or type(prefix) is not V4CommittedPrefix
            or prefix.status != 'completed' or prefix.last_sequence < 1 or not prefix.batch_ids
            or type(packet) is not RecoveredProgramPolicy or packet.run_id != prefix.run_id
            or packet.journal_sequence != prefix.last_sequence
            or type(blocks) is not tuple or not blocks or any(type(block) is not PreparedProgramBlock for block in blocks)
            or type(source) is not SignalStreamRuntime or type(reference) is not OptimizationReference
            or reference.clock_windows is None or day not in reference.sessions or day not in reference.clock_windows
            or type(reference.clock_windows[day]) is not ReferenceClockWindow
            or type(financial_page_size) is not int or not 1 <= financial_page_size <= 1000
            or type(maximum_financial_rows) is not int or not 1 <= maximum_financial_rows <= 1000000
            or type(decision_page_size) is not int or not 1 <= decision_page_size <= 500
            or type(maximum_decisions) is not int or not 1 <= maximum_decisions <= 1000000
            or type(maximum_observation_children) is not int or not 1 <= maximum_observation_children <= 1000000
            or type(maximum_measure_rows) is not int or maximum_measure_rows < 1):
        raise ValueError('Recorded comparison lacks its bounded completed native authorities')
    window = reference.clock_windows[day]
    actor = await _owned_worker(clone_recovered_program_actor, packet)
    await _owned_worker(verify_frozen_program_release, reference, actor.program_release)
    header = actor.program_release.header
    if ((header['source_winner_hash'], header['source_generation'], header['source_candidate'])
            != (reference.winner_sha256, reference.generation, reference.candidate)
            or header['entry_cash'] != Decimal(str(reference.execution['entry_dollars']))
            or header['add_cash'] != Decimal(str(reference.execution['add_dollars']))):
        raise ValueError('Recorded comparison strategy differs from its frozen winner and cash parameters')
    source_cursor = source.typed_source_cursor()
    source_hashes = source.typed_source_commit_hashes()
    if (source_cursor[2] != window.last_clock or source_hashes != packet.source_commit_hashes
            or any(block.release_hash != header['content_hash'] for block in blocks)):
        raise ValueError('Recorded comparison source differs from its complete recovered programme')
    clients, client_ids, client_lock = [], set(), Lock()
    def owned_factory():
        client = reader_factory()
        with client_lock:
            if id(client) in client_ids:
                raise ValueError('Recorded comparison reads cannot reuse an owned connection')
            clients.append(client)
            client_ids.add(id(client))
        return client
    def financial():
        client = owned_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Recorded comparison requires a SELECT-only financial client')
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Recorded comparison prefix moved before financial read')
            result = load_typed_backtest_financial_evidence(client, prefix.run_id,
                page_size=financial_page_size, maximum_rows=maximum_financial_rows)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Recorded comparison prefix moved after financial read')
            return result
        finally:
            client.close()
    async def observations():
        return await read_rule_decision_observations_async(owned_factory, prefix,
            page_size=decision_page_size, maximum_decisions=maximum_decisions,
            maximum_children=maximum_observation_children)
    job = asyncio.ensure_future(asyncio.gather(_owned_worker(financial), observations(), return_exceptions=True))
    try:
        results = await asyncio.shield(job)
    except asyncio.CancelledError:
        await _settle_owned_task(job)
        raise
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        if len(errors) == 1: raise errors[0]
        raise BaseExceptionGroup('Recorded comparison reads failed', errors)
    evidence, decisions = results
    def project():
        if (source.typed_source_cursor() != source_cursor or source.typed_source_commit_hashes() != source_hashes
                or program_policy_fingerprint(packet.actor) != packet.state_hash):
            raise ValueError('Recorded comparison native inputs changed during its reads')
        _check_financial_ledger(evidence, actor, prefix)
        previous = None
        for offset in range(window.clocks):
            at = window.first_clock + timedelta(seconds=offset)
            sequence, checksum, cutoff = source.typed_source_boundary(at)
            if (cutoff != at or not 1 <= sequence <= len(source_hashes)
                    or source_hashes[sequence - 1] != checksum
                    or previous is not None and sequence != previous + 1):
                raise ValueError('Recorded comparison lacks a complete committed one-second source grid')
            previous = sequence
        origin = market_day_boundary(day, 0)
        requested = evidence['session_window']
        start_ms = int((window.requested_start - origin).total_seconds() * 1000) + SESSION_OPEN_OFFSET_MS
        end_ms = int((window.last_clock - origin).total_seconds() * 1000) + SESSION_OPEN_OFFSET_MS
        if (str(evidence['run']['session_date']) != day or requested != dict(start_local_ms=start_ms, end_local_ms=end_ms)):
            raise ValueError('Recorded comparison completed a different requested session window')
        by_listing = {}
        for block in blocks:
            for listing in block.history_hashes:
                if listing in by_listing and by_listing[listing] is not block:
                    raise ValueError('Recorded comparison has ambiguous prepared listing origins')
                by_listing[listing] = block
        observed, excluded = [], 0
        for parent, children, observation in decisions:
            assignment = actor._by_assignment.get(parent['assignment_id'])
            block = by_listing.get(parent['listing_id'])
            if assignment is None or block is None:
                raise ValueError('Recorded comparison decision lacks its prepared assignment')
            verify_rule_decision_prepared_source(parent, children, release=actor.program_release,
                block=block, assignment=assignment, source=source)
            if not window.first_clock <= observation.observed_at <= window.last_clock:
                raise ValueError('Recorded comparison observation exceeds its frozen clock window')
            if observation.observed_at == window.first_clock:
                excluded += 1
                continue
            observed.append(((assignment.account_id, actor.strategy_id, assignment.assignment_id,
                actor._listings[assignment.assignment_id]), observation))
        unique_blocks = {id(block): block for block in blocks}
        market = pl.concat([block.market.select('boundary_at', 'available_at', 'listing_id', 'reference_close')
                            for block in unique_blocks.values()], how='vertical')
        metrics = project_filled_session_measures(actor.execution_state, market, tuple(observed),
            start_at=window.first_clock, stop_at=window.last_clock, maximum_rows=maximum_measure_rows,
            assignment_tickers={(row.account_id, actor.strategy_id, row.assignment_id,
                actor._listings[row.assignment_id]): row.ticker for row in actor._by_assignment.values()})
        app = dict(winner_sha256=reference.winner_sha256, run_id=prefix.run_id,
            strategy_digest=header['content_hash'], full_session=True, market_cursor_verified=True,
            verified_sequence=prefix.last_sequence, source_commit_count=len(source_hashes),
            excluded_origin_observations=excluded, metrics=MappingProxyType(metrics),
            execution_window=MappingProxyType(dict(start_at=window.requested_start, end_at=window.last_clock)),
            measurement_window=MappingProxyType(dict(origin_at=window.first_clock, end_at=window.last_clock,
                samples=window.metric_intervals, interval_ms=1000)))
        # Reuse the same all-session comparer, preserving missing sessions.
        compare_sessions(reference, {day: app})
        if (source.typed_source_cursor() != source_cursor or source.typed_source_commit_hashes() != source_hashes
                or program_policy_fingerprint(packet.actor) != packet.state_hash):
            raise ValueError('Recorded comparison native inputs changed during calculation')
        return RecordedProgramSessionMetrics(day, MappingProxyType(app),
            _comparison_hash(app), _COMPARISON_PROOF)
    metrics = await _owned_worker(project)
    return metrics, evidence, decisions


def compare_recorded_program_sessions(reference, results):
    """Admit only records produced by the joined native reader/calculator."""
    if type(reference) is not OptimizationReference or reference.clock_windows is None:
        raise ValueError('Recorded comparison requires all frozen optimizer window evidence')
    actual = {}
    for day, result in results.items():
        if (type(result) is not RecordedProgramSessionMetrics or result._proof is not _COMPARISON_PROOF
                or result.day != day or _comparison_hash(result.app) != result._hash):
            raise ValueError('Recorded comparison lacks unchanged native joined evidence')
        actual[day] = result.app
    return compare_sessions(reference, actual)
