"""Publish independently reconstructed terminal images at an existing V4 fence.

This is a dedicated repair worker, not journal or restart admission. The caller
owns the managed writer/lease and must cold-verify the resulting common financial
checkpoint before admitting a runtime. Component publication is deliberately the
existing children/readback/Keeper-CAS protocol, so interrupted repairs can repeat
only the identical target images; there is no cross-component transaction.
"""
from dataclasses import dataclass
from datetime import date

from .qmd_columnar_history_transport import _owned_worker
from .backtest_market_data import market_day_boundary
from .backtest_v4_keeper_lease import BacktestV4KeeperLease
from .program_terminal_financial_rollforward import TerminalClosingAccountingResult
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_broker_match_snapshot import (
    ManagedSharedBrokerMatchHeadReader, verify_shared_broker_snapshot,
    publish_shared_broker_snapshot,
)
from src.trading_runtime.arte_oms_observation_snapshot import (
    ManagedSharedOmsObservationHeadReader, verify_shared_oms_observations,
    publish_shared_oms_observations,
)
from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchHead
from src.trading_runtime.strategy_one_oms_observation_snapshot import OmsObservationHead
from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, prepare_captured_portfolio_snapshot,
    publish_prepared_portfolio_snapshot, _SNAPSHOT_FAMILIES, _SNAPSHOT_COMMIT,
)
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_journal_writer import _CONTRACTS
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.keeper_session import ManagedKeeperSession


@dataclass(frozen=True)
class TerminalCheckpointPublicationReceipt:
    prefix: V4CommittedPrefix
    broker_head: BrokerMatchHead
    observation_head: OmsObservationHead
    portfolio_state_hashes: tuple
    previous_heads: tuple
    lease_epoch: int


