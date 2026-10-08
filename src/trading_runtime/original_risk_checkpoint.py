"""Select immutable exit references only after the same native checkpoint fence."""
from types import MappingProxyType
from dataclasses import dataclass, asdict, replace


@dataclass(frozen=True, slots=True)
class OriginalRiskCheckpointReference:
    source_manager_snapshot_id: str
    source_manager_checkpoint_sequence: int
    source_manager_snapshot_hash: str
    source_broker_snapshot_id: str
    source_broker_snapshot_hash: str

    def __post_init__(self):
        from .arte_liquidity_fade_failure_v4 import validate_liquidity_checkpoint_reference
        validate_liquidity_checkpoint_reference(asdict(self))


@dataclass(frozen=True, slots=True)
class OriginalRiskCheckpointRequest:
    diagnostic: object
    financial: object
    source_entry_intent_id: str

    @property
    def witness(self):
        return self.diagnostic.current

    def __post_init__(self):
        from uuid import UUID
        from .confirmed_original_risk_failure import OriginalRiskDecisionDiagnostic
        from .strategy_one_stateful import StrategyOneFinancialView
        if (type(self.diagnostic) is not OriginalRiskDecisionDiagnostic
                or self.diagnostic.checkpoint is not None
                or type(self.financial) is not StrategyOneFinancialView
                or type(self.source_entry_intent_id) is not str
                or str(UUID(self.source_entry_intent_id)) != self.source_entry_intent_id
                or UUID(self.source_entry_intent_id).int == 0):
            raise ValueError('Original-risk pending request requires exact unfenced decision and entry')


def validate_original_risk_state(witness,state,financial):
    """Bind first-held and original risk to actual persisted manager state."""
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState,OriginalRiskManagementState
    from .strategy_one_stateful import StrategyOneEntryProposal,StrategyOneFinancialView
    from .strategy_one_position import ProtectionState
    from .numbered_fixed_strategy import numbered_fixed_strategy
    if (type(state) not in (StrategyOneManagementState,OriginalRiskManagementState)
            or type(financial) is not StrategyOneFinancialView
            or state.boundary_ms != witness.boundary_ms or financial.position_quantity <= 0
            or financial.pending_exit):
        raise ValueError('Original-risk checkpoint lacks exact decision held state')
    key=financial.account_id,financial.assignment_id,financial.ticker
    selected={}
    for family in ('submitted','positions','first_held_boundaries'):
        rows=getattr(state,family)
        if len({k for k,v in rows}) != len(rows) or key not in dict(rows):
            raise ValueError('Original-risk checkpoint lacks unique original position identity')
        selected[family]=dict(rows)[key]
    source=selected['submitted']
    if (type(source) is not StrategyOneEntryProposal
            or numbered_fixed_strategy(source.strategy_number).confirmed_original_risk_policy is None
            or (source.account_id,source.assignment_id,source.ticker) != key
            or source.reference_ask != witness.reference_ask or source.initial_stop != witness.initial_stop
            or not source.boundary_ms < witness.first_held_boundary_ms
            or type(selected['first_held_boundaries']) is not int
            or selected['first_held_boundaries'] != witness.first_held_boundary_ms
            or type(selected['positions']) is not ProtectionState
            or selected['positions'].boundary_ms > state.boundary_ms):
        raise ValueError('Original-risk checkpoint differs from original entry, anchors or first-held')
    return source


