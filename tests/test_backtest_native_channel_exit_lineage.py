from dataclasses import replace
from datetime import timedelta
import pytest

from tests.test_backtest_native_channel_qualification import installed,unit  # noqa: F401
from tests.test_backtest_native_channel_exit_request import inputs,INTENT
from tests.test_native_acquisition_clock import group,fill
from src.backend.backtest_native_channel_exit_request import build_native_channel_exit_request
from src.backend.backtest_native_channel_exit_lineage import project_native_channel_exit_lineage


def case(installed):
    prepared,policy,decisions,quotes,financial,acquired=inputs(installed)
    request=build_native_channel_exit_request(prepared,decisions,quotes,policy=policy,
        financial=financial,acquisition=acquired,source_entry_intent_id=INTENT)
    as_of=acquired.first_execution_time+timedelta(milliseconds=decisions['decision_day_ms'].item())
    owned=replace(group(),intent_id=INTENT,ticker=financial.ticker,
        submitted_at=acquired.first_execution_time-timedelta(seconds=1),updated_at=as_of)
    execution=fill('execution',5,acquired.first_execution_time,symbol=financial.ticker)
    kwargs=dict(run_id='development-protocol',batch_id='22222222-2222-4222-8222-222222222222',
        parent_record_id='33333333-3333-4333-8333-333333333333',
        decision_record_id='44444444-4444-4444-8444-444444444444',as_of=as_of,
        max_rows_per_family=100,expected_conid=11)
    return request,owned,(execution,),kwargs


def test_normalized_lineage_retains_exact_source_and_independently_checked_execution(installed):
    request,owned,executions,kwargs=case(installed)
    source,fills=project_native_channel_exit_lineage(request,owned,executions,**kwargs)
    assert len(source)==1 and len(fills)==1
    assert source[0]['available_day_boundary_ms']==360000
    assert fills[0]['source_event_time']==request.acquisition.first_execution_time
    assert fills[0]['source_execution_id']=='execution'
    assert all(len(row['content_hash'])==64 for row in (*source,*fills))
    assert project_native_channel_exit_lineage(request,owned,executions,**kwargs)==(source,fills)


@pytest.mark.parametrize('defect',['timestamp','conid','future_group','before_submission','foreign_intent'])
def test_projection_cannot_trust_inconsistent_fill_or_group_lineage(installed,defect):
    request,owned,executions,kwargs=case(installed)
    if defect=='timestamp':kwargs['as_of']+=timedelta(milliseconds=100)
    if defect=='conid':kwargs['expected_conid']=22
    if defect=='future_group':owned=replace(owned,updated_at=kwargs['as_of']+timedelta(seconds=1))
    if defect=='before_submission':owned=replace(owned,submitted_at=executions[0].trade_time+timedelta(seconds=1))
    if defect=='foreign_intent':owned=replace(owned,intent_id='55555555-5555-4555-8555-555555555555')
    with pytest.raises(ValueError):project_native_channel_exit_lineage(request,owned,executions,**kwargs)
