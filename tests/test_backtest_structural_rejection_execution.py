"""Actual prepared-session/runtime/manager binding; certifier seams explicit."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_profit_armed_structural_rejection_publication import prewriter_profile
from test_profit_armed_structural_rejection_management import Evidence,DAY,RUN
from src.backend import backtest_structural_rejection_execution as execution
from src.backend.backtest_strategy_one_plan import StrategyOneFixedPlans
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.runtime import TradingRuntime,RunConfig,RunMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
from src.trading_runtime.domain import TradingMode


def prepared(monkeypatch):
    _,profile,_,_=prewriter_profile(monkeypatch)
    source=profile.source
    plans=StrategyOneFixedPlans(source.market,None,source.market,None,object(),None,None,
        source.seeds,source.intervals,None,object())
    authorities=(object(),object(),None,None,None,(),source.price_authority)
    calls=[]
    from src.backend import backtest_strategy_one_execution as inherited
    def entry(**kw):
        assert kw['market'] is plans.market and kw['candidates'] is plans.candidates
        assert kw['entry'] is plans.entry
        calls.append('entry')
        return authorities
    monkeypatch.setattr(inherited,'prepare_strategy_one_entry_authorities',entry)
    session=execution.prepare_structural_rejection_session(plans=plans,number=57,
        run_id=RUN,session_date=DAY,through_boundary_ms=40000,
        client_factory=lambda:SimpleNamespace(close=lambda:calls.append('closed')))
    return session,plans,authorities,calls


def arguments(plans):
    return dict(market=plans.market,candidates=plans.candidates,entry=plans.entry,
        through_boundary_ms=40000,run_id=RUN,number=57)


def runtime(session,*,run_id=RUN):
    source=session.source
    journal=BacktestMemoryJournal(run_id=run_id)
    config=RunConfig(RunMode.BACKTEST,source.strategy_id,source.strategy_revision,('DU1',),DAY,run_id=run_id)
    broker=SimulatedBrokerAdapter(['DU1'],SimulationConfig(initial_cash=10000.),
        mode=TradingMode.BACKTEST,initial_time=market_day_boundary(DAY,0),fixed_bar_mode=True)
    strategy=SimpleNamespace(strategy_id=config.strategy_id,revision=config.strategy_revision,automatic=True)
    return TradingRuntime(config,broker,strategy,journal,intent_planner=SimpleNamespace())


def test_factory_session_precedes_real_runtime_and_binds_exact_manager(monkeypatch):
    session,plans,authorities,calls=prepared(monkeypatch)
    assert session.require(**arguments(plans)) is session
    assert session.entry_authorities is authorities and calls==['entry','closed']
    actor=runtime(session)
    session.bind_runtime(actor)
    manager=StrategyOneManagementRunner(runtime=actor,evidence=Evidence(),tick_for_ticker=lambda _: .01)
    owner=execution.bind_structural_rejection_session_manager(session,manager)
    assert actor._structural_rejection_session is session
    assert owner is session.profile.owner and manager._structural_rejection_owner is owner
    assert owner.lookup is session.source.lookup
    assert owner.price_authority is authorities[-1]
    assert calls==['entry','closed']
    with pytest.raises(ValueError,match='unbound Backtest runtime'): session.bind_runtime(actor)
    actor.journal.close()


@pytest.mark.parametrize('field',('market','candidates','entry','through_boundary_ms','run_id','number'))
def test_session_cannot_be_reused_for_other_source_or_horizon(monkeypatch,field):
    session,plans,_,_=prepared(monkeypatch)
    args=arguments(plans)
    args[field]=39000 if field=='through_boundary_ms' else 58 if field=='number' else 'foreign' if field=='run_id' else object()
    with pytest.raises(ValueError,match='certified operation'): session.require(**args)


def test_constructor_copy_and_foreign_runtime_do_not_issue_authority(monkeypatch):
    session,plans,_,_=prepared(monkeypatch)
    with pytest.raises(ValueError,match='Unissued'): replace(session).require(**arguments(plans))
    actor=runtime(session,run_id='foreign')
    with pytest.raises(ValueError,match='exact unbound Backtest runtime'): session.bind_runtime(actor)
    actor.journal.close()
