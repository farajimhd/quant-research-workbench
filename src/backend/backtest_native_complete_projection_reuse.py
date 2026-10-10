"""Reuse complete preflight proof under fresh full installed source checks.

The registered source certifier still checks its entire approved inventory and
retained parent pins before and after lookup. This module issues no entry,
journal, cash, execution or recovery authority.
"""
from .backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from .backtest_fixed_structural_lot_native_v5 import verify_current_installed_source
from .historical_runtime_versions import fixed_strategy_one_runtime_version_check
from src.trading_runtime.declared_native_manifest import registered_manifest_authority
from src.trading_runtime.fixed_lot_management_native_preparation import uses_management_native_preparation
from src.trading_runtime.strategy_registry import numbered_strategy
from src.trading_runtime.journal_contract import canonical_json


def _proof(value):
    if (type(value) is not str or len(value) != 64
            or any(character not in '0123456789abcdef' for character in value)):
        raise ValueError('Complete native source proof is invalid')
    return value


def _authority_code(authority):
    callbacks = (authority.derive, authority.verify_manifest,
                 authority.certify_source, authority.parent_release_factory)
    snapshot = tuple((callback, getattr(callback, '__code__', None)) for callback in callbacks)
    if any(code is None for _, code in snapshot):
        raise ValueError('Native manifest requires source-owned Python callbacks')
    return snapshot


def load_complete_installed_projection(own):
    """Keep full source certification independent of the backend-only memo key."""
    if type(own) is not CertifiedStrategyOneConfiguration:
        raise ValueError('Complete installed configuration certificate required')
    number = own.strategy_number
    release = numbered_strategy(number)
    if not uses_management_native_preparation(release):
        raise ValueError('Complete native proof reuse requires declared preparation')
    authority = registered_manifest_authority(number)
    if authority is None:
        raise ValueError('Complete native proof reuse lacks registered source authority')
    strategy = own.payload['strategy']
    if (strategy['numbered_release']['contract'] != release.canonical_payload()
            or strategy['strategy_id'] != release.executor_strategy_id
            or strategy['revision'] != release.executor_revision):
        raise ValueError('Complete native proof reuse crosses installed release')
    image = canonical_json(own.payload)
    codes = _authority_code(authority)
    verify_current_installed_source(own)
    fresh_source_proof = _proof(authority.certify_source())
    check = fixed_strategy_one_runtime_version_check(own.payload)
    evidence = check.get('evidence', {})
    if (check.get('status') != 'ready'
            or evidence.get('strategy_id') != release.executor_strategy_id
            or evidence.get('strategy_revision') != release.executor_revision
            or evidence.get('backend_source_fingerprint')
                != strategy['numbered_release']['approved_code_fingerprint']):
        raise ValueError('Complete installed projection preflight is not ready')
    proof = _proof(evidence.get('projection_certificate'))
    # Recheck full coverage, not only src/backend + src/trading_runtime.
    if _proof(authority.certify_source()) != fresh_source_proof:
        raise ValueError('Complete approved source changed during native proof lookup')
    verify_current_installed_source(own)
    if (canonical_json(own.payload) != image or numbered_strategy(number) != release
            or registered_manifest_authority(number) != authority
            or _authority_code(authority) != codes):
        raise ValueError('Installed source or declaration changed during native proof lookup')
    return proof
