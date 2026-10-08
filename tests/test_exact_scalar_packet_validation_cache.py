from dataclasses import dataclass
from types import MappingProxyType

import pytest

from src.trading_runtime.exact_scalar_packet_validation_cache import ExactScalarPacketValidationCache


@dataclass(frozen=True)
class Packet:
    root: object
    children: object


def packet(value=1.0):
    return Packet(MappingProxyType({'value': value}), (MappingProxyType({'child': 1}),))


def cache(validator, **overrides):
    args = dict(packet_type=Packet, row_fields=('root', 'children'),
                max_entries=2, max_rows=10, max_bytes=100000)
    return ExactScalarPacketValidationCache(validator, **{**args, **overrides})


def test_exact_repeated_packet_reuses_only_successful_pure_validation():
    calls = []
    reuse = cache(lambda p: calls.append(p))
    reuse.validate(packet())
    reuse.validate(packet())
    assert len(calls) == 1
    assert reuse.statistics()['hits'] == 1


@pytest.mark.parametrize('values', [(0.0, -0.0), (True, 1), (1, 1.0)])
def test_float_bits_and_scalar_types_are_distinct(values):
    calls = []
    reuse = cache(lambda p: calls.append(p))
    for value in values:
        reuse.validate(packet(value))
    assert len(calls) == 2


def test_backing_mapping_alias_mutation_cannot_reuse_prior_success():
    backing = {'value': 1.0}
    row = Packet(MappingProxyType(backing), ())
    def validate(p):
        if p.root['value'] != 1.0:
            raise ValueError('invalid content')
    reuse = cache(validate)
    reuse.validate(row)
    backing['value'] = 2.0
    with pytest.raises(ValueError, match='invalid content'):
        reuse.validate(row)
    assert reuse.statistics()['entries'] == 1


def test_mutation_during_validator_does_not_publish_a_cache_entry():
    backing = {'value': 1.0}
    row = Packet(MappingProxyType(backing), ())
    reuse = cache(lambda p: backing.update(value=2.0))
    with pytest.raises(ValueError, match='changed during'):
        reuse.validate(row)
    assert reuse.statistics()['entries'] == 0


def test_lru_and_retained_byte_bounds():
    calls = []
    reuse = cache(lambda p: calls.append(p), max_entries=1)
    for value in (1.0, 2.0, 1.0):
        reuse.validate(packet(value))
    assert len(calls) == 3
    assert reuse.statistics()['entries'] == 1
    tiny = cache(lambda p: None, max_bytes=1)
    tiny.validate(packet())
    assert tiny.statistics()['entries'] == 0
    assert tiny.statistics()['retained_key_bytes'] == 0


def test_unsupported_shapes_bypass_to_original_validator():
    calls = []
    reuse = cache(lambda p: calls.append(p), max_rows=1)
    rows = [packet(), Packet({'value': 1.0}, ()), Packet(MappingProxyType({'nested': []}), ())]
    for row in rows:
        reuse.validate(row)
    assert len(calls) == 3
    assert reuse.statistics()['bypasses'] == 3


def test_failed_validation_and_nonempty_return_are_not_cached():
    def fail(p):
        raise ValueError('reject')
    reuse = cache(fail)
    for _ in range(2):
        with pytest.raises(ValueError, match='reject'):
            reuse.validate(packet())
    assert reuse.statistics()['entries'] == 0
    with pytest.raises(ValueError, match='return None'):
        cache(lambda p: True).validate(packet())


def test_omitted_fields_and_reordered_children_cannot_reuse_validation():
    with pytest.raises(ValueError, match='Every frozen packet field'):
        cache(lambda p: None, row_fields=('root',))
    calls = []
    reuse = cache(lambda p: calls.append(p))
    a, b = MappingProxyType({'child': 1}), MappingProxyType({'child': 2})
    reuse.validate(Packet(MappingProxyType({}), (a, b)))
    reuse.validate(Packet(MappingProxyType({}), (b, a)))
    assert len(calls) == 2
