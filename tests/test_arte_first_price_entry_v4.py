from dataclasses import replace
from uuid import UUID

import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
from src.trading_runtime.arte_first_price_entry_v4 import FIRST_PRICE, project_first_price_entry, restore_first_price_entry


def unit():
    market, parent = authority()
    source = load_first_price_source(market, parent, client=Bars())
    plan = compile_certified_price_break_plan(source)
    current = parent.momentum.lookup('AAA', 41000)
    selection = plan.selection_witness('AAA', 41000)
    price = plan.price_witness('AAA', 41000)
    rows = project_first_price_entry(current, selection, price,
        price_source_token=source.token, run_id='price-run', batch_id=str(UUID(int=12)),
        parent_record_id=str(UUID(int=11)), event_month='2026-08-01')
    return current, selection, price, rows


def test_integer_companion_round_trip_and_source_link():
    current, selection, price, rows = unit()
    assert restore_first_price_entry(rows, current, selection) == price
    assert rows[0]['first_setup_boundary_ms'] == 31000
    assert rows[0]['boundary_ms'] == 41000
    assert dict(FIRST_PRICE.columns)['current_close_int'] == 'UInt64'
    assert dict(FIRST_PRICE.columns)['prior_high_int'] == 'UInt64'


@pytest.mark.parametrize('field,value', [
    ('first_setup_boundary_ms', 41000), ('current_close_int', 100),
    ('current_price_valid', 2), ('prior_extremes_valid', True),
    ('selection_token', 'f' * 64), ('strategy_number', 19),
    ('boundary_ms', 41000.0), ('record_id', str(UUID(int=15))),
])
def test_changed_price_anchor_or_entry_identity_rejected(field, value):
    current, selection, _, rows = unit()
    changed = dict(rows[0], **{field: value})
    with pytest.raises(ValueError):
        restore_first_price_entry((changed,), current, selection)


def test_wrong_build_and_missing_companion_rejected():
    current, selection, price, rows = unit()
    with pytest.raises(ValueError):
        restore_first_price_entry((), current, selection)
    changed = dict(rows[0], source_build_id='f' * 64)
    with pytest.raises(ValueError):
        restore_first_price_entry((changed,), current, selection)