def confirm_original_risk_checkpoint_sources(client, manager_keeper, broker_keeper, requests,
                                             receipt, *, run_id, first_price_source):
    """Bind completed requests to both attested Keeper-selected snapshot roots.

    This cold confirmation submits no orders and grants no writer admission.
    Full entry, broker/OMS and market verification remains mandatory at native
    publication. No fill time or current price substitutes for first-held state.
    """
    from .original_risk_checkpoint import OriginalRiskCheckpointRequest
    from src.backend.backtest_typed_publisher import TypedBacktestReceipt
    from .strategy_one_management_snapshot import (
        ManagerSnapshotHead, load_attested_manager_snapshot, load_unattested_manager_snapshot_rows,
    )
    from .strategy_one_broker_match_snapshot import BrokerMatchHead, load_attested_broker_match_snapshot
    from .original_risk_checkpoint import validate_original_risk_state
    from .arte_liquidity_fade_failure_v4 import validate_liquidity_checkpoint_reference
    if (type(requests) is not tuple or not 0 < len(requests) <= 65_536
            or any(type(request) is not OriginalRiskCheckpointRequest for request in requests)
            or type(receipt) is not TypedBacktestReceipt or receipt.last_sequence <= 0
            or type(run_id) is not str or not run_id):
        raise ValueError('Original-risk confirmation requires exact pending decisions and native receipt')
    manager_head, broker_head = manager_keeper.read_head(run_id=run_id), broker_keeper.read_head(run_id=run_id)
    if (type(manager_head) is not ManagerSnapshotHead or type(broker_head) is not BrokerMatchHead
            or any(head.run_id != run_id or head.checkpoint_sequence != receipt.last_sequence
                   or head.journal_batch_id != receipt.last_batch_id for head in (manager_head, broker_head))):
        raise ValueError('Original-risk confirmation receipt differs from selected native snapshot heads')
    from .selected_checkpoint_products import current_checkpoint,checkpoint_root
    image=current_checkpoint(client,manager_keeper,run_id=run_id,
        sequence=receipt.last_sequence,first_price_source=first_price_source)
    state=(image.inherited if image is not None else load_attested_manager_snapshot(client, manager_keeper, run_id=run_id,
        checkpoint_sequence=receipt.last_sequence, first_price_source=first_price_source))
    if image is not None:
        from .strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
        broker=load_unattested_broker_match_snapshot(client,run_id=run_id,checkpoint_sequence=receipt.last_sequence)
        manager_root=checkpoint_root(client,source=image.source,sequence=receipt.last_sequence)
    else:
        broker = load_attested_broker_match_snapshot(client, broker_keeper, run_id=run_id,
            checkpoint_sequence=receipt.last_sequence, first_price_source=first_price_source)
        manager_root = load_unattested_manager_snapshot_rows(client, run_id=run_id, checkpoint_sequence=receipt.last_sequence).snapshot
    from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
    if type(state) is not OriginalRiskManagementState or state.original_risk_requests != requests:
        raise ValueError('Original-risk pending decisions differ from the actual selected manager root')
    if (manager_root['content_hash'] != manager_head.snapshot_hash
            or broker.snapshot['content_hash'] != broker_head.snapshot_hash
            or manager_root['boundary_ms'] != state.boundary_ms
            or broker.snapshot['boundary_ms'] != state.boundary_ms
            or manager_root['session_date'] != broker.snapshot['session_date']):
        raise ValueError('Original-risk confirmation snapshot roots differ from their attested decision clock')
    refs = dict(source_manager_snapshot_id=manager_root['snapshot_id'],
        source_manager_checkpoint_sequence=receipt.last_sequence,
        source_manager_snapshot_hash=manager_head.snapshot_hash,
        source_broker_snapshot_id=broker.snapshot['snapshot_id'], source_broker_snapshot_hash=broker_head.snapshot_hash)
    validate_liquidity_checkpoint_reference(refs)
    identities, result = set(), []
    for request in requests:
        financial = request.financial
        key = financial.account_id, financial.assignment_id, financial.ticker
        if key in identities or request.witness.boundary_ms != state.boundary_ms:
            raise ValueError('Original-risk confirmation repeats a position or crosses its native boundary')
        identities.add(key)
        source = validate_original_risk_state(request.witness, state, financial)
        from src.backend.backtest_strategy_certified_price_break import (
            CertifiedPriceReadbackAuthority,certified_price_entry_intent,
        )
        from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
        from .numbered_fixed_strategy import declared_fixed_rule
        from datetime import date
        if type(first_price_source) is not CertifiedPriceReadbackAuthority or first_price_source.run_id != run_id:
            raise ValueError('Original-risk checkpoint lacks exact certified entry authority')
        day=date.fromisoformat(manager_root['session_date'])
        entry=(certified_episode_entry_intent(first_price_source,source,session_date=day)
            if declared_fixed_rule(source.strategy_number,'strategy-thirty-seven-confirmed-episode-activity-veto-v1')
            else certified_price_entry_intent(first_price_source.plan,source,session_date=day))
        if (entry.intent_id != request.source_entry_intent_id
                or request.diagnostic.newest.session_date != manager_root['session_date']
                or request.diagnostic.newest.ticker != financial.ticker):
            raise ValueError('Original-risk checkpoint crosses original entry or active session')
        result.append(replace(request.diagnostic,checkpoint=OriginalRiskCheckpointReference(**refs)))
    if manager_keeper.read_head(run_id=run_id) != manager_head or broker_keeper.read_head(run_id=run_id) != broker_head:
        raise ValueError('Original-risk checkpoint heads changed before reference selection')
    return tuple(result)


