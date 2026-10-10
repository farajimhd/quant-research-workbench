"""Complete native pending capture on the checkpoint worker, never order authority."""
from collections.abc import Mapping

from .backtest_native_channel_exit_request import NativeChannelExitRequest
from .backtest_native_channel_pending_projection import project_native_channel_pending
from .backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot
from src.trading_runtime.native_channel_management_policy import NativeChannelManagementPolicy
from src.trading_runtime.native_channel_manager_snapshot import seal_native_channel_manager_rows
from src.trading_runtime.strategy_one_management_snapshot import OriginalRiskManagerSnapshotRows


def capture_native_channel_manager_rows(inherited,requests,contexts,*,policy):
    """Seal all pending requests and independently reproduce every saved child.

    Request selection is the caller's causal rule evaluation. This checkpoint
    operation neither filters candidates nor admits orders. The publisher must
    still attest the journal, source entries, broker and Keeper frontier.
    """
    if (type(inherited) is not OriginalRiskManagerSnapshotRows
            or type(policy) is not NativeChannelManagementPolicy
            or not isinstance(requests,Mapping) or not isinstance(contexts,Mapping)
            or set(requests)!=set(contexts) or len(requests)>policy.max_pending_requests
            or any(type(key) is not str for key in requests)):
        raise ValueError('Native manager capture requires complete bounded pending inputs')
    policy.__post_init__()
    root=inherited.snapshot
    pending,sources,executions=[],[],[]
    for key in sorted(requests):
        request=requests[key]
        if type(request) is not NativeChannelExitRequest or not isinstance(contexts[key],Mapping):
            raise ValueError('Native manager capture requires exact requests and source evidence')
        context=dict(contexts[key])
        if (context.get('decision_record_id')!=key or context.get('policy')!=policy.exit_policy
                or request.policy!=policy.exit_policy
                or context.get('source_entry_intent_id')!=request.acquisition.source_entry_intent_id
                or context.get('current_financial')!=request.financial):
            raise ValueError('Native manager request differs from declared decision or policy')
        # Source-only inputs are consumed by the independent reproof below.
        for name in ('prepared','policy','quotes','session_date'):
            context.pop(name)
        header,source_rows,fill_rows=project_native_channel_pending(request,
            manager_snapshot_id=root['snapshot_id'],
            manager_checkpoint_sequence=root['checkpoint_sequence'],
            manager_boundary_ms=root['boundary_ms'],**context)
        pending.append(header)
        sources.extend(source_rows)
        executions.extend(fill_rows)
        if max(len(sources),len(executions))>policy.max_rows_per_family:
            raise ValueError('Complete native pending capture exceeds declared family capacity')
    rows=seal_native_channel_manager_rows(inherited,tuple(pending),tuple(sources),tuple(executions),
        max_pending_requests=policy.max_pending_requests,max_rows_per_family=policy.max_rows_per_family)
    reprove_native_channel_manager_snapshot(rows,policy,contexts)
    return rows
