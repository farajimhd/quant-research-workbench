"""Operation-bound pure validation scope, selected only by an issued source."""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from weakref import WeakKeyDictionary

from .exact_scalar_packet_validation_cache import ExactScalarPacketValidationCache
from .packet_validation_reuse_policy import (
    INPUT, RULE, declared_packet_validation_reuse_policy,
)

_ACTIVE = ContextVar('declared_pure_packet_validator', default=None)
_CACHES = WeakKeyDictionary()
_LOCK = RLock()


def _source_cache(source):
    if not source.installed_json:
        return None
    from .declared_owned_scalar_snapshot_reuse import declared_owned_scalar_caches
    owned = declared_owned_scalar_caches(source)
    if owned is not None:
        return owned.validation
    strategy = source.installed_payload['strategy']
    manifest = strategy.get('numbered_release', {})
    contract = manifest.get('contract', {})
    claimed = ('packet_validation_reuse_policy' in strategy['parameters'] or
               INPUT in contract.get('input_contracts', ()) or
               RULE in contract.get('rule_set_contracts', ()))
    if not claimed:
        return None
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    from .fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
    from .fixed_structural_lot_entry_v4 import FixedStructuralLotEntryRows
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if manifest.get('contract') != release.canonical_payload():
        raise ValueError('Packet reuse differs from issued installed release')
    policy = declared_packet_validation_reuse_policy(release,
        strategy['parameters'].get('packet_validation_reuse_policy'))
    factory = fixed_strategy_executor(release.executor_strategy_id,
                                     release.executor_revision).contract_factory()
    require_declared_fixed_structural_lot_contract(factory,release)
    if factory.validation_reuse_policy != policy:
        raise ValueError('Packet reuse differs from installed typed factory')
    factory.__post_init__()
    with _LOCK:
        binding = _CACHES.get(source)
        if binding is None:
            cache = ExactScalarPacketValidationCache(FixedStructuralLotEntryRows._validate_content,
                packet_type=FixedStructuralLotEntryRows, row_fields=('root', 'lots', 'nodes'),
                max_entries=policy.max_entries, max_rows=policy.max_rows, max_bytes=policy.max_bytes)
            _CACHES[source] = (source.installed_json, policy, cache)
        else:
            installed_json, expected, cache = binding
            if installed_json != source.installed_json or expected != policy:
                raise ValueError('Issued packet reuse source binding changed')
        return cache


@contextmanager
def declared_packet_validation_scope(source):
    # Explicitly set None for an unselected nested source, preventing inherited
    # thread/task scope from enabling reuse for another operation.
    token = _ACTIVE.set(_source_cache(source))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def validate_selected_packet(packet):
    cache = _ACTIVE.get()
    if cache is None:
        return False
    cache.validate(packet)
    return True
