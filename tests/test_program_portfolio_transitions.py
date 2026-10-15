import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.trading_runtime import program_portfolio_transitions as module
from src.trading_runtime.arte_journal_writer import ArteJournalWriter
from src.trading_runtime.arte_portfolio_snapshot import (
    load_portfolio_snapshot, prepare_captured_portfolio_snapshot, publish_prepared_portfolio_snapshot,
)
from src.trading_runtime.keeper_ownership import KeeperOwnershipCoordinator
from src.trading_runtime.portfolio import PortfolioManagementEngine, PortfolioControlMode
from tests.test_arte_portfolio_recovery import _client, AT
from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state


def setup(monkeypatch, *, failure=None):
    client, profile = _client(monkeypatch)
    from tests.test_arte_portfolio_policy import Catalog
    catalog = Catalog()
    catalog.tables, catalog.inserts = client.tables, client.inserts
    original_execute = client.execute
    client.execute = lambda sql: (catalog.execute(sql) if "WHERE policy_hash=" in sql
        or sql.startswith('INSERT INTO arte.trading_portfolio_policy_') else original_execute(sql))
    recovered = recover_portfolio_engine_state(client, run_id='live-run', profiles=(profile,),
        state_revisions={'account-id': 7}, cutoff_at=AT)
    def forbidden(*args, **kwargs): pytest.fail('SQLite path called')
    engine = PortfolioManagementEngine((profile,), journal=SimpleNamespace(append=forbidden,
        save_portfolio_state=forbidden, portfolio_states=forbidden), run_id='live-run',
        strategy_id='strategy-a', strategy_revision=1, typed_recovery=recovered, event_clock=lambda: AT)
    from src.trading_runtime.portfolio import PortfolioSyncState
    # This fixture tests the post-sync current image. Cold recovery itself
    # intentionally blocks entries until actual broker reconciliation.
    engine.states['account-id'].sync_state = PortfolioSyncState.SYNCHRONIZED
    engine.states['account-id'].stale_reason = ''
    class Keeper:
        def __init__(self): self.rows = {}
        def ensure_path(self, path): pass
        def create(self, path, wire):
            if path in self.rows: raise RuntimeError('exists')
            self.rows[path] = (wire, 0)
        def get(self, path):
            wire, version = self.rows[path]
            return wire, SimpleNamespace(version=version)
        def set(self, path, wire, version):
            assert self.rows[path][1] == version
            self.rows[path] = wire, version + 1
    coordinator = object.__new__(KeeperOwnershipCoordinator)
    coordinator._client = Keeper()
    coordinator.current = True
    coordinator.proofs = []
    @asynccontextmanager
    async def claim(run_id, account_id):
        yield 'test-lease'
    coordinator.claim_portfolio_snapshot = claim
    coordinator.portfolio_snapshot_claim_is_current = lambda lease: coordinator.current
    def attest(*args, **kwargs):
        if failure == 'lease': raise RuntimeError('lease lost')
        assert kwargs['additional_leases']
        coordinator.proofs.append(args)
    coordinator.attest_portfolio_snapshot_receipt = attest
    coordinator.global_claims = []
    def acquire(resource, *, owner_id, ttl_seconds):
        value = dict(resource_id=resource, owner_id=owner_id, epoch=1)
        coordinator.global_claims.append(value)
        return value
    coordinator.acquire_portfolio_admission_lease = acquire
    coordinator.release_portfolio_admission_lease = lambda *args, **kwargs: True
    writer = object.__new__(ArteJournalWriter)
    writer._run_mode = 'live'
    writer._coalesce_batches = False
    writer._test_futures = []
    def submit(captured):
        future = Future()
        writer._test_futures.append(future)
        if failure == 'ack':
            future.set_exception(RuntimeError('ambiguous acknowledgement'))
        else:
            digest = publish_prepared_portfolio_snapshot(client, prepare_captured_portfolio_snapshot(captured))
            if failure == 'readback': digest = 'f' * 64
            future.set_result(digest)
        return future
    writer.submit_captured_portfolio_snapshot = submit
    from src.trading_runtime import program_portfolio_journal as source_journal
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_writer import TypedJournalBatch, _sealed_families
    writer.source_units = []
    writer.prefix = V4CommittedPrefix('live-run', 10, '00000000-0000-0000-0000-000000000010',
        'test', 'running', ())
    def source_submit(unit):
        writer.source_units.append(unit)
        base = unit if type(unit) is TypedJournalBatch else unit.base
        seal = ({'v3_control_ids': (unit.control['record_id'],)} if hasattr(unit, 'control')
            else {'v4_allocation_ids': (unit.allocation['record_id'],)} if hasattr(unit, 'allocation') else {})
        _sealed_families(base, **seal)
        writer.prefix = V4CommittedPrefix(base.run_id, base.last_sequence, base.batch_id,
            base.source_cursor, 'running', ())
        receipt = Future()
        receipt.set_result(base.batch_id)
        return receipt
    for name in ('submit_base_v4', 'submit_portfolio_control_v4', 'submit_portfolio_allocation_v4',
                 'submit_reservation_reason_v4'):
        setattr(writer, name, source_submit)
    monkeypatch.setattr(source_journal, 'load_verified_v4_prefix', lambda *args: writer.prefix)
    authority = module.ProgramPortfolioTransitionAuthority(client=client, writer=writer, coordinator=coordinator,
        attempt_id='00000000-0000-0000-0000-000000000002')
    prior = load_portfolio_snapshot(client, run_id='live-run', account_id='account-id', state_revision=7)['state_hash']
    authority.initialize_from_recovered_snapshot(run_id='live-run', account_id='account-id', revision=7,
                                                snapshot_hash=prior)
    return engine, authority, prior


