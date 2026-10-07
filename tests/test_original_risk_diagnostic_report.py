"""Actual saved-report attribution with decoded external producer reads mocked."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.backend import backtest_v4_performance_evidence as report
from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
from test_original_risk_diagnostic_native import native_unit


@pytest.mark.parametrize('tamper',[False,True])
def test_selected_report_exports_exact_semantic_witness_and_rejects_tampered_hash(monkeypatch,tamper):
    from src.backend import backtest_v4_saved_review as review
    from src.trading_runtime import arte_journal_writer as writer,arte_intent_projection as intents
    from src.trading_runtime.arte_followthrough_failure_v4 import FAILURE
    from src.trading_runtime.arte_original_risk_diagnostic_v4 import DIAGNOSTIC
    unit,diagnostic,*_,intent=native_unit()
    parent=unit.failure['parent_record_id'];command_id=str(UUID(int=100))
    at=intent.event_time
    command=dict(record_id=command_id,run_id=unit.base.run_id,batch_id=unit.base.batch_id,
        account_id='DU1',client_order_id='exit-coid',ticker='TEST',conid=123,
        strategy_id='early-squeeze-strategy',strategy_revision=68,side='SELL',sequence=3,
        created_at=(at+timedelta(microseconds=100)).isoformat())
    context=writer.typed_row('trading_order_command_context_v1',dict(record_id=str(UUID(int=101)),
        parent_record_id=command_id,run_id=unit.base.run_id,event_month='2026-01-01',
        batch_id=unit.base.batch_id,account_id='DU1',strategy_intent_id=intent.intent_id,
        order_group_id='group',policy_version='1'))
    failure=writer.typed_row(FAILURE.name,dict(unit.failure))
    companion=writer.typed_row(DIAGNOSTIC.name,dict(unit.diagnostic))
    if tamper:companion['prior_close_int']-=1
    def external_rows(client,sql):
        if 'command_context' in sql:return [context]
        if DIAGNOSTIC.name in sql:return [companion]
        if FAILURE.name in sql:return [failure]
        return [dict(record_id=parent,intent_id=intent.intent_id)]
    monkeypatch.setattr(review,'_complete_detail_rows',lambda *args:(command,))
    monkeypatch.setattr(writer,'_committed_batch_filter',lambda *args:'')
    monkeypatch.setattr(writer,'_rows',external_rows)
    monkeypatch.setattr(intents,'load_committed_strategy_intent_page',lambda *args,**kwargs:
        (SimpleNamespace(record_id=parent,sequence=2,account_id='DU1',intent=intent),))
    execution=SimpleNamespace(execution_id='fill',broker_order_id='58',client_order_id='exit-coid',
        account_id='DU1',instrument=SimpleNamespace(symbol='TEST',conid=123),side='SELL',
        strategy_id='early-squeeze-strategy',strategy_revision=68,
        journal_sequence=4,source_event_time=at+timedelta(microseconds=200),
        exit_reason=intent.reason,quantity=Decimal(10))
    client=SimpleNamespace(confirmed_original_risk_policy=ConfirmedOriginalRiskPolicy())
    prefix=SimpleNamespace(run_id=unit.base.run_id,batch_ids=(unit.base.batch_id,))
    lifecycle=dict(side='LONG',closed_at=execution.source_event_time.isoformat(),
        execution_ids=['fill'],protection_timeline=[])
    if tamper:
        with pytest.raises(RuntimeError,match='differs from its committed hash'):
            report.attach_exit_evidence(client,prefix,[lifecycle],[execution])
    else:
        report.attach_exit_evidence(client,prefix,[lifecycle],[execution])
        component,=lifecycle['exit_components'];observed=component['original_risk_diagnostic']
        assert component['reason']==intent.reason and component['source']=='journal'
        assert observed['semantic_rule']==diagnostic.semantic_rule
        assert observed['newest']['boundary_ms']==diagnostic.current.boundary_ms
        assert observed['prior']['boundary_ms']==diagnostic.current.boundary_ms-5000
        assert observed['failure_content_hash']==failure['content_hash']
