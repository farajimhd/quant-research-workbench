"""Declared signal cap, exact entry/OMS/report identity and closed publication seal."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.trading_runtime import strategy_fifty_release as release
from src.trading_runtime.early_original_risk_failure import EarlyOriginalRiskPolicy, early_original_risk_failure
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_followthrough_exit import validate_witness
from test_early_original_risk_failure import observation
from test_strategy_fifty_failure_route import witness, fixture as failure_graph
from test_strategy_fifty_release import source_fixture, APPROVAL
from test_arte_episode_activity_v4 import graph37, seal
from test_strategy_forty_two_management import prepared_manager


@pytest.mark.parametrize('signal,line,expected', [
    (0.,-.01,True), (.2002,.2,True), (.20020000000000003,.2,False),
    (-.00000001,-.01,False), (.3,.29,False)])
def test_exact_signal_fraction_inclusive_bounds(signal,line,expected):
    value=observation(macd_signal=signal,macd_line=line)
    assert (early_original_risk_failure(value,policy=release.EARLY_FAILURE_POLICY) is not None)==expected


@pytest.mark.parametrize('bounds', [[(0,1),(1,50)], ((True,1),(1,50)),
    ((0,0),(1,50)), ((1,50),(1,50)), ((1,40),(1,50)), ((0,1),(1,10001))])
def test_invalid_declared_signal_fraction_bounds_rejected(bounds):
    with pytest.raises(ValueError,match='Signal bounds'):
        EarlyOriginalRiskPolicy('invalid',None,(1,4),signal_reference_fraction_bounds=bounds)


def test_old_policy_payloads_do_not_gain_new_fields_or_conditions():
    from src.trading_runtime.strategy_forty_six_release import EARLY_FAILURE_POLICY as old
    from src.trading_runtime.strategy_forty_eight_release import EARLY_FAILURE_POLICY as negative
    assert 'signal_reference_fraction_bounds' not in old.payload()
    assert 'signal_reference_fraction_bounds' not in negative.payload()
    strong=observation(macd_line=.29,macd_signal=.3)
    assert early_original_risk_failure(strong,policy=old) is not None
    assert early_original_risk_failure(strong,policy=negative) is None
    assert early_original_risk_failure(strong,policy=release.EARLY_FAILURE_POLICY) is None


def test50_exact_native_entry_activity_sealer_and_foreign_number_rejection():
    args=graph37(50)
    sealed=seal(*args)
    assert sealed[0]['strategy_number']==50 and seal(sealed[0],*args[1:])==sealed
    with pytest.raises(ValueError,match='another numbered entry'):
        seal(*args[:4],replace(args[4],strategy_number=48))


def test50_witness_replays_signal_cap_without_legacy47_fallback():
    weak=witness()
    validate_witness(weak,strategy_number=50)
    strong=replace(weak,macd_line=.29,macd_signal=.3)
    validate_witness(strong,strategy_number=47)
    with pytest.raises(ValueError,match='pinned rule'):
        validate_witness(strong,strategy_number=50)


@pytest.mark.parametrize('signal,expected', [(.02,1),(.3,0),(-.01,0)])
def test50_native_management_selects_weak_positive_only(signal,expected):
    manager,w,financial,rows=prepared_manager(50)
    manager.contract=numbered_fixed_strategy(50)
    key=(financial.account_id,financial.assignment_id,financial.ticker)
    source=manager._submitted[key]
    at=w.completed_five_second_boundary_ms
    manager._first_held_boundaries[key]=at-30_000
    price=int((3*source.reference_ask+source.initial_stop)/4*10_000)-1
    frame=rows(at,completed=True,age=48)
    frame[5000].update(close_int=price,macd_line=signal-.01,macd_signal=signal)
    evidence=asyncio.run(manager.evidence.management_evidence(financial.ticker,frame,boundary_ms=at))
    manager.evidence.management_evidence=AsyncMock(return_value=replace(evidence,bid=price/10_000,ask=price/10_000+.01))
    asyncio.run(manager.on_management(financial,frame,at))
    assert manager.runtime.submit_followthrough_failure.await_count==expected
    if expected:validate_witness(manager.runtime.submit_followthrough_failure.await_args.args[1],strategy_number=50)


def test50_cold_oms_restores_exact_numbered_witness_and_rejects_other_number():
    from src.trading_runtime.arte_oms_projection import _approved_strategy_one_oms_intent
    original,base,row,*_=failure_graph(witness(),50)
    state=SimpleNamespace(sequence=2,group=dict(account_id='DU1',strategy_revision=50,group_id='group-50'))
    source=SimpleNamespace(intent=original,record_id=row['parent_record_id'],batch_id=row['batch_id'])
    history=SimpleNamespace(run_id=base.run_id,records=(),through_sequence=2)
    reservation=dict(account_id='DU1',intent_id=original.intent_id,reservation_id='reservation',
        decision_id='decision',account_key='cash',assignment_id='assignment-1',quantity=10.)
    decision=dict(reservation_id='reservation',decision_id='decision',account_key='cash',
        status='approved',policy_id='policy',policy_revision=1,requested_quantity=10.)
    approved,records=_approved_strategy_one_oms_intent(state,source,history,reservation,decision,followthrough_row=row)
    assert approved.intent_id==original.intent_id and records==()
    state.group['strategy_revision']=48
    with pytest.raises(ValueError,match='committed scalar witness'):
        _approved_strategy_one_oms_intent(state,source,history,reservation,decision,followthrough_row=row)


@pytest.mark.parametrize('changed',[False,True])
def test50_saved_report_requires_exact_release(monkeypatch,changed):
    from test_numbered_trade_report_release_evidence import test_report_requires_exact_numbered_release_and_preserves_identity
    test_report_requires_exact_numbered_release_and_preserves_identity(monkeypatch,50,changed)


def test50_compiler_requires_full_numbered_proof_and_source_seal_stays_closed(monkeypatch):
    from pipelines.strategy_one.strategy_fifty_configuration import compile_strategy_fifty_configuration
    from src.backend import backtest_fixed_v4_certification as certificates
    from src.backend.backtest_strategy_fifty_certification import certify_strategy_fifty_source
    calls=[]
    def blocked(number):
        calls.append(number)
        raise ValueError('unsealed reviewed source')
    monkeypatch.setattr(certificates,'certify_numbered_fixed_v4_projection',blocked)
    with pytest.raises(ValueError,match='unsealed'):
        compile_strategy_fifty_configuration(source_fixture(),**APPROVAL)
    assert calls==[50]
    from src.backend import backtest_strategy_fifty_certification as source_certificates
    assert len(certify_strategy_fifty_source()) == 64
    monkeypatch.setattr(source_certificates, 'STRATEGY50_SOURCE_AST', {})
    with pytest.raises(ValueError,match='review is not sealed'):
        certify_strategy_fifty_source()


def test50_publisher_plan_exact42_parent_and_no_publication(monkeypatch,capsys):
    import sys
    from scripts.clickhouse import publish_strategy_fifty_configuration as command
    from scripts.clickhouse import smoke_strategy_one_backtest as smoke
    from src.backend import backtest_v3_clients as clients
    monkeypatch.setattr(command,'approved_source',lambda commit:'a'*64)
    monkeypatch.setattr(smoke,'_load_private_credentials',lambda:None)
    reader=SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(clients,'v3_client',lambda principal:reader)
    def certified(actual,number):
        assert actual is reader and number==42
        return source_fixture()
    monkeypatch.setattr(command,'certify_numbered_configuration',certified)
    monkeypatch.setattr(command,'compile_strategy_fifty_configuration',
        lambda source,**kwargs:release.derive_strategy_fifty_configuration(source,**kwargs))
    monkeypatch.setattr(sys,'argv',['publish50','--approved-code-commit','a'*40,'--approval-reference','test-plan'])
    assert command.main()==0
    assert 'no publication performed' in capsys.readouterr().out
