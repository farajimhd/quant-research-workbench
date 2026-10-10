"""Real manifest/compiler/loader/memo path with controlled source/SQL seams.

These tests do not approve a source seal, market coverage or financial run.
"""
from dataclasses import replace

import pytest

from tests.test_fixed_lot_management_native_preparation import certified, exact_parent, current_approval
from src.backend import backtest_native_complete_projection_reuse as reuse
from src.backend import backtest_fixed_structural_lot_native_v20 as native
from src.backend import backtest_fixed_v4_certification as certification
from src.backend import historical_runtime_versions as versions
from src.trading_runtime import strategy_registry as registry
from src.trading_runtime.strategy_one_hundred_nine_release import derive_strategy_one_hundred_nine_configuration
from src.trading_runtime.strategy_one_hundred_ten_release import derive_strategy_one_hundred_ten_configuration


@pytest.fixture(params=(derive_strategy_one_hundred_nine_configuration,
                        derive_strategy_one_hundred_ten_configuration))
def installed(monkeypatch, request):
    parent = exact_parent()
    approval = dict(current_approval(), approved_code_fingerprint=versions.LOADED_BACKEND_FINGERPRINT)
    own = certified(request.param(parent, **approval))
    calls = []
    def source_certifier():
        calls.append('full_source')
        return 'c' * 64
    def projection(number):
        calls.append(('complete_projection', number))
        return 'd' * 64
    def current_source(value):
        assert value is own
        calls.append('installed_source')
    key = own.payload['strategy']['strategy_id'], own.strategy_number
    registered = registry._FIXED_REGISTRY[key]
    authority = replace(registered.manifest_authority, certify_source=source_certifier)
    monkeypatch.setitem(registry._FIXED_REGISTRY, key, replace(registered, manifest_authority=authority))
    monkeypatch.setattr(reuse, 'verify_current_installed_source', current_source)
    monkeypatch.setattr(native, 'verify_current_installed_source', current_source)
    monkeypatch.setattr(native, 'certify_numbered_configuration', lambda client, number: own)
    monkeypatch.setattr(versions, 'backend_source_fingerprint', lambda: versions.LOADED_BACKEND_FINGERPRINT)
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', projection)
    versions._numbered_projection_for_code.cache_clear()
    yield parent, own, calls, authority
    versions._numbered_projection_for_code.cache_clear()


def test_public_native_loader_reuses_actual_complete_preflight_memo(installed):
    parent, own, calls, _ = installed
    check = versions.fixed_strategy_one_runtime_version_check(own.payload)
    assert check['status'] == 'ready', check['summary']
    loaded, policy, proof = native.load_installed_configuration(object(), number=own.strategy_number, parent=parent)
    assert loaded is own
    assert policy == registry.fixed_strategy_executor(
        own.payload['strategy']['strategy_id'], own.strategy_number).contract_factory().fixed_structural_lot_policy
    assert proof == check['evidence']['projection_certificate'] == 'd' * 64
    assert calls.count(('complete_projection', own.strategy_number)) == 1
    assert calls.count('full_source') == 2
    assert calls.count('installed_source') == 3


def test_cold_path_still_executes_complete_projection_and_fresh_full_source(installed):
    _, own, calls, _ = installed
    assert reuse.load_complete_installed_projection(own) == 'd' * 64
    assert calls == ['installed_source', 'full_source',
        ('complete_projection', own.strategy_number), 'full_source', 'installed_source']


def change_source_callback(monkeypatch, own, authority, callback):
    key = own.payload['strategy']['strategy_id'], own.strategy_number
    registered = registry._FIXED_REGISTRY[key]
    monkeypatch.setitem(registry._FIXED_REGISTRY, key,
        replace(registered, manifest_authority=replace(authority, certify_source=callback)))


def test_full_source_change_is_rejected_even_with_warm_parent_proof(installed, monkeypatch):
    _, own, calls, authority = installed
    assert versions.fixed_strategy_one_runtime_version_check(own.payload)['status'] == 'ready'
    states = iter(('c' * 64, 'e' * 64))
    def changed():
        return next(states)
    change_source_callback(monkeypatch, own, authority, changed)
    with pytest.raises(ValueError, match='approved source changed'):
        reuse.load_complete_installed_projection(own)
    assert calls.count(('complete_projection', own.strategy_number)) == 1


def test_missing_complete_source_rejects_before_cached_lookup(installed, monkeypatch):
    _, own, calls, authority = installed
    assert versions.fixed_strategy_one_runtime_version_check(own.payload)['status'] == 'ready'
    def missing():
        raise ValueError('required non-backend dependency missing')
    change_source_callback(monkeypatch, own, authority, missing)
    with pytest.raises(ValueError, match='non-backend dependency missing'):
        reuse.load_complete_installed_projection(own)
    assert calls.count(('complete_projection', own.strategy_number)) == 1


def test_authority_code_mutation_during_lookup_is_rejected(installed, monkeypatch):
    _, own, calls, authority = installed
    callback = authority.certify_source
    previous_code = callback.__code__
    def changed_source():
        calls.append('full_source')
        return 'c' * 64
    def projection(number):
        callback.__code__ = changed_source.__code__
        return 'd' * 64
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', projection)
    try:
        with pytest.raises(ValueError, match='declaration changed'):
            reuse.load_complete_installed_projection(own)
    finally:
        callback.__code__ = previous_code


def test_installed_tree_mutation_during_lookup_is_rejected(installed, monkeypatch):
    _, own, _, _ = installed
    def projection(number):
        own.payload['strategy']['parameters']['execution']['tick_size'] = .02
        return 'd' * 64
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', projection)
    with pytest.raises(ValueError, match='declaration changed'):
        reuse.load_complete_installed_projection(own)


def test_projection_failure_cannot_become_native_authority(installed, monkeypatch):
    _, own, _, _ = installed
    def incomplete(number):
        raise ValueError('complete parent proof absent')
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', incomplete)
    with pytest.raises(ValueError, match='preflight is not ready'):
        reuse.load_complete_installed_projection(own)


def test_invalid_complete_projection_digest_is_rejected(installed, monkeypatch):
    _, own, _, _ = installed
    monkeypatch.setattr(certification, 'certify_numbered_fixed_v4_projection', lambda number: 'invalid')
    with pytest.raises(ValueError, match='proof is invalid'):
        reuse.load_complete_installed_projection(own)


def test_caller_payload_is_not_an_installed_certificate():
    with pytest.raises(ValueError, match='configuration certificate required'):
        reuse.load_complete_installed_projection({'strategy_number': 109})
