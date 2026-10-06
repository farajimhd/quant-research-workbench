"""Pure grant plans, never credential/DB or installed source authority tests."""
from dataclasses import replace
import pytest
from scripts.clickhouse.declared_native_principal_plan import (
    desired_declared_native_plans,COMPANIONS,DependencyEvidence)
from scripts.clickhouse.provision_backtest_v4_runner import desired_plan as base_plan
from scripts.clickhouse.provision_backtest_v4_entry_cost_runner import desired_plan as cost_plan
from scripts.clickhouse.provision_backtest_v4_ladder_runner import desired_plan as ladder_plan

def test_exact_separate_principals_dependency_and_writer_boundaries():
    p=desired_declared_native_plans();base=base_plan();own=frozenset(t.name for t in COMPANIONS)
    assert len(own)==16
    assert p.running.principal=='backtest_v4_declared_native_runner'
    assert p.reader.principal=='backtest_v4_declared_native_reader'
    assert p.running.insert_arte==base.insert_arte|own
    assert p.reader.insert_arte==frozenset()
    assert p.reader.select_arte==p.running.select_arte
    assert p.running.select_reference==frozenset({('q_live','market_stock_split_v1')})
    for table in ('bars_v1','indicators_v1','liquidity_100ms_v1',
                  'structural_levels_v7','strategy_one_candidate_v1'):
        assert table in p.reader.select_arte
        assert table not in p.running.insert_arte
    grants=p.running.grants()+p.reader.grants()
    assert all(s.startswith(('GRANT SELECT ON ','GRANT INSERT ON ')) for s in grants)
    assert all('*' not in s for s in grants)
    assert not p.running.insert_arte & {e.table for e in p.evidence}

def test_old_profiles_are_byte_equivalent_values_before_and_after():
    before=(base_plan(),cost_plan(),ladder_plan())
    desired_declared_native_plans()
    assert before==(base_plan(),cost_plan(),ladder_plan())
    assert all(not frozenset(t.name for t in COMPANIONS)&p.insert_arte for p in before)

def test_plan_has_precise_unresolved_source_dependencies_and_is_not_install_ready():
    p=desired_declared_native_plans()
    assert len(p.unresolved)==5
    with pytest.raises(RuntimeError,match='closure unresolved'):p.require_deployment_ready()
    no_claim=replace(p,unresolved=())
    with pytest.raises(RuntimeError,match='cannot certify deployed'):no_claim.require_deployment_ready()

@pytest.mark.parametrize('mutation',[
 lambda p:replace(p,running=replace(p.running,principal='backtest_v4_runner')),
 lambda p:replace(p,reader=replace(p.reader,insert_arte=frozenset({'bars_v1'}))),
 lambda p:replace(p,running=replace(p.running,insert_arte=p.running.insert_arte|{'bars_v1'})),
 lambda p:replace(p,running=replace(p.running,select_arte=p.running.select_arte|{'*'})),
 lambda p:replace(p,reader=replace(p.reader,select_arte=p.reader.select_arte-{'strategy_one_candidate_v1'})),
 lambda p:replace(p,running=replace(p.running,select_reference=frozenset({('q_live','events')}))),
 lambda p:replace(p,evidence=p.evidence+(DependencyEvidence('foreign_table','caller'),)),
 lambda p:replace(p,evidence=p.evidence+(p.evidence[0],)),
 lambda p:replace(p,running=replace(p.running,select_system=p.running.select_system|{'users'})),
 lambda p:replace(p,running=replace(p.running,select_arte=set(p.running.select_arte))),
 lambda p:replace(p,unresolved=['not immutable']),
])
def test_no_wildcard_source_writer_legacy_or_caller_dependency_override(mutation):
    with pytest.raises(ValueError):mutation(desired_declared_native_plans())

def test_builder_has_no_connection_or_credentials_side_effect(monkeypatch):
    import socket
    def blocked(*a,**k):raise AssertionError('Network forbidden for source-only plan')
    monkeypatch.setattr(socket,'create_connection',blocked)
    a=desired_declared_native_plans();b=desired_declared_native_plans()
    assert a==b and a.running.grants()==b.running.grants()
