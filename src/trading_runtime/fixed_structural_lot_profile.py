"""Issued installed fixed-lot capability; constructor values are not authority."""
from dataclasses import dataclass
from threading import RLock
from weakref import WeakKeyDictionary

from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .fixed_structural_lot_entry_schema import TABLES as ENTRY_TABLES
from .fixed_structural_lot_snapshot import TABLES as SNAPSHOT_TABLES
from .fixed_structural_lot_manager_schema import TABLES as MANAGER_TABLES

_ISSUED = WeakKeyDictionary()
_LOCK = RLock()


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class FixedStructuralLotDeclaredProfile:
    operation: object


def _binding(operation):
    from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
    from src.backend.backtest_fixed_structural_lot_source import require_native_fixed_structural_lot_source
    if type(operation) is not NativeFixedStructuralLotOperation:
        raise ValueError('Fixed-lot profile requires exact native installed operation')
    source = operation.source
    require_native_fixed_structural_lot_source(source)
    source.require_installed_admission()
    if type(source.policy) is not FixedStructuralLotPolicy or not source.installed_json:
        raise ValueError('Fixed-lot profile requires installed typed policy')
    source.policy.__post_init__()
    return (source, source.installed_json, source.selected_configuration_hash,
            source.run_id, source.session_date, source.policy.payload())


def issue_fixed_structural_lot_profile(operation):
    binding = _binding(operation)
    profile = FixedStructuralLotDeclaredProfile(operation)
    with _LOCK:
        _ISSUED[profile] = binding
    return profile


def require_fixed_structural_lot_profile(profile):
    if type(profile) is not FixedStructuralLotDeclaredProfile:
        raise ValueError('Exact issued fixed-lot profile required')
    with _LOCK:
        binding = _ISSUED.get(profile)
    if binding is None or binding != _binding(profile.operation):
        raise ValueError('Fixed-lot profile was not issued or its installed binding changed')
    return profile


def selected_fixed_structural_lot_tables(profile):
    require_fixed_structural_lot_profile(profile)
    return (*ENTRY_TABLES, *SNAPSHOT_TABLES, *MANAGER_TABLES)


def require_fixed_structural_lot_client_context(client, context):
    """Bind a selected writer to the exact admitted operation/source envelope."""
    from .fixed_structural_lot_entry_v4 import FixedStructuralLotPublicationContext
    profile = require_fixed_structural_lot_profile(
        getattr(client, 'fixed_structural_lot_profile', None))
    if type(context) is not FixedStructuralLotPublicationContext:
        raise ValueError('Exact installed fixed-lot publication context required')
    context.verify_admission()
    source = profile.operation.source
    if (context.source is not source or context.base.run_id != source.run_id
            or context.unit.packet.root['selected_configuration_hash']
            != source.selected_configuration_hash):
        raise ValueError('Selected client differs from publication source/run/configuration')
    return profile
