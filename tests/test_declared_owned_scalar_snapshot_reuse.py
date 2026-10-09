from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from tests.test_fixed_structural_lot_entry_v4 import packet
from src.trading_runtime.declared_owned_scalar_snapshot_reuse import declared_owned_scalar_caches, _CACHES
from src.trading_runtime.strategy_ninety_five_release import release_contract, OWNED_SNAPSHOT_POLICY
from src.trading_runtime.journal_contract import canonical_json
from src.backend.backtest_fixed_structural_lot_source import derive_fixed_structural_lot_configuration


def test_unselected_source_has_no_ownership_family(monkeypatch):
    source, _, journal, _, _ = packet(monkeypatch)
    try:
        assert declared_owned_scalar_caches(source) is None
        assert source not in _CACHES
    finally:
        journal.close()


def test_resealed_unissued_source_cannot_enable_owned_snapshots(monkeypatch):
    source, _, journal, _, _ = packet(monkeypatch)
    try:
        own = deepcopy(source.parent_payload)
        release = release_contract()
        own['strategy'].update(strategy_number=release.number, revision=release.number,
            numbered_release={'contract': release.canonical_payload()})
        own['strategy']['parameters']['owned_scalar_snapshot_policy'] = OWNED_SNAPSHOT_POLICY.payload()
        selected = canonical_json(derive_fixed_structural_lot_configuration(
            source.parent_payload, source.policy, installed_configuration=own))
        forged = replace(source, installed_json=canonical_json(own), selected_json=selected,
            selected_configuration_hash=sha256(selected.encode()).hexdigest())
        with pytest.raises(ValueError, match='issued|altered'):
            declared_owned_scalar_caches(forged)
        assert forged not in _CACHES
    finally:
        journal.close()


def test_actual_projection_seam_uses_owned_keys_without_skipping_request_checks(monkeypatch):
    from src.trading_runtime import declared_projected_configuration_reuse as projection_selection
    from src.trading_runtime import declared_packet_validation_reuse as packet_selection
    from src.trading_runtime.owned_scalar_row_snapshots import OwnedScalarRowSnapshots, OwnedScalarPacketValidationCache
    from src.trading_runtime.owned_projected_configuration_nodes import OwnedProjectedConfigurationNodeCache
    from src.trading_runtime.fixed_structural_lot_entry_v4 import (
        FixedStructuralLotEntryRows, project_fixed_structural_lot_entry, _exact,
    )
    source, request, journal, record, unit = packet(monkeypatch)
    owner = OwnedScalarRowSnapshots(max_entries=2, max_rows=30033, max_bytes=67108864)
    projection = OwnedProjectedConfigurationNodeCache(ownership=owner, max_entries=2,
        max_input_bytes=1048576, max_rows=30000, max_bytes=67108864)
    validation = OwnedScalarPacketValidationCache(FixedStructuralLotEntryRows._validate_content,
        ownership=owner, packet_type=FixedStructuralLotEntryRows, row_fields=('root', 'lots', 'nodes'),
        max_entries=2, max_rows=30033, max_bytes=67108864)
    # Synthetic pure seam only: no installed source, validator, cash or order authority replaced.
    monkeypatch.setattr(projection_selection, 'declared_projected_configuration_cache', lambda _: projection)
    token = packet_selection._ACTIVE.set(validation)
    try:
        first = project_fixed_structural_lot_entry(record, unit.base, request)
        second = project_fixed_structural_lot_entry(record, unit.base, request)
        assert _exact(first, unit.packet) and _exact(second, unit.packet)
        assert first.nodes is second.nodes and owner.snapshot(first.nodes) is not None
        assert validation.statistics()['hits'] == 1
        with pytest.raises(ValueError, match='differs'):
            project_fixed_structural_lot_entry(replace(record, run_id='foreign'), unit.base, request)
        assert validation.statistics()['hits'] == 1
    finally:
        packet_selection._ACTIVE.reset(token)
        journal.close()
