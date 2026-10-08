import json
from struct import pack

import pytest

from src.trading_runtime.exact_configuration_tree_cache import ExactConfigurationTreeCache
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes


def cache(**changes):
    args = dict(max_entries=2, max_input_bytes=100000, max_rows=1000, max_bytes=1000000)
    return ExactConfigurationTreeCache(**{**args, **changes})


def test_complete_real_tree_encoding_and_owned_immutable_hit():
    payload = {'array': [None, True, 1, 1.0, -0.0, 'é😀'], 'nested': {'x': 2}}
    text = canonical_json(payload)
    reuse = cache()
    rows = reuse.encode(text)
    assert tuple(dict(row) for row in rows) == encode_nodes(payload)
    assert reuse.encode(text) is rows
    payload['array'][0] = 'changed'
    assert rows[2]['value_kind'] == 'null'
    with pytest.raises(TypeError):
        rows[0]['node_id'] = 9
    assert reuse.statistics()['hits'] == 1


def test_types_and_float_bits_do_not_share_an_entry():
    reuse = cache()
    results = [reuse.encode(canonical_json({'v': v}))[1]
               for v in (True, 1, 1.0, 0.0, -0.0)]
    assert [row['value_kind'] for row in results[:3]] == ['bool', 'int', 'float']
    assert pack('!d', results[3]['float_value']) != pack('!d', results[4]['float_value'])
    assert reuse.statistics()['hits'] == 0


@pytest.mark.parametrize('text', ['{"x":1, "y":2}', '{"x":1,"x":2}',
                                  '{"x":NaN}', '{"x":9223372036854775808}', '[]'])
def test_invalid_or_noncanonical_inputs_never_publish(text):
    reuse = cache()
    with pytest.raises((ValueError, TypeError)):
        reuse.encode(text)
    assert reuse.statistics()['entries'] == 0


def test_lru_and_each_declared_bound_preserve_full_uncached_output():
    reuse = ExactConfigurationTreeCache(max_entries=1, max_input_bytes=100000,
                                       max_rows=1000, max_bytes=1000000)
    for value in (1, 2, 1):
        reuse.encode(canonical_json({'v': value}))
    assert reuse.statistics()['misses'] == 3
    assert reuse.statistics()['entries'] == 1
    for bound in ('max_input_bytes', 'max_rows', 'max_bytes'):
        args = dict(max_entries=2, max_input_bytes=100000, max_rows=1000, max_bytes=1000000)
        args[bound] = 1
        limited = ExactConfigurationTreeCache(**args)
        text = canonical_json({'v': 1})
        assert tuple(dict(row) for row in limited.encode(text)) == encode_nodes(json.loads(text))
        assert limited.statistics()['entries'] == limited.statistics()['retained_bytes'] == 0


def test_retained_byte_budget_evicts_without_skipping_encoding():
    first = canonical_json({'v': 1})
    reference = cache()
    reference.encode(first)
    size = reference.statistics()['retained_bytes']
    limited = cache(max_bytes=size + size // 2)
    for value in (1, 2, 1):
        assert tuple(dict(row) for row in limited.encode(canonical_json({'v': value}))) == encode_nodes({'v': value})
        assert limited.statistics()['retained_bytes'] <= size + size // 2
    assert limited.statistics()['entries'] == 1
    assert limited.statistics()['misses'] == 3


@pytest.mark.parametrize('invalid', [False, 0, -1, 1.0, None])
def test_explicit_bounds_cannot_be_coerced(invalid):
    with pytest.raises(ValueError):
        ExactConfigurationTreeCache(max_entries=invalid, max_input_bytes=1, max_rows=1, max_bytes=1)
