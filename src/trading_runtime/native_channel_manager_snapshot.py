"""Uninstalled manager-root extension sealing all normalized pending children."""
from dataclasses import dataclass
from collections import Counter
from decimal import Decimal
from hashlib import sha256

from .arte_journal_writer import typed_contract_row, verify_typed_contract_row
from .arte_native_channel_pending_schema import PENDING, PENDING_SOURCE_ROW, PENDING_EXECUTION_ROW
from .original_risk_pending_snapshot import selected_parent_contract
from .native_channel_manager_snapshot_schema import parent_contract
from .strategy_one_management_snapshot import OriginalRiskManagerSnapshotRows, restore_manager_snapshot
from .journal_contract import canonical_json


@dataclass(frozen=True, slots=True)
class NativeChannelManagerSnapshotRows:
    snapshot: dict
    inherited: OriginalRiskManagerSnapshotRows
    pending: tuple
    sources: tuple
    executions: tuple


def seal_native_channel_manager_rows(inherited, pending, sources, executions, *,
        max_pending_requests, max_rows_per_family):
    """Verify complete child relationships before deriving a new root hash.

    This does not register schemas or publish a Keeper head. The future writer
    must insert all inherited and new children before selecting this root.
    Empty pending inventory remains a complete, explicitly counted snapshot.
    """
    if (type(inherited) is not OriginalRiskManagerSnapshotRows
            or type(max_pending_requests) is not int or not 0 < max_pending_requests < 2**32
            or type(max_rows_per_family) is not int or not 0 < max_rows_per_family < 2**32
            or any(type(rows) is not tuple for rows in (pending, sources, executions))
            or len(pending) > max_pending_requests
            or any(len(rows) > max_rows_per_family for rows in (pending, sources, executions))):
        raise ValueError('Exact inherited manager and bounded complete pending families required')
    state = restore_manager_snapshot(inherited)
    root = verify_typed_contract_row(selected_parent_contract(), inherited.snapshot)
    families = tuple(tuple(sorted((verify_typed_contract_row(contract, row) for row in rows),
        key=lambda row: row['record_id'])) for contract, rows in (
            (PENDING, pending), (PENDING_SOURCE_ROW, sources), (PENDING_EXECUTION_ROW, executions)))
    headers, source_rows, execution_rows = families
    if any(len({row['record_id'] for row in rows}) != len(rows) for rows in families):
        raise ValueError('Native channel snapshot repeats a child identity')
    by_id = {row['record_id']: row for row in headers}
    owners = set()
    entries = dict(state.submitted)
    held = dict(state.positions)
    for row in headers:
        owner = row['account_id'], row['assignment_id'], row['ticker']
        entry = entries.get(owner)
        if (owner in owners or owner not in held or entry is None
                or row['parent_record_id'] != root['snapshot_id']
                or row['manager_snapshot_id'] != root['snapshot_id']
                or row['run_id'] != root['run_id']
                or row['event_month'] != root['snapshot_month']
                or row['manager_checkpoint_sequence'] != root['checkpoint_sequence']
                or row['manager_boundary_ms'] != root['boundary_ms']
                or row['decision_day_ms'] != root['boundary_ms'] + 14_400_000
                or row['session_date'] != root['session_date']
                or row['strategy_number'] != entry.strategy_number
                or Decimal(row['position_quantity']) <= 0
                or Decimal(row['original_ask']) != Decimal(str(entry.reference_ask))
                or Decimal(row['initial_stop']) != Decimal(str(entry.initial_stop))):
            raise ValueError('Pending channel child differs from inherited manager ownership or clock')
        owners.add(owner)
    for rows in (source_rows, execution_rows):
        for row in rows:
            header = by_id.get(row['decision_record_id'])
            if header is None or any(row[key] != header[key] for key in (
                    'run_id', 'parent_record_id', 'event_month', 'batch_id', 'ticker')):
                raise ValueError('Pending lineage child is orphaned or belongs to another manager')
    source_counts = Counter(row['decision_record_id'] for row in source_rows)
    execution_counts = Counter(row['decision_record_id'] for row in execution_rows)
    for header in headers:
        if (source_counts[header['record_id']] != header['source_row_count']
                or execution_counts[header['record_id']] != header['execution_row_count']):
            raise ValueError('Pending lineage child counts differ from decision header')
    values = {k: v for k, v in root.items() if k != 'content_hash'}
    for prefix, rows in zip(('pending', 'source', 'execution'), families):
        values[f'native_channel_{prefix}_count'] = len(rows)
        values[f'native_channel_{prefix}_hash'] = sha256(
            canonical_json([row['content_hash'] for row in rows]).encode()).hexdigest()
    return NativeChannelManagerSnapshotRows(typed_contract_row(parent_contract(), values), inherited,
        pending, sources, executions)
