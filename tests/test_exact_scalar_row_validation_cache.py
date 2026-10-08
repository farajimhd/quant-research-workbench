from types import SimpleNamespace

import pytest

from src.trading_runtime.exact_scalar_row_validation_cache import ExactScalarRowValidationCache
from src.trading_runtime.fixed_structural_lot_entry_schema import NODE
from src.trading_runtime.fixed_structural_lot_entry_v4 import _check_row, _hash


def real_row():
    row = {}
    for name, kind in NODE.columns:
        if kind.startswith('Nullable('): value = None
        elif kind.startswith('UInt') or kind == 'Int64': value = 1
        elif kind == 'Float64': value = 1.0
        elif kind == 'UUID': value = '11111111-1111-4111-8111-111111111111'
        elif kind == 'Date': value = '2026-08-04'
        elif kind == 'FixedString(64)': value = 'a' * 64
        else: value = 'sample'
        row[name] = value
    row['content_hash'] = _hash({k: v for k, v in row.items() if k != 'content_hash'})
    return row


def test_real_validator_keeps_rejecting_mutated_rows_after_warm_hit():
    cache = ExactScalarRowValidationCache(_check_row)
    row = real_row()
    cache.validate(NODE, row)
    cache.validate(NODE, row)
    integer = next(n for n, k in NODE.columns if k.startswith('UInt'))
    uuid = next(n for n, k in NODE.columns if k == 'UUID')
    mutations = [dict(row, content_hash='b' * 64), dict(row, foreign=1),
                 {k: v for k, v in row.items() if k != uuid},
                 dict(row, **{integer: True}), dict(row, **{integer: 2 ** 64}),
                 dict(row, **{uuid: row[uuid].replace('-', '')}),
                 dict(row, **{uuid: '00000000-0000-0000-0000-000000000000'})]
    for changed in mutations:
        with pytest.raises(ValueError): _check_row(NODE, changed)
        with pytest.raises(ValueError): cache.validate(NODE, changed)
    assert cache.statistics()['hits'] == 1
    assert cache.statistics()['entries'] == 1


def test_exact_scalar_types_float_bits_and_schema_are_distinct():
    calls = []
    cache = ExactScalarRowValidationCache(lambda table, row: calls.append((table.columns, row)))
    table = SimpleNamespace(name='sample', columns=(('value', 'Float64'),))
    for value in (1, True, 1.0, 0.0, -0.0):
        cache.validate(table, {'value': value})
    cache.validate(table, {'value': -0.0})
    table.columns = (('value', 'Int64'),)
    cache.validate(table, {'value': -0.0})
    assert len(calls) == 6
    assert cache.statistics()['hits'] == 1


def test_capacity_and_oversized_rows_do_not_skip_validation():
    calls = []
    cache = ExactScalarRowValidationCache(lambda table, row: calls.append(row),
                                         max_entries=2, max_bytes=2048)
    table = SimpleNamespace(name='sample', columns=(('value', 'String'),))
    for value in ('a', 'b', 'c', 'a', 'x' * 4096, 'x' * 4096):
        cache.validate(table, {'value': value})
    assert len(calls) == 6
    assert cache.statistics()['entries'] <= 2
    assert cache.statistics()['retained_key_bytes'] <= 2048


def test_equal_content_keys_ignore_object_sharing_and_mapping_order():
    value = 'scalar-content-' * 40
    equal_value = value.encode().decode()
    assert value == equal_value and value is not equal_value
    calls = []
    cache = ExactScalarRowValidationCache(lambda table, row: calls.append(row))
    table = SimpleNamespace(name='sample', columns=(('a', 'String'), ('b', 'String')))
    cache.validate(table, {'a': value, 'b': value})
    cache.validate(table, {'b': equal_value, 'a': value})
    assert len(calls) == 1
    assert cache.statistics()['hits'] == 1


def test_mutation_during_validation_is_rejected_and_not_retained():
    table = SimpleNamespace(name='sample', columns=(('value', 'Int64'),))
    row = {'value': 1}
    def validate(_table, snapshot):
        assert snapshot['value'] == 1
        row['value'] = 2
    cache = ExactScalarRowValidationCache(validate)
    with pytest.raises(ValueError, match='changed during'):
        cache.validate(table, row)
    assert cache.statistics()['entries'] == 0
