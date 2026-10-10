"""Normalized pending source/fill projection; no native checkpoint admission."""
from uuid import UUID,NAMESPACE_URL,uuid5
from zoneinfo import ZoneInfo

from .backtest_native_channel_exit_request import NativeChannelExitRequest
from src.trading_runtime.native_acquisition_clock import require_acquisition_clock_matches_executions
from src.trading_runtime.arte_native_channel_exit_schema import SOURCE_ROW,EXECUTION_ROW
from src.trading_runtime.arte_journal_writer import typed_contract_row


def project_native_channel_exit_lineage(request, group, executions, *, run_id,
        batch_id,parent_record_id,decision_record_id,as_of,max_rows_per_family,expected_conid):
    """Reprove raw acquisitions and project bounded survivor facts losslessly.

    The journal owner must still reprove source coverage, the original entry,
    checkpoint roots and exact position. Returned rows are not publication or
    permission to submit orders; no registered family catalog is changed.
    """
    if (type(request) is not NativeChannelExitRequest or type(run_id) is not str or not run_id
            or type(max_rows_per_family) is not int or max_rows_per_family<1
            or request.decision['exit_eligible'] is not True):
        raise ValueError('Exact accepted pending request and bounded native context required')
    for value in (batch_id,parent_record_id,decision_record_id):
        if type(value) is not str or str(UUID(value))!=value or UUID(value).int==0:
            raise ValueError('Canonical nonzero native row identities required')
    clock=request.acquisition
    clock=require_acquisition_clock_matches_executions(clock,group,executions,
        session_date=clock.session_date,as_of=as_of,
        source_entry_intent_id=clock.source_entry_intent_id,
        expected_conid=expected_conid)
    local=as_of.astimezone(ZoneInfo('America/New_York'))
    day_us=((local.hour*3600+local.minute*60+local.second)*1000000+local.microsecond)
    if day_us!=request.decision['decision_day_ms']*1000:
        raise ValueError('Lineage projection differs from exact pending decision clock')
    if (len(clock.execution_ids)>max_rows_per_family
            or request.supporting_rows.num_rows+request.reference_rows.num_rows>max_rows_per_family):
        raise ValueError('Native channel lineage exceeds declared family row bound')
    common=dict(parent_record_id=parent_record_id,run_id=run_id,
        event_month=clock.session_date.replace(day=1).isoformat(),batch_id=batch_id,
        decision_record_id=decision_record_id)
    source_rows=[]
    for role,table in (('support',request.supporting_rows),('reference',request.reference_rows)):
        for ordinal,row in enumerate(table.to_pylist()):
            if (row['ticker']!=clock.ticker or row['session_date']!=clock.session_date.isoformat()
                    or any(row[key]!=request.decision[key] for key in (
                        'build_id','attempt_id','feature_attempt_id','policy_digest'))
                    or row['resolution_ms']!=request.policy.sequence.resolution_ms
                    or row['available_day_boundary_ms']>request.decision['decision_day_ms']):
                raise ValueError('Channel source lineage is foreign or future')
            source_rows.append(typed_contract_row(SOURCE_ROW,dict(common,
                record_id=str(uuid5(NAMESPACE_URL,f'{decision_record_id}:{role}:{ordinal}')),
                source_role=role,source_ordinal=ordinal,source_build_id=row['build_id'],
                session_date=row['session_date'],ticker=row['ticker'],
                source_bars_attempt_id=row['attempt_id'],source_feature_attempt_id=row['feature_attempt_id'],
                input_policy_digest=row['policy_digest'],resolution_ms=row['resolution_ms'],
                bucket_index=row['bucket_index'],available_day_boundary_ms=row['available_day_boundary_ms'],
                close_int=row['close_int'],channel=request.policy.sequence.channel,
                channel_value=row[request.policy.sequence.channel])))
    by_id={e.execution_id:e for e in executions if e.execution_id in clock.execution_ids
        and e.account==group.account_id and e.order_id in group.broker_order_ids and e.side=='B'}
    execution_rows=[]
    for ordinal,identity in enumerate(clock.execution_ids):
        e=by_id[identity]
        execution_rows.append(typed_contract_row(EXECUTION_ROW,dict(common,
            record_id=str(uuid5(NAMESPACE_URL,f'{decision_record_id}:execution:{ordinal}')),
            execution_ordinal=ordinal,source_execution_id=identity,source_order_id=e.order_id,
            source_group_id=clock.source_group_id,account_id=e.account,ticker=e.symbol,conid=e.conid,
            source_event_time=e.trade_time,source_epoch_ms=e.trade_time_r,quantity=e.size)))
    return tuple(source_rows),tuple(execution_rows)