@pytest.mark.parametrize('transition,arguments', [
    ('release_intent', {'intent_id': 'intent-1', 'reason': 'explicit cancel'}),
    ('set_control', {'mode': 'entries_paused', 'reason': 'operator'}),
    ('set_allocation_enabled', {'strategy_id': 'strategy-a', 'enabled': False, 'reason': 'operator'}),
])
def test_native_state_installs_only_after_snapshot_and_keeper_receipt(monkeypatch, transition, arguments):
    engine, authority, prior = setup(monkeypatch)
    receipt = asyncio.run(module.apply_program_portfolio_transition(engine, authority,
        account_id='account-id', prior_revision=7, prior_hash=prior, transition=transition, arguments=arguments))
    loaded = load_portfolio_snapshot(authority.client, run_id='live-run', account_id='account-id', state_revision=8)
    assert loaded['state_hash'] == receipt.snapshot_hash
    assert len(authority.coordinator.proofs) == 1
    assert authority.writer.source_units
    assert module._read_head(authority.coordinator._client, module._path('live-run', 'account-id'))[0] == 'committed'
    assert not engine._typed_admission_poisoned
    if transition == 'release_intent': assert engine.reservations['reservation-1'].status == 'released'
    if transition == 'set_control': assert engine.states['account-id'].control_mode == PortfolioControlMode.ENTRIES_PAUSED
    if transition == 'set_allocation_enabled': assert 'strategy-a' in engine.states['account-id'].disabled_strategy_allocations


def test_shared_v4_broker_sync_commits_source_and_snapshot_before_freshness(monkeypatch):
    from src.trading_runtime.ibkr_schema import AccountSummary, AccountLedger
    from src.trading_runtime.portfolio import PortfolioSyncState
    engine, authority, prior = setup(monkeypatch)
    state = engine.states['account-id']
    state.sync_state = PortfolioSyncState.ENTRIES_BLOCKED
    state.stale_reason = 'Typed recovery requires broker synchronization'
    summary = AccountSummary(account_id='account-id', netliquidation=10000,
        totalcashvalue=10000, availablefunds=10000, buyingpower=10000,
        grosspositionvalue=0,
        excessliquidity=10000, timestamp=AT)
    ledger = AccountLedger('account-id', 10000, 10000, 0, 10000, 0, 0, timestamp=AT)
    asyncio.run(authority.synchronize_broker_snapshot(engine, account_id='account-id',
        summary=summary, ledger=ledger, positions=[], open_orders=[]))
    assert engine.states['account-id'].sync_state is PortfolioSyncState.SYNCHRONIZED
    assert authority.writer.source_units[-1].portfolio_reconciliation_events
    head = module._read_head(authority.coordinator._client, module._path('other-run', 'account-id'))
    assert head[0:2] == ('committed', 8) and head[5] == 'live-run'
    assert authority.coordinator.proofs


