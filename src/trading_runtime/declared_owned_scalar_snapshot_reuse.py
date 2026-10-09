"""Issued-source-owned pure caches; no financial or source capability issued."""
from dataclasses import dataclass
from threading import RLock
from weakref import WeakKeyDictionary

from .owned_scalar_snapshot_policy import INPUT, RULE, declared_owned_scalar_snapshot_policy
from .packet_validation_reuse_policy import declared_packet_validation_reuse_policy
from .projected_configuration_reuse_policy import declared_projected_configuration_reuse_policy
from .owned_scalar_row_snapshots import OwnedScalarRowSnapshots, OwnedScalarPacketValidationCache
from .owned_projected_configuration_nodes import OwnedProjectedConfigurationNodeCache

_CACHES = WeakKeyDictionary()
_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class DeclaredOwnedScalarCaches:
    ownership: OwnedScalarRowSnapshots
    projection: OwnedProjectedConfigurationNodeCache
    validation: OwnedScalarPacketValidationCache


def declared_owned_scalar_caches(source):
    if not source.installed_json:
        return None
    strategy = source.installed_payload['strategy']
    parameters = strategy['parameters']
    manifest = strategy.get('numbered_release', {})
    declaration = manifest.get('contract', {})
    claimed = ('owned_scalar_snapshot_policy' in parameters
        or INPUT in declaration.get('input_contracts', ())
        or RULE in declaration.get('rule_set_contracts', ()))
    if not claimed:
        return None
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    from .fixed_structural_lot_owned_snapshot_contract import FixedStructuralLotOwnedSnapshotStrategyContract
    from .fixed_structural_lot_entry_v4 import FixedStructuralLotEntryRows
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    release = numbered_strategy(strategy['strategy_number'])
    if declaration != release.canonical_payload():
        raise ValueError('Owned scalar reuse differs from installed release')
    owned = declared_owned_scalar_snapshot_policy(release, parameters.get('owned_scalar_snapshot_policy'))
    packet = declared_packet_validation_reuse_policy(release, parameters.get('packet_validation_reuse_policy'))
    projection = declared_projected_configuration_reuse_policy(release,
        parameters.get('projected_configuration_reuse_policy'))
    factory = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).contract_factory()
    if (type(factory) is not FixedStructuralLotOwnedSnapshotStrategyContract
            or factory.release != release or factory.owned_snapshot_policy != owned
            or factory.validation_reuse_policy != packet or factory.projection_reuse_policy != projection):
        raise ValueError('Owned scalar reuse differs from exact typed factory')
    factory.__post_init__()
    policies = owned, packet, projection
    with _LOCK:
        binding = _CACHES.get(source)
        if binding is None:
            ownership = OwnedScalarRowSnapshots(max_entries=owned.max_entries,
                max_rows=owned.max_rows, max_bytes=owned.max_bytes)
            family = DeclaredOwnedScalarCaches(ownership,
                OwnedProjectedConfigurationNodeCache(ownership=ownership,
                    max_entries=projection.max_entries, max_input_bytes=projection.max_input_bytes,
                    max_rows=projection.max_rows, max_bytes=projection.max_bytes),
                OwnedScalarPacketValidationCache(FixedStructuralLotEntryRows._validate_content,
                    ownership=ownership, packet_type=FixedStructuralLotEntryRows,
                    row_fields=('root', 'lots', 'nodes'), max_entries=packet.max_entries,
                    max_rows=packet.max_rows, max_bytes=packet.max_bytes))
            _CACHES[source] = source.installed_json, policies, family
        else:
            installed_json, expected, family = binding
            if installed_json != source.installed_json or expected != policies:
                raise ValueError('Issued owned scalar cache binding changed')
        return family
