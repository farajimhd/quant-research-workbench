from dataclasses import replace
from datetime import date,datetime,timezone
import pytest

from tests.test_backtest_native_channel_qualification import installed,unit  # noqa: F401
from tests.test_backtest_native_channel_exit import setup
from src.backend.backtest_native_channel_exit_request import build_native_channel_exit_request
from src.trading_runtime.native_acquisition_clock import NativeAcquisitionClock
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions


INTENT='11111111-1111-4111-8111-111111111111'


def inputs(installed):
    prepared,policy,decisions,quotes=setup(installed)
    ticker=decisions['ticker'][0]
    financial=StrategyOneFinancialView('assignment','account',ticker,AssignmentStatus.WATCHING,
        StrategyPermissions(exit=True),5.0,False,False,False,1)
    acquired=NativeAcquisitionClock(date(2026,8,4),'account','assignment',ticker,INTENT,'group',
        'execution',0,('execution',),datetime(2026,8,4,4,tzinfo=timezone.utc))
    return prepared,policy,decisions.head(1),quotes.head(1),financial,acquired


def test_pending_request_retains_exact_source_support_without_reads_or_order_admission(installed):
    prepared,policy,decisions,quotes,financial,acquired=inputs(installed)
    before=len(installed[0].sql)
    request=build_native_channel_exit_request(prepared,decisions,quotes,policy=policy,
        financial=financial,acquisition=acquired,source_entry_intent_id=INTENT)
    assert request.acquisition is acquired and request.financial is financial
    assert request.source_plan is prepared.source_plan
    assert request.supporting_rows.num_rows==1 and request.reference_rows.num_rows==0
    assert request.supporting_rows['available_day_boundary_ms'][0].as_py()==360000
    assert len(installed[0].sql)==before
    with pytest.raises(TypeError):request.decision['exit_eligible']=False


@pytest.mark.parametrize('defect',['owner','intent','flat','pending','ticker'])
def test_request_cannot_borrow_foreign_or_unheld_financial_source(installed,defect):
    prepared,policy,decisions,quotes,financial,acquired=inputs(installed)
    if defect=='owner':financial=replace(financial,account_id='foreign')
    if defect=='intent':acquired=replace(acquired,source_entry_intent_id='22222222-2222-4222-8222-222222222222')
    if defect=='flat':financial=replace(financial,position_quantity=0.0)
    if defect=='pending':financial=replace(financial,pending_exit=True)
    if defect=='ticker':financial=replace(financial,ticker='foreign')
    with pytest.raises(ValueError):build_native_channel_exit_request(prepared,decisions,quotes,
        policy=policy,financial=financial,acquisition=acquired,source_entry_intent_id=INTENT)
