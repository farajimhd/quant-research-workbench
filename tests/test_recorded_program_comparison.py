"""Joined reader composition with explicit SQL seams; native persistence is tested separately."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import MappingProxyType
from threading import Event, get_ident

import polars as pl
import pytest

from src.backend import recorded_program_comparison as flow
from src.backend.backtest_program_inputs import PreparedProgramBlock
from src.backend.program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, program_policy_fingerprint
from src.backend.signal_stream_runtime_service import SignalStreamRuntime
from src.backend.signal_stream_typed_readback import CommittedCursorHead
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.filled_position_state import FilledPositionState, FillFact, FeeFact
from src.trading_runtime.oms_assignment_observation import AssignmentOrderObservation, ObservedWorkingOrder
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis
from src.trading_runtime.optimized_strategy_reference import ReferenceClockWindow
from src.trading_runtime import optimized_program_specification as compiler
from tests.test_optimized_program_specification import source as compiler_source
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_rule_decision_basis_projection import values

START = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
DAY = '2026-08-03'


def inputs(monkeypatch):
    reference, fields, universe, kwargs = compiler_source(monkeypatch)
    release = restore_release(project_release(compiler.compile_frozen_winner(reference, fields,
        universe_identity=universe), fields, **kwargs))
    _, template = definition_and_actor()
    assignment = replace(template.assignments()[0], account_id='account')
    actor = AssignedProgramStrategy(release, (assignment,), {'assignment': 'listing'})
    key = ('account', actor.strategy_id, 'assignment', 'listing')
    for index, (action, quantity, price) in enumerate([
            ('enter_long', '10', '10'), ('add_long', '5', '12'),
            ('reduce_long', '5', '11'), ('exit', '10', '9')]):
        identity = str(index)
        actor.execution_state.apply_fill(FillFact(identity, identity, identity, *key, 'ABC', action,
            D(quantity), D(price), START + timedelta(seconds=index + 1, milliseconds=500)))
        actor.execution_state.apply_fee(FeeFact(identity, D(1), 'USD'))
    clocks = [START + timedelta(seconds=index) for index in range(1, 6)]
    hashes = tuple(f'{index:064x}' for index in range(1, 6))
    source = SignalStreamRuntime()
    source._install_committed_source(CommittedCursorHead(DAY, 5, hashes[-1], {}, {}, 0,
        clocks[-1].isoformat(), hashes, tuple(at.isoformat() for at in clocks)),
        configuration_revision='configuration', source_revision='source')
    market = pl.DataFrame(dict(boundary_at=clocks, available_at=clocks, listing_id=['listing'] * 5,
        evaluation_block=['origin'] * 5, observed_ordinal=list(range(5)), reference_close=[10., 11., 10., 12., 9.]))
    masks = market.select('listing_id', 'evaluation_block', 'observed_ordinal', 'boundary_at')
    block = PreparedProgramBlock(release.header['content_hash'], 'origin', 0, 5, {'listing': 'd' * 64}, market, masks)
    prefix = V4CommittedPrefix('run', 100, values()['batch_id'], f'{DAY}:5000', 'completed', (values()['batch_id'],))
    packet = RecoveredProgramPolicy(actor, 100, 4, 5, MappingProxyType({'assignment': 5}), 'run', hashes,
        program_policy_fingerprint(actor), _RECOVERY_PROOF)
    decisions = []
    for ordinal, (at, quantity) in enumerate(zip(clocks, (0., 10., 15., 10., 0.))):
        parent, children = project_rule_decision_basis({**values('wait'), 'batch_id': prefix.last_batch_id,
            'release_id': release.header['release_id'], 'release_content_hash': release.header['content_hash'],
            'source_session_date': DAY, 'source_batch_sequence': ordinal + 1, 'source_commit_hash': hashes[ordinal],
            'history_input_hash': 'd' * 64, 'evaluation_block': 'origin', 'observed_ordinal': ordinal,
            'boundary_at': at, 'basis_sequence': 20 + ordinal, 'decision_sequence': 21 + ordinal,
            'decision_id': block.completion_identity(run_id='run', assignment_id='assignment',
                source_hash=hashes[ordinal], listing_id='listing', ordinal=ordinal, terminal=False)}, ())
        observation = AssignmentOrderObservation('account', 'assignment', 'ABC', at, False, False, (),
            (ObservedWorkingOrder('stop', 'protective_stop', quantity, 0., 9., 'Submitted'),) if quantity else (), 0.)
        decisions.append((parent, children, observation))
    ledger = actor.execution_state
    fills = tuple(dict(execution_id=fact.execution_id, account_id=fact.account_id, ticker=fact.ticker,
        broker_order_id=fact.order_id, side='BUY' if fact.action in {'enter_long', 'add_long'} else 'SELL',
        quantity=fact.quantity, price=float(fact.price), source_event_time=fact.filled_at)
        for fact in ledger._fills.values())
    fees = tuple(dict(execution_id=fact.execution_id, account_id='account', status='final',
        currency=fact.currency, commission=fact.commission) for fact in ledger._fees.values())
    evidence = dict(status='completed', complete=True, requested_window_completed=True, committed_sequence=100,
        run=dict(run_id='run', strategy_id=actor.strategy_id, strategy_revision=actor.revision,
            code_hash=release.header['code_hash'], account_ids=('account',), session_date=DAY),
        fills=fills, commissions=fees, session_window=dict(start_local_ms=14400000, end_local_ms=14405000))
    days = [f'2026-08-{index:02d}' for index in range(1, 31)]
    reference = replace(reference, sessions={day: dict(net_pnl=0., closed_positions=0, terminal_valid=True) for day in days},
        clock_windows={DAY: ReferenceClockWindow(START, clocks[0], clocks[-1], 5, 'a' * 64, 'b' * 64)})
    return prefix, packet, block, source, reference, tuple(decisions), evidence


def install_reads(monkeypatch, decisions, evidence, *, delayed=False):
    clients, entered, release = [], Event(), Event()
    main = get_ident()
    class Client:
        def __init__(self): self.closed = False; clients.append(self)
        def execute(self, query):
            assert query == "SELECT getSetting('readonly')" and get_ident() != main
            return '1'
        def close(self): self.closed = True
    monkeypatch.setattr(flow, 'load_verified_v4_prefix', lambda *args: prefix_holder[0])
    prefix_holder = [None]
    def financial(client, run_id, **kwargs):
        assert get_ident() != main and kwargs == dict(page_size=2, maximum_rows=20)
        if delayed:
            entered.set()
            assert release.wait(5)
        return evidence
    monkeypatch.setattr(flow, 'load_typed_backtest_financial_evidence', financial)
    async def observations(factory, prefix, *, page_size, maximum_decisions, maximum_children):
        client = factory()
        try:
            assert page_size == 2 and maximum_children == 20
            if len(decisions) > maximum_decisions:
                raise ValueError('decision bound')
            return decisions
        finally: client.close()
    monkeypatch.setattr(flow, 'read_rule_decision_observations_async', observations)
    return Client, clients, prefix_holder, entered, release


def run_kwargs():
    return dict(day=DAY, financial_page_size=2, maximum_financial_rows=20, decision_page_size=2,
        maximum_decisions=10, maximum_observation_children=20, maximum_measure_rows=100)


def test_joined_comparison_uses_frozen_first_clock_exclusion_and_keeps_all30(monkeypatch):
    prefix, packet, block, source, reference, decisions, evidence = inputs(monkeypatch)
    factory, clients, holder, _, _ = install_reads(monkeypatch, decisions, evidence)
    holder[0] = prefix
    result = asyncio.run(flow.read_recorded_program_session_metrics(factory, prefix, packet, (block,), source, reference,
        **run_kwargs()))
    assert result.app['metrics']['net_pnl'] == -19 and result.app['metrics']['drawdown'] == 31.
    assert result.app['metrics']['capital_dollar_seconds'] == 380. and result.app['metrics']['stop_risk_dollar_seconds'] == 65.
    assert result.app['metrics']['inactivity_fraction'] == .25 and result.app['excluded_origin_observations'] == 1
    assert all(client.closed for client in clients) and len(clients) == 2
    rows = flow.compare_recorded_program_sessions(reference, {DAY: result})
    assert len(rows) == 30 and rows[2]['status'] == 'compared'
    assert sum(row['status'] == 'not_run' for row in rows) == 29
    with pytest.raises(ValueError, match='unchanged native'):
        flow.compare_recorded_program_sessions(reference, {DAY: replace(result, app={**result.app, 'run_id': 'foreign'})})


def test_completed_zero_fill_run_measures_native_unfilled_order_observations(monkeypatch):
    prefix, packet, block, source, reference, decisions, evidence = inputs(monkeypatch)
    packet.actor.execution_state = FilledPositionState()
    packet = replace(packet, state_hash=program_policy_fingerprint(packet.actor))
    evidence = {**evidence, 'fills': (), 'commissions': ()}
    decisions = tuple((parent, children, replace(observation, pending_acquisition=True,
        working_orders=(ObservedWorkingOrder('entry', 'entry', 10., 9., 0., 'Submitted'),)))
        for parent, children, observation in decisions)
    factory, clients, holder, _, _ = install_reads(monkeypatch, decisions, evidence)
    holder[0] = prefix
    result, returned_evidence, returned_decisions = asyncio.run(
        flow._read_recorded_program_session(factory, prefix, packet,
            (block,), source, reference, **run_kwargs()))
    metrics = result.app['metrics']
    assert metrics['net_pnl'] == metrics['closed_positions'] == metrics['filled_batches'] == 0
    assert metrics['drawdown'] == metrics['capital_dollar_seconds'] == metrics['stop_risk_dollar_seconds'] == 0
    assert metrics['inactivity_fraction'] == 1 and metrics['terminal_valid']
    assert returned_evidence['fills'] == () and returned_decisions == decisions
    assert not packet.actor.execution_state.positions and all(client.closed for client in clients)


@pytest.mark.parametrize('failure', ['fee', 'fill', 'missing_fee', 'window', 'source_gap', 'decision_bound', 'source_parent'])
@pytest.mark.parametrize('collector', [False, True])
def test_joined_comparison_rejects_partial_or_mismatched_recorded_facts(monkeypatch, failure, collector):
    prefix, packet, block, source, reference, decisions, evidence = inputs(monkeypatch)
    if failure == 'fee': evidence['commissions'] = ({**evidence['commissions'][0], 'commission': D(0)}, *evidence['commissions'][1:])
    elif failure == 'fill': evidence['fills'] = ({**evidence['fills'][0], 'price': 10.000000000000002}, *evidence['fills'][1:])
    elif failure == 'missing_fee': evidence['commissions'] = evidence['commissions'][1:]
    elif failure == 'window': evidence['session_window']['end_local_ms'] += 1000
    elif failure == 'source_gap': source._typed_cutoffs[1] += timedelta(milliseconds=1)
    elif failure == 'source_parent': decisions = (({**decisions[0][0], 'history_input_hash': 'e' * 64}, *decisions[0][1:]), *decisions[1:])
    factory, clients, holder, _, _ = install_reads(monkeypatch, decisions, evidence)
    holder[0] = prefix
    kwargs = run_kwargs()
    if failure == 'decision_bound': kwargs['maximum_decisions'] = 4
    read = flow.read_recorded_program_session_metrics
    if collector:
        from src.backend.optimized_audit_collection import collect_native_execution_audit
        read = collect_native_execution_audit
        kwargs['maximum_audit_rows'] = 100
    with pytest.raises(ValueError):
        asyncio.run(read(factory, prefix, packet, (block,), source, reference, **kwargs))
    assert clients and all(client.closed for client in clients)


@pytest.mark.parametrize('collector', [False, True])
def test_joined_read_cancellation_drains_concurrent_owned_clients(monkeypatch, collector):
    prefix, packet, block, source, reference, decisions, evidence = inputs(monkeypatch)
    factory, clients, holder, entered, release = install_reads(monkeypatch, decisions, evidence, delayed=True)
    holder[0] = prefix
    read, kwargs = flow.read_recorded_program_session_metrics, run_kwargs()
    if collector:
        from src.backend.optimized_audit_collection import collect_native_execution_audit
        read = collect_native_execution_audit
        kwargs['maximum_audit_rows'] = 100
    async def run():
        pending = asyncio.create_task(read(factory, prefix, packet,
            (block,), source, reference, **kwargs))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            # The observation lane finishes while the financial worker is
            # deliberately blocked; this is concurrent I/O, not a second
            # sequential pass that merely uses async function syntax.
            assert any(client.closed for client in clients) and any(not client.closed for client in clients)
            pending.cancel()
            await asyncio.sleep(0)
        finally: release.set()
        with pytest.raises(asyncio.CancelledError): await pending
    asyncio.run(run())
    assert clients and all(client.closed for client in clients)


def test_matching_winner_label_cannot_hide_a_changed_rule_program(monkeypatch):
    _, packet, _, _, reference, _, _ = inputs(monkeypatch)
    release = packet.actor.program_release
    changed = replace(release, programs={**release.programs, 'entry': release.programs['exit']})
    with pytest.raises(ValueError, match='programs or management policy'):
        flow.verify_frozen_program_release(reference, changed)
