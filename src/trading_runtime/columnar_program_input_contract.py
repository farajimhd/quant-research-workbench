"""Session-portable program schema pins; source-day evidence stays independent.

This revision identifies a selected producer contract, never a market-data
snapshot. Every consumed day still needs its exact cold-certified history pin.
"""
from collections.abc import Mapping

from src.market_engine import columnar_history_product as c
from .columnar_rule_program import InputField


def portable_revision(fields, producer_code_hash, *, volume_window_seconds):
    """Hash canonical shared IDs/units and producer semantics without day lineage."""
    c.selected_catalog(fields)
    c.require_hash(producer_code_hash)
    if not fields or type(volume_window_seconds) is not int or not 1 <= volume_window_seconds <= 3600:
        raise ValueError("Program input contract requires fields and an explicit volume window")
    return c.content_hash(dict(schema_version=1,
        contract="columnar-program-selected-input-v1", algorithm=c.REVISION,
        fields=[(f.field_id, f.name, f.unit) for f in fields],
        producer_code_hash=producer_code_hash, volume_window_seconds=volume_window_seconds,
        resolution_ms=1000, value_type="Float32"))


def validate_program_input_contract(fields, pin):
    """Accept a legacy exact-day pin or the exact selected producer contract.

    CertifiedHistoryPin independently verifies product revision, source parents,
    field identities and coverage hashes. Portable matching never replaces it.
    """
    from src.backend.qmd_columnar_history import CertifiedHistoryPin
    if type(pin) is not CertifiedHistoryPin or not isinstance(fields, Mapping) or not fields:
        raise ValueError("Program inputs require a cold-certified history pin and declared fields")
    catalog = {f.name: f for f in pin.fields}
    selected = []
    for name, field in fields.items():
        if (type(field) is not InputField or field.name != name or name not in catalog
                or field.unit != catalog[name].unit
                or field.product != "q_live.qmd_causal_history_value_v1"
                or field.resolution_ms != 1000):
            raise ValueError("Program field differs from its producer-owned history contract")
        selected.append(catalog[name])
    revision = portable_revision(tuple(sorted(selected, key=lambda f: f.field_id)),
        pin.parents.producer_code_hash, volume_window_seconds=pin.parents.volume_window_seconds)
    if any(field.revision not in (pin.scope.product_revision, revision) for field in fields.values()):
        raise ValueError("Program field differs from its producer-owned history contract revision")
