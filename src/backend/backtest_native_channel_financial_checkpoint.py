"""Cold channel held-position and acquisition confirmation; no order admission.

Source/fill recomputation, native checkpoint attestation and OMS ownership are
separate checks. This result covers held quantity, committed acquisition and
checkpoint references; native publication and OMS admission remain separate.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .backtest_market_data import SESSION_OPEN_OFFSET_MS, market_day_boundary
from .backtest_native_channel_exit_projection import NativeChannelExitCheckpointReference
from .backtest_typed_publisher import TypedBacktestReceipt
from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy


@dataclass(frozen=True, slots=True)
class NativeChannelHeldCheckpoint:
    request: object
    checkpoint: NativeChannelExitCheckpointReference
    conid: int
    held_quantity: float


def confirm_native_channel_held_checkpoint(client, manager_keeper, broker_keeper,
        receipt, *, run_id, policy, contexts, first_price_source):
    """Join complete pending evidence to attested roots and committed OMS groups.

    Run on the cold/worker path. Do not call from the market decision loop or
    treat this intermediate evidence as authorization to publish an exit.
    """
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.native_channel_manager_reader import load_attested_native_channel_manager_snapshot
    from src.trading_runtime.strategy_one_management_snapshot import ManagerSnapshotHead
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        BrokerMatchHead, load_attested_broker_match_snapshot,
    )

    if (type(receipt) is not TypedBacktestReceipt or type(receipt.last_sequence) is not int
            or receipt.last_sequence <= 0 or type(run_id) is not str or not run_id
            or type(policy) is not NativeChannelManagementPolicy
            or getattr(client, 'native_channel_management_policy', None) != policy):
        raise ValueError('Channel held confirmation requires exact declared receipt and client')
    policy.__post_init__()
    sequence = receipt.last_sequence
    prefix = load_verified_v4_prefix(client, run_id, first_price_source=first_price_source)
    if (type(prefix) is not V4CommittedPrefix or prefix.status != 'running'
            or prefix.run_id != run_id or prefix.last_sequence != sequence
            or not prefix.batch_ids or prefix.last_batch_id != prefix.batch_ids[-1]
            or prefix.last_batch_id != receipt.last_batch_id or prefix.source_cursor != receipt.source_cursor):
        raise ValueError('Channel held confirmation differs from independently committed frontier')
    heads = (manager_keeper.read_head(run_id=run_id), broker_keeper.read_head(run_id=run_id))
    if (type(heads[0]) is not ManagerSnapshotHead or type(heads[1]) is not BrokerMatchHead
            or any(h.run_id != run_id or h.checkpoint_sequence != sequence
                   or h.journal_batch_id != receipt.last_batch_id for h in heads)):
        raise ValueError('Channel held confirmation differs from selected checkpoint heads')
    rows, state, requests = load_attested_native_channel_manager_snapshot(client, manager_keeper,
        run_id=run_id, checkpoint_sequence=sequence, policy=policy, contexts=contexts,
        first_price_source=first_price_source)
    broker = load_attested_broker_match_snapshot(client, broker_keeper, run_id=run_id,
        checkpoint_sequence=sequence, first_price_source=first_price_source)
    root = rows.snapshot
    cursor = load_latest_backtest_cursor(client, prefix)
    if (root['content_hash'] != heads[0].snapshot_hash
            or broker.snapshot['content_hash'] != heads[1].snapshot_hash
            or root['run_id'] != run_id or broker.snapshot['run_id'] != run_id
            or root['checkpoint_sequence'] != sequence or broker.snapshot['checkpoint_sequence'] != sequence
            or root['boundary_ms'] != state.boundary_ms
            or broker.snapshot['boundary_ms'] != state.boundary_ms
            or root['session_date'] != broker.snapshot['session_date']
            or not isinstance(cursor, dict) or cursor.get('run_id') != run_id
            or cursor.get('event_sequence') != sequence or cursor.get('batch_id') != receipt.last_batch_id
            or cursor.get('boundary_ms') != state.boundary_ms
            or cursor.get('session_date') != root['session_date']):
        raise ValueError('Channel held confirmation crosses native roots or decision cursor')
    result = _verify_native_channel_checkpoint_holdings(client, prefix, rows, state, requests,
        broker, policy=policy, contexts=contexts, first_price_source=first_price_source)
    if (manager_keeper.read_head(run_id=run_id), broker_keeper.read_head(run_id=run_id)) != heads:
        raise ValueError('Channel held checkpoint heads changed before reference selection')
    if load_verified_v4_prefix(client, run_id, first_price_source=first_price_source) != prefix:
        raise ValueError('Channel committed frontier changed before reference selection')
    return tuple(result)


def load_historical_native_channel_held_checkpoint(client, prefix, reference, *,
        policy, contexts, first_price_source):
    """Reprove a saved exit checkpoint against its exact committed predecessor.

    This verifies historical roots and source/OMS evidence, without claiming
    that an old root is the current Keeper head. The caller must independently
    verify the preceding journal prefix and the exit's sealed batch membership.
    It grants no current trading, INSERT or recovery-drain permission.
    """
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.native_channel_manager_reader import load_unattested_native_channel_manager_rows
    from src.trading_runtime.strategy_one_management_snapshot import (
        restore_manager_snapshot, attach_committed_momentum_sources,
    )
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        load_unattested_broker_match_snapshot, verify_broker_match_snapshot,
    )
    from .backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot
    if (type(prefix) is not V4CommittedPrefix or prefix.status != 'running'
            or not prefix.batch_ids or prefix.last_batch_id != prefix.batch_ids[-1]
            or type(reference) is not NativeChannelExitCheckpointReference
            or type(policy) is not NativeChannelManagementPolicy
            or policy != getattr(client,'native_channel_management_policy',None)
            or prefix.last_sequence != reference.source_manager_checkpoint_sequence):
        raise ValueError('Historical channel confirmation requires exact declared predecessor and reference')
    reference.__post_init__()
    policy.__post_init__()
    run_id, sequence = prefix.run_id, prefix.last_sequence
    rows=load_unattested_native_channel_manager_rows(client,run_id=run_id,
        checkpoint_sequence=sequence,policy=policy)
    state=attach_committed_momentum_sources(client,prefix,restore_manager_snapshot(rows.inherited),
        first_price_source=first_price_source)
    requests=reprove_native_channel_manager_snapshot(rows,policy,contexts)
    broker=verify_broker_match_snapshot(load_unattested_broker_match_snapshot(client,
        run_id=run_id,checkpoint_sequence=sequence))
    root, broker_root = rows.snapshot, broker.snapshot
    cursor=load_latest_backtest_cursor(client,prefix)
    if (root['snapshot_id'] != reference.source_manager_snapshot_id
            or root['content_hash'] != reference.source_manager_snapshot_hash
            or broker_root['snapshot_id'] != reference.source_broker_snapshot_id
            or broker_root['content_hash'] != reference.source_broker_snapshot_hash
            or root['run_id'] != run_id or broker_root['run_id'] != run_id
            or root['checkpoint_sequence'] != sequence or broker_root['checkpoint_sequence'] != sequence
            or root['boundary_ms'] != state.boundary_ms or broker_root['boundary_ms'] != state.boundary_ms
            or root['session_date'] != broker_root['session_date']
            or not isinstance(cursor,dict) or cursor.get('run_id') != run_id
            or cursor.get('event_sequence') != sequence or cursor.get('batch_id') != prefix.last_batch_id
            or cursor.get('boundary_ms') != state.boundary_ms
            or cursor.get('session_date') != root['session_date']):
        raise ValueError('Historical channel roots differ from exact saved reference or committed cursor')
    return _verify_native_channel_checkpoint_holdings(client,prefix,rows,state,requests,broker,
        policy=policy,contexts=contexts,first_price_source=first_price_source)


def _verify_native_channel_checkpoint_holdings(client, prefix, rows, state, requests,
        broker, *, policy, contexts, first_price_source):
    """Shared historical and current native ownership checks; no order authority."""
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    from src.trading_runtime.arte_oms_projection import load_recovered_strategy_one_oms_lineage
    from src.trading_runtime.native_held_oms_checkpoint import verify_native_held_oms_position
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID
    run_id, sequence, root = prefix.run_id, prefix.last_sequence, rows.snapshot
    context = load_typed_run_context(client, run_id)
    accounts, number = context.get('account_ids'), context.get('strategy_revision')
    if (context.get('run_id') != run_id or context.get('mode') != 'backtest'
            or context.get('strategy_id') != STRATEGY_ID or type(number) is not int
            or context.get('evaluation_interval_ms') != 100
            or context.get('session_date') != root['session_date']
            or type(accounts) is not tuple or not 0 < len(accounts) <= 65_535
            or any(type(a) is not str or not a for a in accounts) or len(set(accounts)) != len(accounts)
            or {a['account_id'] for a in broker.accounts} != set(accounts)
            or numbered_fixed_strategy(number).native_channel_management_policy != policy):
        raise ValueError('Channel held confirmation differs from pinned strategy and account context')
    lineage = load_recovered_strategy_one_oms_lineage(client, prefix,
        allowed_accounts=frozenset(accounts), strategy_number=number, first_price_source=first_price_source)
    submitted = dict(state.submitted)
    if len(submitted) != len(state.submitted):
        raise ValueError('Channel held confirmation has ambiguous original entry ownership')
    reference = NativeChannelExitCheckpointReference(root['snapshot_id'], sequence,
        root['content_hash'], broker.snapshot['snapshot_id'], broker.snapshot['content_hash'])
    result, seen, acquisitions = [], set(), []
    headers = sorted(rows.pending, key=lambda row: row['record_id'])
    for header, request in zip(headers, requests, strict=True):
        financial, clock = request.financial, request.acquisition
        key = financial.account_id, financial.assignment_id, financial.ticker
        entry = submitted.get(key)
        evidence = contexts[header['record_id']]
        if (key in seen or entry is None or entry.strategy_number != number
                or evidence['original_entry'] != entry
                or (entry.account_id, entry.assignment_id, entry.ticker) != key
                or financial.account_id not in accounts or financial.pending_exit
                or clock.session_date.isoformat() != root['session_date']
                or request.decision['decision_day_ms'] != state.boundary_ms + SESSION_OPEN_OFFSET_MS):
            raise ValueError('Channel held confirmation repeats or borrows native position ownership')
        seen.add(key)
        conid, quantity = verify_native_held_oms_position(prefix, sequence, broker, lineage,
            accounts=accounts, strategy_number=number, financial=financial,
            source_entry_intent_id=clock.source_entry_intent_id,
            expected_exit_quantity=financial.position_quantity)
        if conid != evidence['expected_conid']:
            raise ValueError('Channel source fills differ from the native held instrument')
        original = tuple(item for item in lineage if item.source_intent.intent.intent_id == clock.source_entry_intent_id
                         and item.source_intent.account_id == financial.account_id)
        if len(original) != 1 or original[0].state.group['group_id'] != clock.source_group_id:
            raise ValueError('Channel acquisition group differs from committed original OMS entry')
        acquisitions.append((request, original[0], evidence['group'], conid))
        result.append(NativeChannelHeldCheckpoint(request, reference, conid, quantity))
    if acquisitions:
        from .backtest_v4_execution_restore import execution_order_bindings_from_oms, load_v4_broker_executions
        from src.trading_runtime.ibkr_schema import Execution
        requests_by_coid, coid_by_broker_id = execution_order_bindings_from_oms(lineage)
        payloads = load_v4_broker_executions(client, prefix, requests_by_coid=requests_by_coid,
            coid_by_broker_id=coid_by_broker_id, next_execution_id=broker.snapshot['next_execution_id'])
        as_of = market_day_boundary(root['session_date'], state.boundary_ms)
        executions = []
        for payload in payloads:
            values = dict(payload)
            values['trade_time'] = datetime.fromisoformat(values['trade_time'])
            if values['trade_time'].tzinfo is None or values['trade_time'].astimezone(timezone.utc) > as_of:
                raise ValueError('Channel committed execution exceeds its checkpoint clock')
            executions.append(Execution(**values))
        executions = tuple(executions)
        for request, original, group, conid in acquisitions:
            _verify_committed_channel_acquisition(request, original, group, executions, as_of, conid)
    return tuple(result)


def _verify_committed_channel_acquisition(request, original, group, executions, as_of, conid):
    """Reject supplied OMS/fill clocks that differ from committed native facts."""
    from src.trading_runtime.arte_journal_reader import _journal_instant
    from src.trading_runtime.order_management import OrderGroupSnapshot
    from src.trading_runtime.native_acquisition_clock import require_acquisition_clock_matches_executions
    native = original.state.group
    bindings = original.state.broker_bindings
    if (type(group) is not OrderGroupSnapshot or group.group_id != native['group_id']
            or group.intent_id != original.source_intent.intent.intent_id
            or group.account_id != native['account_id']
            or group.assignment_id != original.admission_reservation['assignment_id']
            or group.ticker != original.source_intent.intent.ticker
            or group.action != original.source_intent.intent.action
            or group.state.value != native['state']
            or group.submitted_at != _journal_instant(native['submitted_at'])
            or group.updated_at != _journal_instant(native['updated_at'])
            or Decimal(str(group.filled_quantity)) != Decimal(str(native['filled_quantity']))
            or Decimal(str(group.remaining_quantity)) != Decimal(str(native['remaining_quantity']))
            or set(group.broker_order_ids) != {b['broker_order_id'] for b in bindings}):
        raise ValueError('Channel supplied acquisition group differs from committed OMS state')
    require_acquisition_clock_matches_executions(request.acquisition, group, executions,
        session_date=request.acquisition.session_date, as_of=as_of,
        source_entry_intent_id=request.acquisition.source_entry_intent_id, expected_conid=conid)