def load_original_risk_checkpoint(client, prefix, failure, parent, event, diagnostic,
                                  *, first_price_source):
    """Verify actual pre-exit manager and broker state against a committed prefix."""
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_projection import load_latest_backtest_cursor
    from .strategy_one_management_snapshot import (
        load_unattested_manager_snapshot_rows, restore_manager_snapshot,
        attach_committed_momentum_sources,
    )
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_engine import AssignmentStatus, StrategyPermissions
    from .arte_followthrough_failure_v4 import _source_entry
    from .strategy_liquidity_fade_financial_checkpoint import load_liquidity_fade_financial_checkpoint
    reference = diagnostic.checkpoint
    if type(reference) is not OriginalRiskCheckpointReference:
        raise ValueError('Original-risk exit lacks durable checkpoint authority')
    sequence = reference.source_manager_checkpoint_sequence
    if (type(prefix) is not V4CommittedPrefix or prefix.status != 'running'
            or prefix.run_id != failure['run_id'] or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]
            or type(event['sequence']) is not int
            or not 0 < sequence <= prefix.last_sequence < event['sequence']):
        raise ValueError('Original-risk checkpoint lacks independently verified preceding prefix')
    cursor = load_latest_backtest_cursor(client, replace(prefix, last_sequence=sequence))
    if (not isinstance(cursor, dict) or cursor.get('event_sequence') != sequence
            or cursor.get('run_id') != prefix.run_id
            or str(cursor.get('batch_id')) not in prefix.batch_ids
            or cursor.get('boundary_ms') != diagnostic.current.boundary_ms
            or cursor.get('session_date') != diagnostic.newest.session_date):
        raise ValueError('Original-risk checkpoint differs from exact decision cursor')
    from .selected_checkpoint_products import source_for_client,load_historical_checkpoint,checkpoint_root
    selected_source=source_for_client(client)
    if selected_source is not None:
        image=load_historical_checkpoint(client,prefix,source=selected_source,sequence=sequence)
        root=checkpoint_root(client,source=selected_source,sequence=sequence)
        if (root['snapshot_id']!=reference.source_manager_snapshot_id
                or root['content_hash']!=reference.source_manager_snapshot_hash
                or root['boundary_ms']!=cursor['boundary_ms']
                or root['session_date']!=cursor['session_date']):
            raise ValueError('Original-risk selected checkpoint differs from immutable reference')
        state=image.inherited
    else:
        rows = load_unattested_manager_snapshot_rows(client, run_id=prefix.run_id,
                                                    checkpoint_sequence=sequence)
        root = rows.snapshot
        if (root.get('snapshot_id') != reference.source_manager_snapshot_id
                or root.get('content_hash') != reference.source_manager_snapshot_hash
                or root.get('run_id') != prefix.run_id or root.get('checkpoint_sequence') != sequence
                or root.get('boundary_ms') != cursor['boundary_ms']
                or root.get('session_date') != cursor['session_date']):
            raise ValueError('Original-risk manager root differs from immutable checkpoint reference')
        state = attach_committed_momentum_sources(client, prefix, restore_manager_snapshot(rows),
                                                 first_price_source=first_price_source)
    financial = StrategyOneFinancialView(failure['assignment_id'], event['account_id'], parent['ticker'],
        AssignmentStatus.MANAGING, StrategyPermissions(), float(parent['quantity']), False, False, False, 1)
    linked = {**failure, **asdict(reference)}
    load_liquidity_fade_financial_checkpoint(client, prefix, linked, parent, event, financial,
        first_price_source=first_price_source, original_risk_diagnostic=diagnostic)
    from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
    candidates=tuple(request for request in getattr(state,'original_risk_requests',())
        if (request.financial.account_id,request.financial.assignment_id,request.financial.ticker)
            ==(financial.account_id,financial.assignment_id,financial.ticker))
    if (type(state) is not OriginalRiskManagementState or len(candidates)!=1
            or replace(diagnostic,checkpoint=None)!=candidates[0].diagnostic
            or candidates[0].source_entry_intent_id!=str(failure['source_entry_intent_id'])
            or candidates[0].financial.position_quantity!=financial.position_quantity):
        raise ValueError('Original-risk exit differs from its durable pending checkpoint decision')
    source = validate_original_risk_state(diagnostic.current, state, financial)
    entry, source_event, child = _source_entry(client, prefix.run_id,
        str(failure['source_entry_intent_id']), prior_batch_id=str(cursor['batch_id']),
        exit_batch_id=str(failure['batch_id']), verified_prefix=prefix, first_price_source=first_price_source)
    if (entry['action'] != 'enter_long' or entry['reason'] != 'strategy_one_entry'
            or str(entry['batch_id']) not in prefix.batch_ids
            or entry['ticker'] != financial.ticker or source_event['account_id'] != financial.account_id
            or child['strategy_number'] != failure['strategy_number']
            or source.strategy_number != failure['strategy_number']
            or child['assignment_id'] != financial.assignment_id or child['boundary_ms'] != source.boundary_ms
            or float(entry['reference_price']) != diagnostic.current.reference_ask
            or float(entry['invalidation_price']) != diagnostic.current.initial_stop
            or source_event['sequence'] >= sequence):
        raise ValueError('Original-risk checkpoint differs from committed original entry')
    return state
