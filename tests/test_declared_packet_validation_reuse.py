"""Pure runtime hook controls; these fixtures confer no installed admission."""
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_fixed_structural_lot_entry_v4 import packet
from src.trading_runtime import declared_packet_validation_reuse as reuse
from src.trading_runtime.exact_scalar_packet_validation_cache import ExactScalarPacketValidationCache
from src.trading_runtime.fixed_structural_lot_entry_v4 import (
    FixedStructuralLotEntryRows, FixedStructuralLotPublicationContext,
)


def pure_cache():
    return ExactScalarPacketValidationCache(FixedStructuralLotEntryRows._validate_content,
        packet_type=FixedStructuralLotEntryRows, row_fields=('root', 'lots', 'nodes'),
        max_entries=2, max_rows=30033, max_bytes=64 * 1024 * 1024)


def test_real_packet_content_hook_reuses_success_without_altering_request(monkeypatch):
    source, request, journal, record, unit = packet(monkeypatch)
    cache = pure_cache()
    token = reuse._ACTIVE.set(cache)  # pure hook seam, not a source capability
    try:
        unit.packet.__post_init__()
        unit.packet.__post_init__()
        assert cache.statistics()['hits'] == 1
        # An unselected source resets this scope and executes the original
        # source-equivalence path; content reuse cannot admit that source.
        context = FixedStructuralLotPublicationContext(unit, record, source)
        actual = context.verify_source()
        assert actual.entry == request.entry and actual.intent == request.intent
        assert cache.statistics()['hits'] == 1
        with pytest.raises(ValueError, match='installed'):
            context.verify_admission()
    finally:
        reuse._ACTIVE.reset(token)
        journal.close()


def test_unselected_nested_scope_resets_and_restores_pure_hook(monkeypatch):
    source, _, journal, _, unit = packet(monkeypatch)
    cache = pure_cache()
    token = reuse._ACTIVE.set(cache)
    try:
        with reuse.declared_packet_validation_scope(source):
            assert reuse._ACTIVE.get() is None
            unit.packet.__post_init__()
        assert reuse._ACTIVE.get() is cache
        assert cache.statistics()['entries'] == 0
    finally:
        reuse._ACTIVE.reset(token)
        journal.close()


def test_resealed_unissued_source_cannot_enable_runtime_reuse(monkeypatch):
    from src.trading_runtime.strategy_ninety_three_release import release_contract, VALIDATION_REUSE_POLICY
    from src.trading_runtime.journal_contract import canonical_json
    from src.backend.backtest_fixed_structural_lot_source import derive_fixed_structural_lot_configuration
    from hashlib import sha256
    source, _, journal, _, _ = packet(monkeypatch)
    try:
        own = deepcopy(source.parent_payload)
        own['strategy'].update(strategy_number=93, revision=93,
            numbered_release={'contract': release_contract().canonical_payload()})
        own['strategy']['parameters']['packet_validation_reuse_policy'] = VALIDATION_REUSE_POLICY.payload()
        selected = canonical_json(derive_fixed_structural_lot_configuration(
            source.parent_payload, source.policy, installed_configuration=own))
        forged = replace(source, installed_json=canonical_json(own), selected_json=selected,
                         selected_configuration_hash=sha256(selected.encode()).hexdigest())
        with pytest.raises(ValueError, match='not issued'):
            with reuse.declared_packet_validation_scope(forged):
                pytest.fail('Unissued source entered a selected cache scope')
    finally:
        journal.close()
