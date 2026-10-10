"""Full pending-evidence recovery; this grants no checkpoint or order authority."""
from collections.abc import Mapping
from collections import defaultdict
from datetime import date

from .backtest_native_channel_pending_recovery import reprove_native_channel_pending_rows
from src.trading_runtime.native_channel_manager_snapshot import verify_native_channel_manager_rows


def reprove_native_channel_manager_snapshot(rows, policy, contexts):
    """Recompute every pending request under independently supplied source facts.

    The caller still must attest the selected Keeper head, committed journal
    prefix, original entry source and broker checkpoint before order admission.
    Contexts are indexed by decision identity, never inferred from saved prices.
    """
    verified = verify_native_channel_manager_rows(rows, policy)
    if not isinstance(contexts, Mapping) or set(contexts) != {r['record_id'] for r in verified.pending}:
        raise ValueError('Independent evidence must cover the exact pending inventory')
    sources, fills = defaultdict(list), defaultdict(list)
    for row in verified.sources:
        sources[row['decision_record_id']].append(row)
    for row in verified.executions:
        fills[row['decision_record_id']].append(row)
    root = verified.snapshot
    requests = []
    for header in sorted(verified.pending, key=lambda row: row['record_id']):
        context = contexts[header['record_id']]
        if not isinstance(context, Mapping):
            raise ValueError('Exact independently supplied pending evidence required')
        context = dict(context)
        if (context.get('policy') != policy.exit_policy
                or context.get('session_date') != date.fromisoformat(root['session_date'])
                or context.get('run_id') != root['run_id']
                or context.get('parent_record_id') != root['snapshot_id']
                or context.get('max_rows_per_family') != policy.max_rows_per_family
                or any(key in context for key in ('manager_snapshot_id',
                    'manager_checkpoint_sequence', 'manager_boundary_ms', 'stored_utc', 'checkpoint'))):
            raise ValueError('Independent pending context differs from declared manager root or policy')
        requests.append(reprove_native_channel_pending_rows(header,
            tuple(sources[header['record_id']]), tuple(fills[header['record_id']]),
            manager_snapshot_id=root['snapshot_id'],
            manager_checkpoint_sequence=root['checkpoint_sequence'],
            manager_boundary_ms=root['boundary_ms'], **context))
    return tuple(requests)
