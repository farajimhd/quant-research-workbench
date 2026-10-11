from dataclasses import replace
from datetime import datetime,timezone

import pytest

from tests.test_backtest_native_channel_qualification import installed,unit  # noqa: F401
from tests.test_backtest_native_channel_exit_recovery import recovery_case
from src.backend.backtest_native_channel_management_request import build_native_channel_management_request


def native_inputs(installed):
    _,context=recovery_case(installed)
    elapsed=context['as_of']-datetime(1970,1,1,tzinfo=timezone.utc)
    at_us=(elapsed.days*86400+elapsed.seconds)*1_000_000+elapsed.microseconds
    from src.trading_runtime.native_acquisition_clock import acquisition_clock_from_entry_executions
    acquisition=acquisition_clock_from_entry_executions(context['group'],context['executions'],
        session_date=context['session_date'],as_of=context['as_of'],
        source_entry_intent_id=context['source_entry_intent_id'],expected_conid=context['expected_conid'])
    quote=context['quotes'].row(0,named=True)
    return context,dict(quote_valid=1,quote_timestamp_us=at_us,bid_int=quote['bid_int'],ask_int=quote['ask_int'],
        ticker=context['current_financial'].ticker,session_date=context['session_date'].isoformat(),boundary_ms=360000),acquisition


def build(context,quote,acquisition):
    return build_native_channel_management_request(context['prepared'],quote,policy=context['policy'],
        financial=context['current_financial'],acquisition=acquisition,
        original_entry=context['original_entry'],boundary_ms=360000)


def test_native_adapter_preserves_causal_clock_original_risk_and_no_source_reads(installed):
    context,quote,acquisition=native_inputs(installed)
    before=len(installed[0].sql)
    request=build(context,quote,acquisition)
    assert request is not None
    assert request.decision['decision_day_ms']==14_760_000
    assert request.acquisition==acquisition
    assert request.financial is context['current_financial']
    assert len(installed[0].sql)==before


@pytest.mark.parametrize('defect',['future','stale','missing','pending','flat'])
def test_native_adapter_cannot_enter_an_exit_without_current_quote_and_holdings(installed,defect):
    context,quote,acquisition=native_inputs(installed)
    if defect=='future': quote['quote_timestamp_us']+=1
    elif defect=='stale': quote['quote_timestamp_us']-=context['policy'].quote.quote_max_age_us+1
    elif defect=='missing': quote['quote_valid']=0
    else: context['current_financial']=replace(context['current_financial'],
        **({'pending_exit':True} if defect=='pending' else {'position_quantity':0.0}))
    assert build(context,quote,acquisition) is None


def test_native_adapter_rejects_foreign_original_entry(installed):
    context,quote,acquisition=native_inputs(installed)
    context['original_entry']=replace(context['original_entry'],account_id='foreign')
    with pytest.raises(ValueError): build(context,quote,acquisition)


@pytest.mark.parametrize('field,value',[('ticker','foreign'),('session_date','2026-08-05'),('boundary_ms',360100)])
def test_native_adapter_rejects_crossed_quote_identity(installed,field,value):
    context,quote,acquisition=native_inputs(installed)
    quote[field]=value
    with pytest.raises(ValueError): build(context,quote,acquisition)
