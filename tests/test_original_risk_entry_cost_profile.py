"""Combined capability requires a declaration, exact grants and both SSD families."""
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
from tests.test_original_risk_profile import Catalog
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_entry_spread_risk_v4 import ENTRY_SPREAD_RISK
from src.trading_runtime.consecutive_price_confirmed_risk import (
    ConsecutivePriceRiskPolicy, ENTRY_COST_CAPABILITY_RULE, POLICY_KEY,
    parse_consecutive_price_risk_policy)
from src.trading_runtime.strategy_one_hundred_fourteen_release import PRICE_POLICY, release_contract, declared_policies
from src.trading_runtime.original_risk_diagnostic_profile import (
    validate_original_risk_profile, original_risk_runner_identity, original_risk_runner_options)
from src.trading_runtime.journal_contract import canonical_json
from scripts.clickhouse import provision_backtest_v4_original_risk_entry_cost_runner as provision
from scripts.clickhouse.provision_backtest_v4_entry_cost_runner import desired_plan as entry_plan

POLICY = replace(PRICE_POLICY, entry_cost_capability=True)


class CombinedCatalog(Catalog):
    def __init__(self, *, cost_grants=True, **kwargs):
        super().__init__(waiting=True, binding_grant=True, **kwargs)
        self.confirmed_original_risk_policy = POLICY
        self.entry_spread_risk_profile = True
        self.user = original_risk_runner_identity(POLICY)[1]
        self.contracts[ENTRY_SPREAD_RISK.name] = ENTRY_SPREAD_RISK
        if cost_grants:
            self.writable.add(ENTRY_SPREAD_RISK.name)
            self.required.add(ENTRY_SPREAD_RISK.name)


def test_combined_preflight_verifies_both_capabilities():
    client = CombinedCatalog()
    seal = writer._v4_preflight(client)
    assert seal.confirmed_original_risk_policy == POLICY
    assert seal.entry_spread_risk_profile is True
    assert any(ENTRY_SPREAD_RISK.name in q and 'FROM system.parts' in q for q in client.statements)


@pytest.mark.parametrize('defect', ['missing','policy','parts'])
def test_entry_cost_storage_fails_before_writes(defect):
    client = CombinedCatalog(defect=defect, defect_table=ENTRY_SPREAD_RISK.name)
    with pytest.raises((ValueError, RuntimeError)):
        writer._v4_preflight(client)
    assert all(not q.startswith(('INSERT','CREATE','GRANT')) for q in client.statements)


def test_missing_entry_cost_grants_fails_before_writes():
    with pytest.raises((ValueError, RuntimeError)):
        writer._v4_preflight(CombinedCatalog(cost_grants=False))


@pytest.mark.parametrize('principal', ['backtest_v4_original_risk_runner','backtest_v4_entry_cost_runner','backtest_v4_runner'])
def test_neither_old_principal_can_supply_combined_capability(principal):
    client = CombinedCatalog()
    client.user = principal
    with pytest.raises(RuntimeError, match='dedicated principal'):
        writer._v4_preflight(client)


def test_profile_flags_must_match_exact_declared_capability():
    validate_original_risk_profile(False, True, POLICY)
    for flags in ((False,False),(True,False),(True,True),(False,1)):
        with pytest.raises(ValueError):
            validate_original_risk_profile(*flags, POLICY)
    with pytest.raises(ValueError):
        validate_original_risk_profile(False,True,PRICE_POLICY)
    assert original_risk_runner_identity(PRICE_POLICY)[1] == 'backtest_v4_original_risk_runner'


def test_rule_and_payload_must_select_combination_together():
    release=release_contract()
    policies=declared_policies()
    policies[POLICY_KEY]=json.loads(canonical_json(POLICY.payload()))
    with pytest.raises(ValueError, match='capability declaration'):
        parse_consecutive_price_risk_policy(release,policies)
    selected=replace(release,rule_set_contracts=release.rule_set_contracts+(ENTRY_COST_CAPABILITY_RULE,))
    assert parse_consecutive_price_risk_policy(selected,policies)==POLICY
    with pytest.raises(ValueError, match='capability declaration'):
        parse_consecutive_price_risk_policy(selected,declared_policies())
    duplicate=replace(selected,rule_set_contracts=selected.rule_set_contracts+(ENTRY_COST_CAPABILITY_RULE,))
    with pytest.raises(ValueError, match='capability declaration'):
        parse_consecutive_price_risk_policy(duplicate,policies)


def test_declared_contract_cannot_select_combination_without_entry_cost_policy():
    contract=SimpleNamespace(confirmed_original_risk_policy=POLICY,entry_spread_risk_policy=None)
    with pytest.raises(ValueError,match='entry-cost semantics'):
        original_risk_runner_options(contract)
    contract.entry_spread_risk_policy=object()
    assert original_risk_runner_options(contract)==dict(confirmed_original_risk_policy=POLICY,entry_spread_risk=True)


