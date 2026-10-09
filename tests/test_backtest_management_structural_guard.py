from copy import copy, deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

import pytest

from src.backend.backtest_management_structural_guard import (
    ManagementStructuralGuard, capture_management_structural_guard as capture,
    require_management_structural_guard as require,
)
from src.backend.backtest_fixed_lot_management_reuse import _content


class Role(Enum):
    ENTRY = 'entry'


@dataclass
class Packet:
    rows: list
    graph_json: str


def packet():
    return Packet([{'quantity': 7, 'price': Decimal('3.2300'), 'high': -0.0,
                    'role': Role.ENTRY, 'at': datetime(2026, 8, 4, tzinfo=timezone.utc),
                    'day': date(2026, 8, 4), 'links': (1, 2), 'ids': frozenset({'a', 'b'})}],
                  '{"immutable_graph":"' + 'x' * 100_000 + '"}')


def test_fresh_full_graph_accepts_equal_copy_without_json_reencoding():
    value = packet()
    image = capture(value)
    assert _content(value) == _content(deepcopy(value))
    assert require(image, value) is image
    assert require(image, deepcopy(value)) is image


@pytest.mark.parametrize('change', [
    lambda p: p.rows[0].update(quantity=8),
    lambda p: p.rows[0].update(quantity=True),
    lambda p: p.rows[0].update(high=0.0),
    lambda p: p.rows[0].update(price=Decimal('3.23')),
    lambda p: p.rows[0].update(links=[1, 2]),
    lambda p: p.rows[0].update(extra=1),
    lambda p: p.rows.append(dict(p.rows[0])),
    lambda p: setattr(p, 'graph_json', p.graph_json + ' '),
    lambda p: p.rows[0].pop('quantity'),
])
def test_nested_mutation_is_detected_on_every_use(change):
    value = packet()
    image = capture(value)
    require(image, value)
    change(value)
    with pytest.raises(ValueError, match='content changed'):
        require(image, value)


def test_mapping_insertion_order_does_not_change_canonical_content():
    value = {'a': [1], 'b': [2]}
    image = capture(value)
    require(image, {'b': [2], 'a': [1]})


def test_list_reordering_is_detected():
    value = [1, 2]
    image = capture(value)
    with pytest.raises(ValueError):
        require(image, [2, 1])


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_float_matches_existing_closed_json_boundary(value):
    with pytest.raises(ValueError):
        _content(value)
    with pytest.raises(ValueError):
        capture(value)


def test_cycles_fail_closed():
    value = []
    value.append(value)
    with pytest.raises(ValueError, match='cycle'):
        capture(value)


def test_unissued_and_copied_images_cannot_verify():
    value = packet()
    image = capture(value)
    for foreign in (ManagementStructuralGuard(image.node_count), copy(image), deepcopy(image)):
        with pytest.raises(ValueError, match='Unissued'):
            require(foreign, value)


def test_image_field_replacement_is_detected():
    value = packet()
    image = capture(value)
    object.__setattr__(image, 'node_count', image.node_count + 1)
    with pytest.raises(ValueError, match='image changed'):
        require(image, value)


def test_dataclass_type_substitution_is_rejected_even_if_named_alike():
    value = packet()
    image = capture(value)
    Other = dataclass(type('Packet', (), {'__annotations__': {'rows': list, 'graph_json': str}}))
    with pytest.raises(ValueError, match='content changed'):
        require(image, Other(value.rows, value.graph_json))


def test_actual_native_publication_unit_and_record_are_fully_checked(monkeypatch):
    # Existing controlled producer/certifier fixture; actual native binder,
    # journal record and normalized semantic batch construction remain real.
    from test_fixed_structural_lot_entry_v4 import packet as native_packet
    _, _, _, record, unit = native_packet(monkeypatch)
    value = (unit, record)
    image = capture(value)
    assert _content(value)
    require(image, value)
    require(image, value)
    changed = replace(record, payload={**record.payload, 'unexpected': 1})
    with pytest.raises(ValueError, match='content changed'):
        require(image, (unit, changed))
