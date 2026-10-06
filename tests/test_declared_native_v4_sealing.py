"""Shared sealer preserves own entity identity and requires complete units."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_arte_declared_native_management_v4 import packet, case
from src.trading_runtime.arte_declared_native_v4_unit import DeclaredNativeV4Unit
from src.trading_runtime.arte_journal_writer import _sealed_families


def test_entry_sealing_keeps_own_event_and_existing_semantic_rows(case):
    unit = DeclaredNativeV4Unit(case.packet)
    with pytest.raises(ValueError, match='no typed contract'):
        _sealed_families(unit.base)
    sealed = dict(_sealed_families(unit.base, declared_unit=unit))
    assert sealed['trading_event_v1'][0]['entity_type'] == 'declared_native_intent'
    assert sealed['trading_strategy_intent_v1'][0]['intent_id'] == case.submission.intent.intent_id
    assert len(sealed['trading_intent_protection_slice_v1']) == len(unit.base.intent_slices)


def test_management_sealing_retains_every_original_request_in_order(packet):
    unit = DeclaredNativeV4Unit(packet.packet)
    sealed = dict(_sealed_families(unit.base, declared_unit=unit))
    assert tuple(row['entity_type'] for row in sealed['trading_event_v1']) == (
        'declared_native_management_intent',) * len(packet.submission.intents)
    assert tuple(row['intent_id'] for row in sealed['trading_strategy_intent_v1']) == tuple(
        intent.intent_id for intent in packet.submission.intents)
    assert unit.base.first_sequence == packet.packet.bases[0].first_sequence
    assert unit.base.last_sequence == packet.packet.bases[-1].last_sequence
    with pytest.raises(ValueError, match='no typed contract'):
        _sealed_families(unit.base)


@pytest.mark.parametrize('change', ['proxy', 'clone'])
def test_sealer_rejects_foreign_unit_or_detached_base(case, change):
    unit = DeclaredNativeV4Unit(case.packet)
    source = SimpleNamespace(base=unit.base, packet=unit.packet) if change == 'proxy' else unit
    base = unit.base if change == 'proxy' else replace(unit.base)
    with pytest.raises(ValueError, match='complete exact native unit'):
        _sealed_families(base, declared_unit=source)
