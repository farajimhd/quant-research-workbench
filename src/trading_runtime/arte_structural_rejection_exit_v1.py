"""Own normalized exit pointers; no writer admission or execution authority.

The referenced manager family already preserves the complete lossless witness.
This companion joins its account/assignment/ticker key to the exact factory
intent and all four same-cursor financial roots. Cold native replay and writer
admission must independently verify those persisted families before use.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from uuid import UUID, NAMESPACE_URL, uuid5
from weakref import WeakKeyDictionary

from .arte_journal_schema import TableContract
from .profit_armed_structural_rejection_confirmation import (
    require_structural_rejection_confirmation, structural_rejection_confirmation_reference)
from .profit_armed_structural_rejection_exit import structural_rejection_exit_intent


EXIT = TableContract('trading_structural_rejection_exit_v1', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('intent_id', 'UUID'), ('source_entry_intent_id', 'UUID'),
    ('account_id', 'String'), ('assignment_id', 'String'), ('ticker', 'String'),
    ('boundary_ms', 'UInt32'), ('position_quantity', 'Float64'),
    ('reference_bid_int', 'UInt64'), ('intent_hash', 'FixedString(64)'),
    ('source_manager_checkpoint_sequence', 'UInt64'),
    ('source_manager_snapshot_id', 'UUID'), ('source_manager_snapshot_hash', 'FixedString(64)'),
    ('source_broker_snapshot_id', 'UUID'), ('source_broker_snapshot_hash', 'FixedString(64)'),
    ('source_oms_snapshot_id', 'UUID'), ('source_oms_snapshot_hash', 'FixedString(64)'),
    ('source_portfolio_state_hash', 'FixedString(64)'), ('content_hash', 'FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, parent_record_id, record_id')
TABLES = (EXIT,)
_ISSUED = WeakKeyDictionary()


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class PreparedStructuralRejectionExit:
    row: object


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError('Structural rejection exit requires canonical nonzero UUID')
    return value


def _intent_hash(intent):
    payload = intent.payload()
    payload['event_time'] = intent.event_time.isoformat()
    return sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


def prepare_structural_rejection_exit(confirmation, intent, *, parent_record_id, batch_id):
    """Freeze exact issued pending evidence; hashes alone cannot issue this row."""
    require_structural_rejection_confirmation(confirmation)
    expected = structural_rejection_exit_intent(confirmation)
    if type(intent) is not type(expected) or intent != expected:
        raise ValueError('Structural rejection exit differs from exact factory intent')
    _uuid(parent_record_id)
    _uuid(batch_id)
    request = confirmation.request
    # The actual issued owner supplies the declared number; no caller-selected
    # strategy number, inherited exit label, or release-name dispatch.
    from .profit_armed_structural_rejection_confirmation import _ISSUED as confirmations
    owner = confirmations[confirmation][0]
    number = owner.manager.contract.strategy_number
    if type(number) is not int or not 0 < number < 2**32:
        raise ValueError('Structural rejection exit lacks declared strategy number')
    row = dict(record_id=str(uuid5(NAMESPACE_URL,
        f'{confirmation.run_id}:{parent_record_id}:structural-rejection-exit-v1')),
        parent_record_id=parent_record_id, run_id=confirmation.run_id,
        event_month=intent.event_time.strftime('%Y-%m-01'), batch_id=batch_id,
        strategy_number=number, intent_id=_uuid(intent.intent_id),
        source_entry_intent_id=_uuid(request.source_entry_intent_id),
        account_id=request.financial.account_id, assignment_id=request.financial.assignment_id,
        ticker=request.financial.ticker, boundary_ms=confirmation.boundary_ms,
        position_quantity=float(request.financial.position_quantity),
        reference_bid_int=request.witness.quote.bid_int, intent_hash=_intent_hash(intent),
        **structural_rejection_confirmation_reference(confirmation))
    row['content_hash'] = sha256(json.dumps(row, sort_keys=True, separators=(',', ':'),
                                           allow_nan=False).encode()).hexdigest()
    if set(row) != {name for name, _ in EXIT.columns}:
        raise ValueError('Structural rejection exit differs from complete own schema')
    from src.backend.backtest_management_structural_guard import capture_management_structural_guard
    result = PreparedStructuralRejectionExit(MappingProxyType(row))
    _ISSUED[result] = (confirmation, intent, result.row, capture_management_structural_guard(result.row),
                       capture_management_structural_guard(intent))
    return result


def require_prepared_structural_rejection_exit(prepared):
    """Fresh descendant checks; this is not a persisted native commit seal."""
    if type(prepared) is not PreparedStructuralRejectionExit or prepared not in _ISSUED:
        raise ValueError('Unissued structural rejection exit evidence')
    confirmation, intent, row, guard, intent_guard = _ISSUED[prepared]
    require_structural_rejection_confirmation(confirmation)
    from src.backend.backtest_management_structural_guard import require_management_structural_guard
    require_management_structural_guard(guard, row)
    require_management_structural_guard(intent_guard, intent)
    if (prepared.row is not row
            or intent != structural_rejection_exit_intent(confirmation)):
        raise ValueError('Structural rejection exit evidence or intent changed')
    return prepared
