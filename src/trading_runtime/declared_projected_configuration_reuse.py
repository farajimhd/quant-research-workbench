"""Issued-source selection of bounded pure configuration-node projection."""
from threading import RLock
from weakref import WeakKeyDictionary

from .exact_projected_configuration_nodes import ExactProjectedConfigurationNodeCache
from .projected_configuration_reuse_policy import (
    INPUT, RULE, declared_projected_configuration_reuse_policy,
)

_CACHES = WeakKeyDictionary()
_LOCK = RLock()


def declared_projected_configuration_cache(source):
    if not source.installed_json:
        return None
    from .declared_owned_scalar_snapshot_reuse import declared_owned_scalar_caches
    owned = declared_owned_scalar_caches(source)
    if owned is not None:
        return owned.projection
    strategy = source.installed_payload['strategy']
    manifest = strategy.get('numbered_release', {})
    declaration = manifest.get('contract', {})
    claimed = ('projected_configuration_reuse_policy' in strategy['parameters']
               or INPUT in declaration.get('input_contracts', ())
               or RULE in declaration.get('rule_set_contracts', ()))
    if not claimed:
        return None
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    from .fixed_structural_lot_projection_reuse_contract import FixedStructuralLotProjectionReuseStrategyContract
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if declaration != release.canonical_payload():
        raise ValueError('Projected-node reuse differs from issued installed release')
    policy = declared_projected_configuration_reuse_policy(release,
        strategy['parameters'].get('projected_configuration_reuse_policy'))
    factory = fixed_strategy_executor(release.executor_strategy_id,
        release.executor_revision).contract_factory()
    if (type(factory) is not FixedStructuralLotProjectionReuseStrategyContract
            or factory.release != release or factory.projection_reuse_policy != policy):
        raise ValueError('Projected-node reuse differs from installed typed factory')
    factory.__post_init__()
    with _LOCK:
        binding = _CACHES.get(source)
        if binding is None:
            cache = ExactProjectedConfigurationNodeCache(max_entries=policy.max_entries,
                max_input_bytes=policy.max_input_bytes, max_rows=policy.max_rows,
                max_bytes=policy.max_bytes)
            _CACHES[source] = (source.installed_json, policy, cache)
        else:
            installed_json, expected, cache = binding
            if installed_json != source.installed_json or expected != policy:
                raise ValueError('Issued projected-node reuse binding changed')
        return cache
