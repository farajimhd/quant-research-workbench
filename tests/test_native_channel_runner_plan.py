import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_management_owner import owner_case
from scripts.clickhouse.native_channel_runner_plan import desired_plan
from scripts.clickhouse.provision_backtest_v4_original_risk_runner import desired_plan as parent_plan
from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
from src.trading_runtime.native_channel_management_policy import selected_writer_contracts
from src.market_engine.native_causal_channel_contract import FEATURE_TABLE, COVERAGE_TABLE


def test_dedicated_plan_retains_financial_grants_without_feature_writes(installed):
    owner,_,_,_,_=owner_case(installed)
    risk=ConfirmedOriginalRiskPolicy()
    parent=parent_plan(risk)
    plan=desired_plan(owner.policy,risk)
    companions=frozenset(table.name for table in selected_writer_contracts(owner.policy))
    products=frozenset(table.removeprefix('arte.') for table in (FEATURE_TABLE,COVERAGE_TABLE))
    assert plan.principal=='backtest_v4_native_channel_runner'
    assert plan.select_arte==parent.select_arte|companions|products
    assert plan.insert_arte==parent.insert_arte|companions
    assert not products & plan.insert_arte
    assert parent.principal=='backtest_v4_original_risk_runner'
    with pytest.raises(ValueError): desired_plan(None,risk)
    with pytest.raises(ValueError): desired_plan(owner.policy,None)


def test_storage_failure_precedes_credentials_or_mutation(installed,monkeypatch):
    from scripts.clickhouse.native_channel_runner_plan import apply_with_clients
    from types import SimpleNamespace
    owner,_,_,_,_=owner_case(installed)
    calls=[]
    admin=SimpleNamespace(execute=lambda sql:calls.append(sql) or ('operator' if 'currentUser' in sql else '0'))
    def fail(*args,**kwargs): raise ValueError('SSD missing')
    monkeypatch.setattr('src.trading_runtime.arte_journal_schema.storage_preflight',fail)
    with pytest.raises(ValueError,match='SSD missing'):
        apply_with_clients(admin=admin,credential=lambda **kw:pytest.fail('credential opened'),
            client_factory=lambda *args:pytest.fail('runner opened'),channel_policy=owner.policy,
            risk_policy=ConfirmedOriginalRiskPolicy())
    assert all(sql.startswith('SELECT ') for sql in calls)


def test_apply_reconciles_exact_grants_and_closes_runner(installed,monkeypatch):
    from scripts.clickhouse.native_channel_runner_plan import apply_with_clients
    from scripts.clickhouse.provision_fixed_backtest_v3_principals import _desired_grants
    from types import SimpleNamespace
    owner,_,_,_,_=owner_case(installed)
    risk=ConfirmedOriginalRiskPolicy()
    expected=desired_plan(owner.policy,risk)
    trace=[]
    admin=SimpleNamespace(execute=lambda sql:trace.append(sql) or ('operator' if 'currentUser' in sql else '1'))
    runner=SimpleNamespace(execute=lambda sql:expected.principal,close=lambda:trace.append('closed'))
    monkeypatch.setattr('src.trading_runtime.arte_journal_schema.storage_preflight',
        lambda *args,**kw:trace.append('journal SSD'))
    monkeypatch.setattr('src.backend.backtest_native_channel_store.storage_preflight',
        lambda *args,**kw:trace.append('feature SSD'))
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer._v4_preflight',
        lambda client:trace.append('runner preflight'))
    inventories=iter((set(),_desired_grants(expected)))
    monkeypatch.setattr('scripts.clickhouse.provision_fixed_backtest_v3_principals._effective_grants',
        lambda *args:next(inventories))
    def credential(**kwargs):
        assert kwargs=={'account_exists':True}
        assert 'journal SSD' in trace and 'feature SSD' in trace
        return 'x'*40
    assert apply_with_clients(admin=admin,credential=credential,client_factory=lambda *args:runner,
        channel_policy=owner.policy,risk_policy=risk)==expected
    assert trace[-1]=='closed'
    grants=[sql for sql in trace if sql.startswith('GRANT ')]
    assert len(grants)==len(_desired_grants(expected))
    assert not any('GRANT INSERT ON arte.'+table.removeprefix('arte.')+' ' in sql
        for table in (FEATURE_TABLE,COVERAGE_TABLE) for sql in grants)
    assert not any(sql.startswith(('ALTER ','CREATE TABLE','INSERT ')) for sql in trace)


def test_writer_feature_reads_are_selected_by_exact_channel_policy(installed,monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer
    from types import SimpleNamespace
    owner,_,_,_,_=owner_case(installed)
    permissions=[]
    monkeypatch.setattr(writer,'storage_preflight',lambda *args,**kw:None)
    monkeypatch.setattr(writer,'journal_permission_preflight',lambda *args,**kw:permissions.append(kw))
    client=SimpleNamespace(native_channel_management_policy=owner.policy,
        confirmed_original_risk_policy=ConfirmedOriginalRiskPolicy(),
        execute=lambda sql:'backtest_v4_native_channel_runner' if 'currentUser' in sql else '')
    writer._v4_preflight(client)
    products={table.removeprefix('arte.') for table in (FEATURE_TABLE,COVERAGE_TABLE)}
    assert products <= permissions[-1]['read_only_tables']
    assert not products & permissions[-1]['journal_tables']
    client.native_channel_management_policy=None
    client.confirmed_original_risk_policy=None
    writer._v4_preflight(client)
    assert not products & permissions[-1]['read_only_tables']