def _publish(writer_client, *, read_client_factory, writer_connection_factory,
        session, lease, prefix, previous_prefix, previous_broker_head,
        previous_observation_head, broker_result, broker_contract, observations,
        portfolios, concurrency):
    if (type(prefix) is not V4CommittedPrefix or type(previous_prefix) is not V4CommittedPrefix
            or prefix.status != 'running' or previous_prefix.status != 'running'
            or previous_prefix.run_id != prefix.run_id
            or not 0 < previous_prefix.last_sequence < prefix.last_sequence
            or prefix.batch_ids[:len(previous_prefix.batch_ids)] != previous_prefix.batch_ids
            or type(previous_broker_head) is not BrokerMatchHead
            or type(previous_observation_head) is not OmsObservationHead
            or any(h.run_id != prefix.run_id or h.checkpoint_sequence != previous_prefix.last_sequence
                   or h.journal_batch_id != previous_prefix.last_batch_id
                   for h in (previous_broker_head, previous_observation_head))
            or type(session) is not ManagedKeeperSession or not session.writable
            or type(lease) is not BacktestV4KeeperLease or lease.run_id != prefix.run_id or lease.epoch < 2
            or lease.owner._session is not session
            or getattr(writer_client, 'backtest_v4_lease', None) is not lease
            or getattr(writer_client, 'manager_keeper_session', None) is not session
            or getattr(writer_client, 'typed_insert_strict', False) is not True
            or type(getattr(writer_client, 'typed_insert_dispatch', None)) is not TypedInsertDispatch
            or writer_client.typed_insert_dispatch.keeper is not session.client
            or not callable(read_client_factory) or not callable(writer_connection_factory)
            or type(concurrency) is not int or not 1 <= concurrency <= 8
            or type(broker_result) is not TerminalClosingAccountingResult
            or type(portfolios) is not tuple or not 1 <= len(portfolios) <= 128
            or any(type(p) is not CapturedPortfolioSnapshot for p in portfolios)):
        raise ValueError('Terminal checkpoint publication lacks its exact owned repair scope')
    scope = broker_result.scope
    if (scope.run_id != prefix.run_id or scope.first_sequence != previous_prefix.last_sequence+1
            or scope.last_sequence != prefix.last_sequence
            or previous_broker_head.snapshot_hash != scope.initial_snapshot_hash):
        raise ValueError('Terminal checkpoint projection differs from its original head')
    broker = verify_shared_broker_snapshot(broker_result.broker_snapshot,
        broker_contract=broker_contract, run_id=prefix.run_id,
        checkpoint_sequence=prefix.last_sequence,
        expected_content_hash=broker_result.broker_snapshot.snapshot['content_hash'])
    observed = verify_shared_oms_observations(observations)
    root = broker.snapshot
    expected_clock = market_day_boundary(date.fromisoformat(root['session_date']), root['boundary_ms'])
    if (expected_clock != scope.stop_at
            or prefix.source_cursor != f"{root['session_date']}:{root['boundary_ms']}"
            or previous_prefix.source_cursor != f"{root['session_date']}:{int((scope.start_at-market_day_boundary(root['session_date'],0)).total_seconds()*1000)}"
            or any(row['quantity_f64_bits'] for row in broker.positions) or broker.open_orders
            or any(row['has_performance_path'] or row['unrealized_f64_bits']
                   or row['market_value_f64_bits'] for row in broker.marks)
            or any(observed.root[key] != root[key]
                   for key in ('run_id','session_date','checkpoint_sequence','boundary_ms'))
            or any(row['broker_status'] not in ('Filled','Cancelled') for row in observed.observations)):
        raise ValueError('Terminal checkpoint images lack one flat completed market fence')
    accounts = {row['account_id'] for row in broker.accounts}
    if len(accounts) != len(portfolios) or {p.account_id for p in portfolios} != accounts:
        raise ValueError('Terminal checkpoint account population differs')
    prepared = []
    for capture in sorted(portfolios, key=lambda p:p.account_id):
        if (capture.run_id != prefix.run_id or capture.state_revision != prefix.last_sequence
                or capture.snapshot_at != expected_clock or capture.allocations
                or capture.commands or capture.requests or capture.reconciliation
                or any(r.remaining_quantity or r.status not in
                       {'released','filled','cancelled','rejected','policy_blocked'}
                       for r in capture.reservations)):
            raise ValueError('Terminal Portfolio capture is not an exact flat synchronized projection')
        prepared.append(prepare_captured_portfolio_snapshot(capture))
    keeper, generation, client_id = session.client, session._generation, session.client.client_id
    dispatch = writer_client.typed_insert_dispatch
    broker_reader = ManagedSharedBrokerMatchHeadReader(session)
    oms_reader = ManagedSharedOmsObservationHeadReader(session)
    targets = (root['content_hash'], observed.root['content_hash'])
    selected = [previous_broker_head, previous_observation_head]
    def guard(*, allow_target=False):
        if (not session.writable or session.client is not keeper or session._generation != generation
                or keeper.client_id != client_id or writer_client.manager_keeper_session is not session
                or writer_client.backtest_v4_lease is not lease
                or writer_client.typed_insert_dispatch is not dispatch or dispatch.keeper is not keeper
                or writer_client.typed_insert_strict is not True):
            raise RuntimeError('Terminal checkpoint publication ownership changed')
        lease.assert_current()
        actual = (broker_reader.read_head(run_id=prefix.run_id), oms_reader.read_head(run_id=prefix.run_id))
        for i, head in enumerate(actual):
            if head == selected[i]:
                continue
            # Only an identical already-published target can resume a partial
            # repair. The native publisher then verifies all selected rows.
            if (not allow_target or head.checkpoint_sequence != prefix.last_sequence or head.journal_batch_id != prefix.last_batch_id
                    or head.snapshot_hash != targets[i] or head.keeper_version <= selected[i].keeper_version):
                raise RuntimeError('Terminal checkpoint selected head changed')
            selected[i] = head
    reader = read_client_factory()
    if reader is writer_client:
        raise ValueError('Terminal checkpoint verification cannot borrow its writer')
    try:
        if reader.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError('Terminal checkpoint requires a dedicated SELECT-only reader')
        guard(allow_target=True)
        if load_verified_v4_prefix(reader, prefix.run_id) != prefix:
            raise RuntimeError('Terminal checkpoint current journal prefix differs')
        cursor = load_latest_backtest_cursor(reader, prefix)
        if (not isinstance(cursor, dict) or any(cursor.get(k) != value for k,value in (
                ('run_id',prefix.run_id),('event_sequence',prefix.last_sequence),
                ('batch_id',prefix.last_batch_id),('session_date',root['session_date']),('boundary_ms',root['boundary_ms'])))):
            raise RuntimeError('Terminal checkpoint cursor differs from its attested projection')
        storage_preflight(writer_client, tables=tuple(_CONTRACTS[name]
            for name in (*[table for table,_ in _SNAPSHOT_FAMILIES],_SNAPSHOT_COMMIT)))
        guard()
        selected[0] = publish_shared_broker_snapshot(writer_client, session, broker,
            broker_contract=broker_contract, journal_batch_id=prefix.last_batch_id,
            connection_factory=writer_connection_factory, concurrency=concurrency)
        guard()
        selected[1] = publish_shared_oms_observations(writer_client, session, observed,
            journal_batch_id=prefix.last_batch_id)
        guard()
        hashes = []
        for capture in prepared:
            guard()
            hashes.append((capture.account_id,publish_prepared_portfolio_snapshot(writer_client,capture)))
            guard()
        if (load_verified_v4_prefix(reader,prefix.run_id) != prefix
                or load_latest_backtest_cursor(reader,prefix) != cursor):
            raise RuntimeError('Terminal checkpoint journal changed during publication')
        guard()
        if any(h.checkpoint_sequence != prefix.last_sequence or h.journal_batch_id != prefix.last_batch_id
               or h.snapshot_hash != digest for h,digest in zip(selected,targets,strict=True)):
            raise RuntimeError('Terminal checkpoint publication did not select its exact images')
        return TerminalCheckpointPublicationReceipt(prefix,*selected,tuple(hashes),
            (previous_broker_head,previous_observation_head),lease.epoch)
    finally:
        reader.close()


async def publish_terminal_program_checkpoints(writer_client, **scope):
    """Drain one dedicated publication worker even if the caller is cancelled.

    The caller owns writer/session/lease lifetimes and exclusive submission.
    This does not mutate a journal worker, publish a journal batch, complete a
    run, or grant restart admission. Run normal financial recovery afterwards.
    """
    return await _owned_worker(_publish,writer_client,**scope)
