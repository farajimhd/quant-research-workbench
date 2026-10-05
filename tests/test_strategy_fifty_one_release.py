"""Complete B declarations, exact economics, registry and normalized publication."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
import asyncio
import sys

import pytest

from src.trading_runtime import strategy_fifty_one_release as release
from src.trading_runtime.strategy_fifty_one_contract import (
    strategy_fifty_one_contract, AssignedFixedSwingLadder51, ladder_gate_policy)
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from test_strategy_forty_six_release import source_fixture, APPROVAL


def envelope():
    return release.derive_strategy_fifty_one_configuration(source_fixture(), **APPROVAL)


def reseal(manifest):
    manifest['manifest_hash'] = sha256(canonical_json(
        {k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()


def test_complete_b_manifest_copies_economics_without_old_entry_requirements():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = release.derive_strategy_fifty_one_configuration(source, **APPROVAL)
    assert source.payload == before
    payload = result['payload']
    for key in before.keys() - {'strategy','strategy_profile','run_plan'}:
        assert payload[key] == before[key]
    for key in before['strategy'].keys() - {
            'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}:
        assert payload['strategy'][key] == before['strategy'][key]
    manifest = payload['strategy']['numbered_release']
    assert set(manifest) & set(before['strategy']['numbered_release']) == {
        'contract','approved_digest','approved_code_commit','approved_code_fingerprint',
        'approval_reference','publication_mode','source_revision_id','source_payload_hash','manifest_hash',
        'session_policy'}
    assert manifest['session_policy'] == release.policies()['session_policy']
    contract = release.release_contract()
    assert contract.input_contracts == release.INPUT_CONTRACTS
    assert contract.rule_set_contracts == release.RULE_CONTRACTS
    assert not any('macd' in rule.lower() or 'bos' in rule.lower()
        for rule in contract.rule_set_contracts)
    assert 'certified-early-squeeze-signal-stream@completed-boundary' in contract.input_contracts
    assert manifest['automatic_market_policy']['gate']['qualification_mode'] == 'vwap_cross'
    assert 'source_through_boundary_ms' not in manifest['automatic_market_policy']
    assert manifest['automatic_market_policy']['source_through_boundary_ms_by_session'] == {
        'premarket':19_800_000, 'afterhours':57_600_000}
    assert manifest['economic_policy']['capital_request_value'] == 1/3
    assert manifest['automatic_market_policy']['population_exclusions'] == ['LGHL']
    assert _verified_numbered_envelope(result)[0] == payload


@pytest.mark.parametrize('field,wrong', [('payload_hash','f'*64),
    ('attempt_id','00000000-0000-0000-0000-000000000001')])
def test_exact_parent_rejection(field, wrong):
    with pytest.raises(ValueError, match='exact pinned'):
        release.derive_strategy_fifty_one_configuration(
            replace(source_fixture(), **{field:wrong}), **APPROVAL)


@pytest.mark.parametrize('policy', sorted(release.policies()))
def test_resealed_policy_mutation_rejected(policy):
    strategy = envelope()['payload']['strategy']
    manifest = strategy['numbered_release']
    manifest[policy]['unreviewed'] = True
    reseal(manifest)
    with pytest.raises(ValueError, match='pinned B policy'):
        release.verify_prepared_strategy_fifty_one_manifest(strategy)


def test_exact_complete_policy_shapes_and_native_contract_registry():
    contract = strategy_fifty_one_contract()
    gate = ladder_gate_policy()
    assert (gate.minimum_session_shares, gate.minimum_session_dollars,
        gate.minimum_trade_rate_10s, gate.minimum_trade_rate_60s,
        gate.maximum_spread_bps, gate.minimum_price_int, gate.quote_freshness_us,
        gate.admission_ttl_ms, gate.vwap_buffer_int) == (10000.,10000.,3.,3.,200.,10000,1000000,300000,0)
    assert contract.allows_session_exit
    assert not any((contract.allows_adds, contract.allows_reentry,
        contract.allows_trailing, contract.allows_replacement))
    assert contract.acquisition_cutoff(19_500_000) and not contract.acquisition_cutoff(19_499_900)
    assert contract.liquidation_due(19_740_000) and not contract.liquidation_due(19_739_900)
    assert contract.liquidation_due(57_300_000) and not contract.liquidation_due(57_299_900)
    assert numbered_strategy(51) == release.release_contract()
    executor = fixed_strategy_executor('early-squeeze-strategy',51)
    assert executor.contract_factory() == contract
    assert executor.strategy_factory is AssignedFixedSwingLadder51
    for number in (43,44,45):
        with pytest.raises(ValueError): numbered_strategy(number)


def test_own_assigned_factory_rejects_mutable_permissions_and_legacy_callbacks():
    from src.trading_runtime.strategy_engine import StrategyAssignment, AssignmentStatus
    contract = strategy_fifty_one_contract()
    assignment = StrategyAssignment('strategy-51:DU1:TEST','early-squeeze-strategy',51,
        'DU1','TEST',1,AssignmentStatus.WATCHING,contract.automatic_entry_policy.permissions,{})
    executor = fixed_strategy_executor('early-squeeze-strategy',51)
    built = executor.build([assignment],mode='backtest')
    assert built.assignments() == (assignment,)
    with pytest.raises(ValueError):executor.build([assignment,assignment],mode='backtest')
    with pytest.raises(ValueError):executor.build([assignment],mode='live')
    with pytest.raises(ValueError):
        executor.build([replace(assignment,permissions=replace(assignment.permissions,reenter=True))],mode='backtest')
    with pytest.raises(RuntimeError):asyncio.run(built.on_event(None,'DU1'))
    with pytest.raises(RuntimeError):asyncio.run(built.on_observation(None,'DU1'))


def test_compiler_routes_only49_and_propagates_missing_full_source_proof(monkeypatch):
    from src.backend import backtest_fixed_v4_certification as certification
    from pipelines.strategy_one.strategy_fifty_one_configuration import compile_strategy_fifty_one_configuration
    seen=[]
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',lambda n:seen.append(n))
    assert compile_strategy_fifty_one_configuration(source_fixture(),**APPROVAL) == envelope()
    assert seen == [51]
    def reject(number):
        assert number==51
        raise ValueError('unsealed combined source')
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',reject)
    with pytest.raises(ValueError,match='unsealed'):
        compile_strategy_fifty_one_configuration(source_fixture(),**APPROVAL)


def test_cli_dry_plan_reads_exact42_and_never_dispatches(monkeypatch,capsys):
    from scripts.clickhouse import publish_strategy_fifty_one_configuration as command
    from scripts.clickhouse import smoke_strategy_one_backtest as credentials
    from src.backend import backtest_v3_clients as clients
    reader=SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(sys,'argv',['publish49','--approved-code-commit','d'*40,'--approval-reference','test-only'])
    monkeypatch.setattr(command,'approved_source',lambda commit:'e'*64)
    monkeypatch.setattr(credentials,'_load_private_credentials',lambda:None)
    monkeypatch.setattr(clients,'v3_client',lambda role:reader)
    def certify(actual,number):
        assert actual is reader and number==42
        return source_fixture()
    monkeypatch.setattr(command,'certify_numbered_configuration',certify)
    monkeypatch.setattr(command,'compile_strategy_fifty_one_configuration',lambda *a,**kw:envelope())
    monkeypatch.setattr(command.subprocess,'run',lambda *a,**kw:pytest.fail('plan dispatched'))
    assert command.main()==0
    assert 'no publication performed' in capsys.readouterr().out
