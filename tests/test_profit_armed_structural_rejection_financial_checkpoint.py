"""Financial publication boundary qualification; persisted readers are seams."""
from dataclasses import replace
from types import SimpleNamespace
import asyncio

import pytest

from src.trading_runtime import profit_armed_structural_rejection_financial_checkpoint as financial


def issued():
    publication,client,session=object(),object(),object()
    capture=financial.StructuralRejectionFinancialCapture(object(),object(),9,'{}')
    receipt=financial.StructuralRejectionFinancialVerification(publication,capture,'broker','oms')
    financial._VERIFIED[receipt]=(publication,capture,client,session,'broker','oms')
    return receipt,dict(publication=publication,client=client,session=session)


def test_publication_boundary_reverifies_complete_financial_capture(monkeypatch):
    receipt,kwargs=issued()
    calls=[]
    def verify(capture,publication,*,client,session):
        calls.append((capture,publication,client,session))
        return SimpleNamespace(broker_head='broker',oms_head='oms')
    monkeypatch.setattr(financial,'verify_financial_capture',verify)
    assert financial.require_financial_verification(receipt,**kwargs) is receipt
    assert calls==[(receipt.capture,kwargs['publication'],kwargs['client'],kwargs['session'])]


@pytest.mark.parametrize('reason',('journal cursor changed','Portfolio inventory changed','OMS inventory changed'))
def test_old_receipt_does_not_bypass_fresh_inventory_failure(monkeypatch,reason):
    receipt,kwargs=issued()
    def verify(*args,**kw):
        raise ValueError(reason)
    monkeypatch.setattr(financial,'verify_financial_capture',verify)
    with pytest.raises(ValueError,match=reason):
        financial.require_financial_verification(receipt,**kwargs)


@pytest.mark.parametrize('field',('broker_head','oms_head'))
def test_fresh_head_must_match_original_receipt(monkeypatch,field):
    receipt,kwargs=issued()
    fresh=SimpleNamespace(broker_head='broker',oms_head='oms')
    setattr(fresh,field,'changed')
    monkeypatch.setattr(financial,'verify_financial_capture',lambda *args,**kw:fresh)
    with pytest.raises(ValueError,match='heads changed'):
        financial.require_financial_verification(receipt,**kwargs)


def test_copied_receipt_cannot_authorize_publication():
    receipt,kwargs=issued()
    with pytest.raises(ValueError,match='Unissued'):
        financial.require_financial_verification(replace(receipt),**kwargs)


def test_capture_uses_actual_runtime_broker_oms_and_portfolio_actors(monkeypatch):
    from datetime import date
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_market_data import market_day_boundary
    from src.backend import backtest_profit_armed_structural_rejection_management as manager_module
    from src.trading_runtime.runtime import TradingRuntime,RunConfig,RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
    from src.trading_runtime.domain import TradingMode
    from src.trading_runtime import profit_armed_structural_rejection_profile as profile_module
    from src.trading_runtime import profit_armed_structural_rejection_snapshot as snapshots
    from tests.test_trading_runtime import _NoopStrategy

    async def run():
        day=date(2026,8,18);run_id='00000000-0000-0000-0000-000000000010'
        journal=BacktestMemoryJournal(run_id=run_id)
        broker=SimulatedBrokerAdapter(['DU1'],SimulationConfig(initial_cash=10000.),
            mode=TradingMode.BACKTEST,initial_time=market_day_boundary(day,0),fixed_bar_mode=True)
        runtime=TradingRuntime(RunConfig(RunMode.BACKTEST,'noop',1,('DU1',),day,run_id=run_id),
            broker,_NoopStrategy(),journal,intent_planner=SimpleNamespace())
        await runtime.initialize()
        # Source/profile/manager guards are explicit component seams. Financial
        # actors, capture methods, projections and full Portfolio hash are real.
        state=SimpleNamespace(boundary_ms=25000)
        owner=SimpleNamespace(manager=SimpleNamespace(runtime=runtime))
        profile=SimpleNamespace(owner=owner)
        monkeypatch.setattr(profile_module,'require_native_structural_rejection_profile',lambda *a,**k:profile)
        monkeypatch.setattr(manager_module,'require_structural_rejection_capture',lambda *a,**k:owner)
        monkeypatch.setattr(snapshots,'project_structural_rejection_snapshot',lambda **k:
            SimpleNamespace(snapshot={'content_hash':'a'*64}))
        runtime.last_event_time=market_day_boundary(day,state.boundary_ms)
        captured=financial.issue_financial_capture(profile,state,sequence=9)
        image=financial.require_financial_capture(captured,profile=profile,state=state)
        assert image['broker']['snapshot']['boundary_ms']==25000
        assert image['oms']['root']['boundary_ms']==25000
        assert tuple(image['portfolio'])==('DU1',)
        assert len(image['portfolio']['DU1']['state_hash'])==64
        assert image['manager_hash']=='a'*64
        # Actor changes after handoff cannot rewrite the issued old image.
        runtime.portfolio.states['DU1'].peak_net_liquidation+=1.
        later=financial.issue_financial_capture(profile,state,sequence=9)
        assert financial.require_financial_capture(captured)==image
        assert financial.require_financial_capture(later)['portfolio']!=image['portfolio']
        with pytest.raises(ValueError,match='Unissued'):
            financial.require_financial_capture(replace(captured))
        journal.close()
    asyncio.run(run())
