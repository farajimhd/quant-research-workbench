"""Native source repair preserves published46 policy under new immutable47."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime import strategy_forty_six_release as parent
from src.trading_runtime import strategy_forty_seven_release as child
from src.trading_runtime.journal_contract import canonical_json
from test_strategy_forty_six_release import source_fixture as strategy42_fixture, APPROVAL


def source_fixture():
    result = parent.derive_strategy_forty_six_configuration(strategy42_fixture(), **APPROVAL)
    return CertifiedStrategyOneConfiguration(
        child.PARENT_REVISION_ID.split(':')[1], child.PARENT_PAYLOAD_HASH,
        result['node_hash'], result['source_candidate_id'], result['source_candidate_hash'],
        'test-only', result['payload'])


def test_repair_preserves_every_nonidentity_configuration_field_and_policy():
    source = source_fixture()
    before = deepcopy(source.payload)
    result = child.derive_strategy_forty_seven_configuration(source, **APPROVAL)
    assert source.payload == before
    current, prior = child.release_contract(), parent.release_contract()
    assert current.number == current.executor_revision == 47
    assert current.rule_set_contracts == prior.rule_set_contracts
    assert current.input_contracts == prior.input_contracts
    assert current.evaluation_interval == prior.evaluation_interval
    assert current.rule_set_contracts.count(child.EARLY_FAILURE_POLICY.policy_id) == 1
    changed = {
        'strategy': {'strategy_number', 'revision', 'name', 'profile_id', 'profile_revision', 'numbered_release'},
        'strategy_profile': {'profile_id', 'revision', 'definition_revision', 'name', 'description'},
        'run_plan': {'name', 'description', 'profile_id'},
    }
    for section, old in before.items():
        new = result['payload'][section]
        if section not in changed:
            assert new == old
        else:
            assert old.keys() == new.keys()
            for name in old.keys() - changed[section]:
                assert new[name] == old[name]
    policies = set(child.INHERITED_POLICIES) | {'half_risk_liquidity_policy', 'early_original_risk_failure_policy'}
    for name in policies:
        assert result['payload']['strategy']['numbered_release'][name] == before['strategy']['numbered_release'][name]
    assert result['source_candidate_hash'] == child.PARENT_PAYLOAD_HASH
    assert result['payload_hash'] == sha256(canonical_json(result['payload']).encode()).hexdigest()


@pytest.mark.parametrize('field,value', [
    ('payload_hash', 'a' * 64), ('attempt_id', '00000000-0000-0000-0000-000000000001'),
])
def test_rejects_foreign_exact46_parent(field, value):
    with pytest.raises(ValueError, match='exact pinned certified Strategy46'):
        child.derive_strategy_forty_seven_configuration(replace(source_fixture(), **{field: value}), **APPROVAL)


def test_installed_verifier_checks_exact47_executor(monkeypatch):
    from src.trading_runtime import strategy_registry
    result = child.derive_strategy_forty_seven_configuration(source_fixture(), **APPROVAL)
    original = strategy_registry.fixed_strategy_executor
    seen = []
    def observe(strategy_id, number):
        seen.append(number)
        return original(strategy_id, number)
    monkeypatch.setattr(strategy_registry, 'fixed_strategy_executor', observe)
    child.verify_strategy_forty_seven_manifest(result['payload']['strategy'])
    assert seen == [47]


def test_compiler_envelope_and_complete_native_source_certificate():
    from pipelines.strategy_one.strategy_forty_seven_configuration import compile_strategy_forty_seven_configuration
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    envelope = compile_strategy_forty_seven_configuration(source_fixture(), **APPROVAL)
    payload, nodes = _verified_numbered_envelope(envelope)
    assert payload['strategy']['strategy_number'] == 47
    assert len(nodes) == envelope['node_count']
    assert len(certify_numbered_fixed_v4_projection(47)) == 64
    with pytest.raises(ValueError, match='provenance'):
        _verified_numbered_envelope(dict(envelope, source_candidate_hash='f' * 64))


def test_source_certificate_rejects_mutation_of_own47_module(tmp_path):
    from src.backend.backtest_strategy_forty_seven_certification import certify_strategy_forty_seven_source
    relative = 'src/trading_runtime/strategy_forty_seven_release.py'
    changed = tmp_path / 'changed47.py'
    changed.write_text(Path(relative).read_text(encoding='utf-8') + '\nUNREVIEWED_CHANGE = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned release source changed'):
        certify_strategy_forty_seven_source(source_overrides={relative: changed})


def test47_inherited_exit_reason_factories_and_declared_rule():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, numbered_session_exit_reason
    from src.trading_runtime.strategy_profit_giveback_exit import profit_giveback_reason
    from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_reason
    from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_reason
    assert numbered_session_exit_reason(47) == 'strategy_forty_seven_session_exit'
    assert profit_giveback_reason(47) == 'strategy_forty_seven_profit_giveback'
    assert confirmed_ah_reason(47) == 'strategy_forty_seven_confirmed_ah_failure'
    assert liquidity_fade_reason(47) == 'strategy_forty_seven_liquidity_fade_failure'
    assert numbered_fixed_strategy(47).early_original_risk_policy == child.EARLY_FAILURE_POLICY


def test47_management_accepts_declared_quarter_ah_extension():
    import asyncio
    from unittest.mock import AsyncMock
    from test_strategy_forty_two_management import prepared_manager
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.strategy_followthrough_exit import validate_witness
    manager, witness, financial, rows = prepared_manager(47)
    manager.contract = numbered_fixed_strategy(47)
    key = (financial.account_id, financial.assignment_id, financial.ticker)
    source = manager._submitted[key]
    boundary = witness.completed_five_second_boundary_ms
    manager._first_held_boundaries[key] = boundary - 30_000
    price_int = int((3 * source.reference_ask + source.initial_stop) / 4 * 10_000) - 1
    frame = rows(boundary, completed=True, age=48)
    frame[5000].update(close_int=price_int, macd_line=.01, macd_signal=.02)
    evidence = asyncio.run(manager.evidence.management_evidence(financial.ticker, frame, boundary_ms=boundary))
    manager.evidence.management_evidence = AsyncMock(return_value=replace(
        evidence, bid=price_int / 10_000, ask=price_int / 10_000 + .01))
    asyncio.run(manager.on_management(financial, frame, boundary))
    manager.runtime.submit_followthrough_failure.assert_awaited_once()
    _, actual, entry_id = manager.runtime.submit_followthrough_failure.await_args.args
    validate_witness(actual, strategy_number=47)
    assert entry_id == manager.runtime._strategy_one_entry_intent.return_value.intent_id
