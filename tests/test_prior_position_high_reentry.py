"""Real financial reducer/coordinator with explicit unregistered declaration seam."""
import asyncio
from dataclasses import replace
import json
import pytest
from test_strategy_one_stateful import _facts
from src.trading_runtime.prior_position_high_reentry import *
from src.trading_runtime.strategy_sixty_four_contract import strategy_sixty_four_contract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_stateful import StrategyOneReentryWitness
from src.backend import backtest_strategy_one_stateful as adapter
from src.backend.backtest_strategy_one_coordinator import run_strategy_one_proposals
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler


def selected_contract():
    # Component declaration fixture only. No identity is registered/published.
    base = strategy_sixty_four_contract()
    release = replace(base.release,
        rule_set_contracts=base.release.rule_set_contracts + (PRIOR_POSITION_HIGH_REENTRY_RULE,),
        input_contracts=base.release.input_contracts + (PRIOR_POSITION_HIGH_REENTRY_INPUT,),
        approved_digest='')
    release = replace(release, approved_digest=release.digest())
    payload = json.loads(base.policy_json)
    payload['prior_position_high_reentry_policy'] = PriorPositionHighReentryPolicy().payload()
    return replace(base, release=release, policy_json=canonical_json(payload))


def test_exact_declaration_pairing_and_default_absence():
    assert strategy_sixty_four_contract().prior_position_high_reentry_policy is None
    selected = selected_contract()
    assert type(selected.prior_position_high_reentry_policy) is PriorPositionHighReentryPolicy
    for field, marker in [('rule_set_contracts', PRIOR_POSITION_HIGH_REENTRY_RULE),
                          ('input_contracts', PRIOR_POSITION_HIGH_REENTRY_INPUT)]:
        for values in [tuple(v for v in getattr(selected.release, field) if v != marker),
                       getattr(selected.release, field) + (marker,)]:
            release = replace(selected.release, **{field: values}, approved_digest='')
            release = replace(release, approved_digest=release.digest())
            with pytest.raises(ValueError): replace(selected, release=release)
    payload=json.loads(selected.policy_json)
    payload['prior_position_high_reentry_policy']['every_reentry']=1
    with pytest.raises(ValueError): replace(selected, policy_json=canonical_json(payload))
    payload.pop('prior_position_high_reentry_policy')
    with pytest.raises(ValueError): replace(selected, policy_json=canonical_json(payload))


@pytest.mark.parametrize('previous,current,accepted', [(99900,100200,True),(100000,100001,True),
    (100001,100200,False),(99900,100000,False),(99900,99999,False)])
def test_real_coordinator_late_other_resistance(monkeypatch, previous, current, accepted):
    candidate,fact,activation,financial=_facts()
    financial=replace(financial,completed_entries=1,
        permissions=replace(financial.permissions,reenter=True))
    witness=StrategyOneReentryWitness(20000,'OTHER',100000,previous,current)
    # Preserve all native source/candidate/coordinator operations. Only installed
    # contract lookup is explicit since no successor identity exists yet.
    original=adapter.numbered_fixed_strategy(1)
    policy=selected_contract().prior_position_high_reentry_policy
    component=selected_contract()
    monkeypatch.setattr(type(component), "entry_momentum_growth_policy", property(lambda _:None))
    actions=[]
    async def noop(*args): pass
    async def views(*args): actions.append('financial'); return (financial,)
    async def prior(*args): actions.append('prior'); return witness
    async def proposed(value): actions.append('proposal')
    def run(contract):
        monkeypatch.setattr(adapter,'numbered_fixed_strategy',lambda _:contract)
        scheduler=StrategyOneBoundaryScheduler(session_date='2026-08-18',candidate_rows=iter((candidate,)),
            active_source=lambda *_:iter(()))
        entry=CertifiedEntryEvidencePlan('b'*16,'2026-08-18',(),(activation,),(fact,),'e'*64)
        return asyncio.run(run_strategy_one_proposals(scheduler,entry,process_broker_boundary=noop,
            financial_views=views,on_entry_proposal=proposed,on_management=noop,
            position_source_owned=lambda _:False,financially_active_tickers=lambda:(),finish_boundary=noop,
            observe_activation=noop,observe_completed_seconds=noop,reentry_witness=prior))
    assert run(original).entry_proposals==1
    actions.clear()
    assert run(component).entry_proposals==int(accepted)
    assert actions[:2]==['financial','prior']
    assert ('proposal' in actions)==accepted


def test_adapter_first_entry_and_foreign_policy(monkeypatch):
    c,f,a,v=_facts()
    original=adapter.numbered_fixed_strategy(1)
    baseline=adapter.propose_certified_strategy_one_entry(c,f,a,v)
    component=selected_contract()
    monkeypatch.setattr(type(component), "entry_momentum_growth_policy", property(lambda _:None))
    monkeypatch.setattr(adapter,'numbered_fixed_strategy',lambda _:component)
    assert adapter.propose_certified_strategy_one_entry(c,f,a,v)==baseline
    monkeypatch.setattr(type(component), "prior_position_high_reentry_policy", property(lambda _:object()))
    with pytest.raises(TypeError): adapter.propose_certified_strategy_one_entry(c,f,a,v)


def test_actual_completed_source_adapter_without_momentum_bypass(monkeypatch):
    from test_backtest_strategy_first_price_source import authority, Bars
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan, propose_certified_price_entry
    from test_backtest_strategy_entry_activity_source import source_authority
    market,plan=source_authority(ten_percent=True)
    parent=plan.source.parent
    candidate,_,_,financial=_facts()
    contract=selected_contract()
    monkeypatch.setattr(adapter,'numbered_fixed_strategy',lambda _:contract)
    fact=parent.entry.lookup('AAA',31000)
    activation=parent.entry.activations[0]
    financial=replace(financial,completed_entries=1,permissions=replace(financial.permissions,reenter=True))
    witness=StrategyOneReentryWitness(20000,'OTHER',100000,99900,100200)
    allowed=propose_certified_price_entry(plan,candidate,fact,activation,financial,reentry=witness,strategy_number=64)
    assert allowed.proposal is not None
    denied=propose_certified_price_entry(plan,candidate,fact,activation,financial,
        reentry=replace(witness,current_bar_close_int=100000),strategy_number=64)
    assert denied.reason=='reentry_requires_prior_high_bar_break'
