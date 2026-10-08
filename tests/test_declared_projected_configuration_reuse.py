"""Pure projection seam tests, never financial/source admission evidence."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from tests.test_fixed_structural_lot_entry_v4 import packet
from tests.test_fixed_structural_lot_native import cert
from tests.test_projected_configuration_reuse_release import prepared
from src.trading_runtime import declared_projected_configuration_reuse as reuse
from src.trading_runtime.exact_projected_configuration_nodes import ExactProjectedConfigurationNodeCache
from src.trading_runtime.fixed_structural_lot_entry_v4 import project_fixed_structural_lot_entry, _exact
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_ninety_four_release import release_contract, PROJECTION_REUSE_POLICY
from src.backend.backtest_fixed_structural_lot_source import derive_fixed_structural_lot_configuration


def test_unselected_issued_source_does_not_select_projection_reuse(monkeypatch):
    source, _, journal, _, _ = packet(monkeypatch)
    try:
        assert reuse.declared_projected_configuration_cache(source) is None
        assert source not in reuse._CACHES
    finally:
        journal.close()


def test_unissued_resealed_source_cannot_enable_declared_projection_reuse(monkeypatch):
    source, _, journal, _, _ = packet(monkeypatch)
    try:
        own = deepcopy(source.parent_payload)
        own['strategy'].update(strategy_number=94, revision=94,
            numbered_release={'contract': release_contract().canonical_payload()})
        own['strategy']['parameters']['projected_configuration_reuse_policy'] = PROJECTION_REUSE_POLICY.payload()
        selected = canonical_json(derive_fixed_structural_lot_configuration(
            source.parent_payload, source.policy, installed_configuration=own))
        forged = replace(source, installed_json=canonical_json(own), selected_json=selected,
            selected_configuration_hash=sha256(selected.encode()).hexdigest())
        with pytest.raises(ValueError, match='issued|altered'):
            reuse.declared_projected_configuration_cache(forged)
        assert forged not in reuse._CACHES
    finally:
        journal.close()


def test_original_projection_seam_preserves_exact_rows_and_semantic_checks(monkeypatch):
    source, request, journal, record, unit = packet(monkeypatch)
    selected = ExactProjectedConfigurationNodeCache(max_entries=2, max_input_bytes=1048576,
        max_rows=30000, max_bytes=67108864)
    # Replace only the pure selection seam on a synthetic prepared-only fixture.
    # No installed capability, validator, financial profile or source proof is issued.
    monkeypatch.setattr(reuse, 'declared_projected_configuration_cache', lambda _: selected)
    try:
        first = project_fixed_structural_lot_entry(record, unit.base, request)
        second = project_fixed_structural_lot_entry(record, unit.base, request)
        assert _exact(first, unit.packet) and _exact(second, unit.packet)
        assert first.nodes is second.nodes
        assert selected.statistics()['hits'] == 1
        with pytest.raises(ValueError, match='differs'):
            project_fixed_structural_lot_entry(replace(record, run_id='foreign'), unit.base, request)
        assert selected.statistics()['hits'] == 1
    finally:
        journal.close()


def test_native_v15_complete_semantic_verifier_retains_all_three_policies():
    from src.backend.backtest_fixed_structural_lot_native_v15 import verify_installed_configuration
    from src.trading_runtime.strategy_ninety_four_release import VALIDATION_REUSE_POLICY
    from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
    parent, kwargs, result = prepared()
    assert verify_installed_configuration(parent, cert(result['payload']), kwargs['release'],
        kwargs['parent_release']) == (FixedStructuralLotPolicy(), VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY)


def test_unapproved_v15_complete_source_inventory_remains_closed():
    from src.backend.backtest_fixed_structural_lot_certification_v15 import certify_fixed_structural_lot_source
    with pytest.raises(ValueError, match='unapproved'):
        certify_fixed_structural_lot_source()
