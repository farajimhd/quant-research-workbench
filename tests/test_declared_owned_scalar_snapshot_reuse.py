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