def test_current_shared_head_cannot_supply_a_future_financial_image(monkeypatch):
    from datetime import timedelta
    engine, authority, _ = setup(monkeypatch)
    with pytest.raises(RuntimeError, match='later than the completed decision boundary'):
        asyncio.run(authority.verify_current_head(engine, 'account-id', as_of=AT - timedelta(microseconds=1)))


@pytest.mark.parametrize('failure', ['ack', 'readback', 'lease'])
def test_uncertain_transition_never_exposes_candidate_and_requires_cold_recovery(monkeypatch, failure):
    engine, authority, prior = setup(monkeypatch, failure=failure)
    before = engine.reservations['reservation-1']
    with pytest.raises(RuntimeError):
        asyncio.run(module.apply_program_portfolio_transition(engine, authority,
            account_id='account-id', prior_revision=7, prior_hash=prior, transition='release_intent',
            arguments={'intent_id': 'intent-1', 'reason': 'cancel'}))
    assert engine.reservations['reservation-1'] == before
    assert engine._typed_admission_poisoned
    assert module._read_head(authority.coordinator._client, module._path('live-run', 'account-id'))[0] == 'pending'


def test_stale_head_and_open_arguments_fail_closed(monkeypatch):
    engine, authority, prior = setup(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(module.apply_program_portfolio_transition(engine, authority,
            account_id='account-id', prior_revision=7, prior_hash=prior, transition='set_control',
            arguments={'mode': 'entries_paused', 'reason': 'operator', 'callback': lambda: None}))
    assert not engine._typed_admission_poisoned and not authority.writer._test_futures


def test_uncommitted_memory_mutation_cannot_be_laundered_into_a_receipt(monkeypatch):
    engine, authority, prior = setup(monkeypatch)
    engine.states['account-id'].disabled_strategy_allocations.add('uncommitted')
    with pytest.raises(RuntimeError, match='memory differs'):
        asyncio.run(module.apply_program_portfolio_transition(engine, authority,
            account_id='account-id', prior_revision=7, prior_hash=prior, transition='release_intent',
            arguments={'intent_id': 'intent-1', 'reason': 'cancel'}))
    assert not authority.writer.source_units and not engine._typed_admission_poisoned


def test_policy_selection_preserves_native_control_fact_and_catalog_snapshot(monkeypatch):
    engine, authority, prior = setup(monkeypatch)
    policy = engine.states['account-id'].profile.policy
    receipt = asyncio.run(module.apply_program_portfolio_transition(engine, authority,
        account_id='account-id', prior_revision=7, prior_hash=prior, transition='select_policy',
        arguments={'policy': policy, 'reason': 'reviewed policy'}))
    unit = authority.writer.source_units[0]
    assert unit.control['control_event'] == 'portfolio_policy_selected'
    assert unit.control['policy_hash'] is not None and unit.control['reason'] == 'reviewed policy'
    assert engine.states['account-id'].policy_override is not None
    assert receipt.snapshot_hash


def test_oms_fill_update_preserves_allocation_and_reservation_source_facts(monkeypatch):
    from src.trading_runtime.order_management import OrderGroupSnapshot, OrderManagementState
    from dataclasses import replace
    engine, authority, prior = setup(monkeypatch)
    identity = 'account-id:strategy-a:assignment-1:AAA'
    snapshot = OrderGroupSnapshot('group-1', 'intent-1', 'account-id', 'AAA', 'enter_long',
        OrderManagementState.PARTIALLY_FILLED, (), (), AT, AT, 3., 7., (), '', None,
        1, False, 'assignment-1', fill_role='entry', fill_incremental_quantity=1.)
    asyncio.run(module.apply_program_portfolio_transition(engine, authority,
        account_id='account-id', prior_revision=7, prior_hash=prior, transition='order_group_update',
        arguments={'snapshot': snapshot}))
    assert len(authority.writer.source_units) == 2
    from decimal import Decimal
    assert Decimal(authority.writer.source_units[0].allocation['incremental_quantity']) == 1
    assert engine.allocations[identity].quantity == 1
    assert engine.reservations['reservation-1'].filled_quantity == 3
