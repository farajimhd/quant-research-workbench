from dataclasses import dataclass
from types import MappingProxyType

import pytest

from src.trading_runtime.exact_scalar_packet_validation_cache import ExactScalarPacketValidationCache
from src.trading_runtime.owned_scalar_row_snapshots import (
    OwnedScalarRowSnapshots, OwnedScalarPacketValidationCache,
)


@dataclass(frozen=True)
class Packet:
    root: object
    rows: object


def caches(validator=lambda packet: None, *, entries=2, size=100000):
    owner = OwnedScalarRowSnapshots(max_entries=entries, max_rows=20, max_bytes=size)
    bounds = dict(packet_type=Packet, row_fields=('root', 'rows'), max_entries=2,
                  max_rows=20, max_bytes=100000)
    return owner, ExactScalarPacketValidationCache(validator, **bounds), OwnedScalarPacketValidationCache(
        validator, ownership=owner, **bounds)


@pytest.mark.parametrize('value', [None, False, True, 0, -1, 2**90, 0., -0.,
                                  float('inf'), float('-inf'), float('nan'), 'é😀'])
def test_complete_keys_match_and_original_aliases_cannot_change_owned_rows(value):
    owner, old, new = caches()
    backing = {'b': value, 'a': 1}
    rows = owner.own_rows((MappingProxyType(backing),))
    packet = Packet(MappingProxyType({'root': True}), rows)
    assert old._snapshot(packet) == new._snapshot(packet)
    before = new._snapshot(packet)
    backing['a'] = 99
    assert rows[0]['a'] == 1
    assert before == new._snapshot(packet)
    with pytest.raises(TypeError):
        rows[0]['a'] = 3


def test_packet_replacement_is_revalidated_and_unowned_alias_mutation_detected():
    calls = []
    owner, old, new = caches(lambda packet: calls.append(packet))
    packet = Packet(MappingProxyType({'root': 1}), owner.own_rows((MappingProxyType({'x': 1}),)))
    new.validate(packet)
    new.validate(packet)
    assert len(calls) == 1
    backing = {'x': 2}
    object.__setattr__(packet, 'rows', (MappingProxyType(backing),))
    new.validate(packet)
    assert len(calls) == 2
    owner, old, mutating = caches(lambda packet: backing.update(x=3))
    with pytest.raises(ValueError, match='changed during validation'):
        mutating.validate(packet)
    assert mutating.statistics()['entries'] == 0


def test_eviction_and_byte_bypass_preserve_complete_keys():
    owner, old, new = caches(entries=1)
    rows = owner.own_rows((MappingProxyType({'x': 1}),))
    second = owner.own_rows((MappingProxyType({'x': 2}),))
    assert owner.snapshot(rows) is None
    assert owner.snapshot(second) is not None
    packet = Packet(MappingProxyType({'r': 1}), rows)
    assert old._snapshot(packet) == new._snapshot(packet)
    owner, old, new = caches(size=1)
    rows = owner.own_rows((MappingProxyType({'x': 1}),))
    assert owner.snapshot(rows) is None
    assert owner.statistics()['entries'] == 0
    packet = Packet(MappingProxyType({'r': 1}), rows)
    assert old._snapshot(packet) == new._snapshot(packet)


def test_empty_groups_do_not_double_count_retention():
    owner, _, _ = caches()
    owner.own_rows(())
    before = owner.statistics()['retained_bytes']
    owner.own_rows(())
    assert owner.statistics()['retained_bytes'] == before


def test_unsupported_key_is_rejected_without_comparison():
    class Hostile:
        def __lt__(self, other):
            raise AssertionError('Unsupported key compared')
    owner, _, _ = caches()
    with pytest.raises(ValueError, match='scalar mapping'):
        owner.own_rows((MappingProxyType({'a': 1, Hostile(): 2}),))