def test_exact_grant_plan_extends_entry_cost_without_mutating_old_profiles():
    base=entry_plan()
    plan=provision.desired_plan(POLICY)
    assert entry_plan()==base
    assert plan.principal == original_risk_runner_identity(POLICY)[1]
    selected={'trading_original_risk_diagnostic_v4','trading_original_risk_pending_snapshot_v1','trading_strategy_one_manager_snapshot_v4'}
    assert plan.select_arte-base.select_arte==selected
    assert plan.insert_arte-base.insert_arte==selected
    assert ENTRY_SPREAD_RISK.name in plan.select_arte & plan.insert_arte
    assert all(p in {'SELECT','INSERT'} and '*' not in (d,t) for p,d,t in provision._desired_grants(plan))
    with pytest.raises(ValueError):
        provision.desired_plan(PRICE_POLICY)


def test_cold_reader_requires_combined_principal_and_readonly():
    from src.backend.backtest_fixed_journal_bootstrap import _v4_cold_reader_preflight
    client=CombinedCatalog()
    _v4_cold_reader_preflight(client)
    client.user='backtest_v4_original_risk_runner'
    with pytest.raises(RuntimeError,match='unexpected principal'):
        _v4_cold_reader_preflight(client)


def test_credential_selection_uses_only_combined_namespace(monkeypatch):
    seen=[]
    def credentials(prefix,path):
        seen.append((prefix,path))
        return 'http://127.0.0.1:18123',original_risk_runner_identity(POLICY)[1],'synthetic-only'
    monkeypatch.setattr(writer,'_dedicated_clickhouse_credentials',credentials)
    _,user,_=writer._v4_runner_credentials(entry_spread_risk=True,confirmed_original_risk_policy=POLICY)
    assert user == provision.PRINCIPAL
    assert seen == [(provision.STEM,'BACKTEST_V4_ORIGINAL_RISK_ENTRY_COST_RUNNER_CREDENTIAL_FILE')]


@pytest.mark.parametrize('defect', ['missing','policy','parts'])
def test_combined_provision_rejects_entry_cost_storage_before_credentials(monkeypatch,defect):
    from tests import test_provision_backtest_v4_original_risk_runner as legacy_tests
    monkeypatch.setattr(legacy_tests,'waiting',provision)
    lane=legacy_tests.Writer(())
    admin=legacy_tests.Operator(lane,defect=defect)
    admin.defect_table=ENTRY_SPREAD_RISK.name
    admin.contracts[ENTRY_SPREAD_RISK.name]=ENTRY_SPREAD_RISK
    private=[]
    with pytest.raises(ValueError,match='missing|layout differs|outside live_market_ssd'):
        provision.apply_with_clients(admin=admin,credential=lambda **kw:private.append(kw),
            client_factory=object(),policy=POLICY)
    assert not private
    assert not any(q.startswith(('GRANT','CREATE USER')) for q in admin.statements)


def test_combined_provision_real_preflight_and_idempotent_grant_apply(monkeypatch):
    from tests import test_provision_backtest_v4_original_risk_runner as legacy_tests
    monkeypatch.setattr(legacy_tests,'waiting',provision)
    lane=legacy_tests.Writer(())
    lane.user=provision.PRINCIPAL
    lane.contracts[ENTRY_SPREAD_RISK.name]=ENTRY_SPREAD_RISK
    admin=legacy_tests.Operator(lane)
    admin.contracts[ENTRY_SPREAD_RISK.name]=ENTRY_SPREAD_RISK
    password=legacy_tests.PASSWORD
    calls=[]
    def credentials(**kw):
        calls.append(kw)
        return password
    def factory(user,secret):
        assert user==provision.PRINCIPAL and secret==password
        return lane
    for attempt in range(2):
        admin.statements.clear()
        provision.apply_with_clients(admin=admin,credential=credentials,
            client_factory=factory,policy=POLICY)
        assert lane.grants==set(provision._desired_grants(provision.desired_plan(POLICY)))
        assert lane.confirmed_original_risk_policy==POLICY and lane.entry_spread_risk_profile is True
        assert lane.closed
        if attempt:
            assert not any(q.startswith(('GRANT','CREATE USER')) for q in admin.statements)
    assert calls==[{'account_exists':True},{'account_exists':True}]



def test_actual_cli_plan_and_failure_output_without_external_access(monkeypatch,capsys):
    from src.trading_runtime import numbered_fixed_strategy as registry
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    initialize_numbered_fixed_strategies()
    contract=SimpleNamespace(confirmed_original_risk_policy=POLICY,entry_spread_risk_policy=object())
    monkeypatch.setattr(registry,'numbered_fixed_strategy',lambda number:contract)
    monkeypatch.setattr(provision,'_admin_client',lambda *a:pytest.fail('external client opened'))
    monkeypatch.setattr(provision,'credential',lambda **kw:pytest.fail('secret opened'))
    assert provision.main(['--strategy-number','999'])==0
    output=capsys.readouterr()
    assert 'Plan only' in output.out and provision.PRINCIPAL in output.out
    assert 'SELECT' in output.out and 'INSERT' in output.out
    with pytest.raises(SystemExit) as error:
        provision.main(['--strategy-number','999','--apply','--confirm-policy','foreign'])
    assert error.value.code==2
    assert '--apply requires --confirm-policy' in capsys.readouterr().err
