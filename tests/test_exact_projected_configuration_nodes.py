from dataclasses import FrozenInstanceError
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

import pytest

from src.trading_runtime.exact_projected_configuration_nodes import ExactProjectedConfigurationNodeCache
from src.trading_runtime.fixed_structural_lot_entry_schema import NODE, TREE_KINDS
from src.trading_runtime.fixed_structural_lot_entry_v4 import _seal
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes


def common():
    return dict(parent_record_id='11111111-1111-4111-8111-111111111111',
        root_record_id='22222222-2222-4222-8222-222222222222', run_id='run',
        event_month='2026-08-01', batch_id='33333333-3333-4333-8333-333333333333',
        sequence=1)


def cache(**changes):
    bounds = dict(max_entries=2, max_input_bytes=100000, max_rows=100, max_bytes=1000000)
    bounds.update(changes)
    return ExactProjectedConfigurationNodeCache(**bounds)


def texts(value=1):
    return tuple(canonical_json(payload) for payload in ({'value': value}, {'values': [True, None]}, {}))


def test_matches_original_complete_projection_and_owns_immutable_rows():
    values = ({'value': -0.0}, {'values': [True, None, 'é']}, {})
    binding = common()
    expected = tuple(_seal(NODE, dict(binding,
        record_id=str(uuid5(NAMESPACE_URL, f'{binding["root_record_id"]}:{kind}:{row["node_id"]}')),
        tree_kind=kind, **row))
        for kind, payload in zip(TREE_KINDS, values) for row in encode_nodes(payload))
    selected = cache()
    arguments = (canonical_json(binding), tuple(map(canonical_json, values)))
    result = selected.project(*arguments)
    assert result.rows == expected
    assert result.counts == tuple(len(encode_nodes(payload)) for payload in values)
    assert selected.project(*arguments) is result
    assert all(type(row) is MappingProxyType for row in result.rows)
    with pytest.raises(TypeError):
        result.rows[0]['run_id'] = 'foreign'
    with pytest.raises(FrozenInstanceError):
        result.counts = ()
    assert selected.statistics()['hits'] == 1


@pytest.mark.parametrize('changed', [True, 1.0, -0.0, '1'])
def test_scalar_type_and_value_changes_do_not_hit(changed):
    selected = cache()
    binding = canonical_json(common())
    selected.project(binding, texts(1))
    selected.project(binding, texts(changed))
    assert selected.statistics()['hits'] == 0


def test_common_identity_and_signed_zero_are_part_of_complete_key():
    selected = cache()
    binding = common()
    selected.project(canonical_json(binding), texts(-0.0))
    selected.project(canonical_json(binding), texts(0.0))
    binding['run_id'] = 'foreign'
    changed = selected.project(canonical_json(binding), texts(0.0))
    assert all(row['run_id'] == 'foreign' for row in changed.rows)
    assert selected.statistics()['hits'] == 0
    assert selected.statistics()['entries'] == 2


@pytest.mark.parametrize('bound', ['max_input_bytes', 'max_rows', 'max_bytes'])
def test_retention_budget_does_not_truncate_output(bound):
    selected = cache(**{bound: 1})
    result = selected.project(canonical_json(common()), texts())
    assert len(result.rows) == 7
    assert selected.statistics()['entries'] == 0
    assert selected.statistics()['bypasses'] == 1


@pytest.mark.parametrize('invalid', ['{ "value":1}', '{"value":1,"value":1}', '{"value":NaN}'])
def test_invalid_or_noncanonical_tree_is_never_retained(invalid):
    selected = cache()
    with pytest.raises(ValueError):
        selected.project(canonical_json(common()), (invalid, '[]', '{}'))
    assert selected.statistics()['entries'] == 0


@pytest.mark.parametrize('field,value', [('sequence', True), ('sequence', -1),
    ('batch_id', 'invalid'), ('event_month', '2026-8-1')])
def test_original_schema_checks_remain_mandatory(field, value):
    binding = common()
    binding[field] = value
    selected = cache()
    with pytest.raises(ValueError):
        selected.project(canonical_json(binding), texts())
    assert selected.statistics()['entries'] == 0


def test_extra_common_field_and_wrong_tree_inventory_fail():
    selected = cache()
    binding = common()
    binding['unretained'] = 'bad'
    with pytest.raises(ValueError):
        selected.project(canonical_json(binding), texts())
    with pytest.raises(ValueError):
        selected.project(canonical_json(common()), texts()[:2])


def test_lru_and_byte_eviction_preserve_complete_outputs():
    selected = cache(max_entries=1)
    binding = canonical_json(common())
    first = selected.project(binding, texts(1))
    size = selected.statistics()['retained_bytes']
    second = selected.project(binding, texts(2))
    assert selected.project(binding, texts(1)) == first
    assert selected.statistics()['misses'] == 3
    assert len(second.rows) == len(first.rows)
    byte_limited = cache(max_bytes=size + 1000)
    byte_limited.project(binding, texts(1))
    byte_limited.project(binding, texts(2))
    stats = byte_limited.statistics()
    assert stats['entries'] == 1
    assert stats['retained_bytes'] <= stats['max_bytes']


def test_concurrent_same_key_has_one_complete_projection():
    from concurrent.futures import ThreadPoolExecutor
    selected = cache()
    arguments = (canonical_json(common()), texts())
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(lambda _: selected.project(*arguments), range(4)))
    assert all(value is results[0] for value in results)
    assert selected.statistics()['misses'] == 1
    assert selected.statistics()['hits'] == 3


@pytest.mark.parametrize('bound', ['max_entries', 'max_input_bytes', 'max_rows', 'max_bytes'])
@pytest.mark.parametrize('value', [0, -1, True])
def test_explicit_positive_bounds(bound, value):
    with pytest.raises(ValueError):
        cache(**{bound: value})
