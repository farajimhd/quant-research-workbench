"""Rebind pending channel claims to an actual journal record; no write rights."""
from uuid import NAMESPACE_URL,uuid5

from .backtest_native_channel_exit_unit import (
    native_channel_exit_intent,project_native_channel_exit_unit,reprove_native_channel_exit_unit,
)
from .backtest_native_channel_exit_projection import NativeChannelExitCheckpointReference
from src.trading_runtime.native_channel_exit_journal_context import NativeChannelExitJournalContext
from src.trading_runtime.journal_contract import JournalRecord,canonical_json


def project_native_channel_journal_exit(record,source,*,attempt_id,batch_id,prior_batch_id,
        source_cursor,run_month,first_price_source,expected_config):
    if type(record) is not JournalRecord or type(source) is not tuple or len(source)!=4:
        raise ValueError('Channel projection requires exact journal record and complete pending source')
    request,context,strategy_id,strategy_revision=source
    if (type(context) is not NativeChannelExitJournalContext
            or first_price_source is not context.first_price_source
            or not isinstance(expected_config,dict)
            or (expected_config.get('strategy_id'),expected_config.get('strategy_revision'))
                !=(strategy_id,strategy_revision)):
        raise ValueError('Channel projection differs from its declared run/source authority')
    intent=native_channel_exit_intent(request,strategy_number=strategy_revision,
        management_policy=context.policy)
    payload={key:value for key,value in record.payload.items()
        if key not in {'strategy_id','strategy_revision','correlation_id','causation_id'}}
    base=context.unit.base
    if ((record.category,record.entity_type)!=('strategy','strategy_intent')
            or record.run_id!=base.run_id or record.entity_id!=intent.intent_id
            or record.account_id!=request.financial.account_id or record.event_time!=intent.event_time
            or record.sequence!=base.first_sequence or base.first_sequence!=base.last_sequence
            or prior_batch_id!=base.prior_batch_id or source_cursor!=base.source_cursor
            or run_month!=base.run_month
            or (record.payload.get('strategy_id'),record.payload.get('strategy_revision'))
                !=(strategy_id,strategy_revision)
            or canonical_json(payload)!=canonical_json(intent.payload())):
        raise ValueError('Channel projection changed original journal intent or checkpoint frontier')
    matches=[dict(value) for value in context.contexts.values()
        if value.get('current_financial')==request.financial
        and value.get('source_entry_intent_id')==request.acquisition.source_entry_intent_id]
    if len(matches)!=1:
        raise ValueError('Channel projection lacks exactly one independent acquisition context')
    evidence=matches[0]
    evidence.update(checkpoint=NativeChannelExitCheckpointReference(**{key:context.unit.decision[key]
        for key in ('source_manager_snapshot_id','source_manager_checkpoint_sequence',
            'source_manager_snapshot_hash','source_broker_snapshot_id','source_broker_snapshot_hash')}),
        batch_id=context.batch_id,parent_record_id=context.parent_record_id,
        decision_record_id=context.decision_record_id)
    reprove_native_channel_exit_unit(context.unit,management_policy=context.policy,
        recorded_at=context.recorded_at,attempt_id=context.attempt_id,
        prior_batch_id=base.prior_batch_id,sequence=base.first_sequence,source_cursor=base.source_cursor,
        correlation_id=context.correlation_id,causation_id=context.causation_id,**evidence)
    decision_id=str(uuid5(NAMESPACE_URL,f'{record.record_id}:native-channel-exit'))
    evidence.update(batch_id=batch_id,parent_record_id=record.record_id,decision_record_id=decision_id)
    envelope=dict(management_policy=context.policy,recorded_at=record.recorded_at,
        attempt_id=attempt_id,prior_batch_id=prior_batch_id,sequence=record.sequence,
        source_cursor=source_cursor,correlation_id=record.payload.get('correlation_id',''),
        causation_id=record.payload.get('causation_id',''))
    projection={key:value for key,value in evidence.items() if key not in
        {'prepared','policy','quotes','session_date','stored_utc','group','executions',
            'source_entry_intent_id','current_financial'}}
    unit=project_native_channel_exit_unit(request,evidence['group'],evidence['executions'],
        **envelope,**projection)
    unit=reprove_native_channel_exit_unit(unit,**envelope,**evidence)
    return NativeChannelExitJournalContext(unit,context.policy,context.contexts,first_price_source,
        record.recorded_at,attempt_id,batch_id,record.record_id,decision_id,
        envelope['correlation_id'],envelope['causation_id'])
