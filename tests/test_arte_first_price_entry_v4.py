from dataclasses import replace
from uuid import UUID, uuid5, NAMESPACE_URL

import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan
from src.trading_runtime.arte_first_price_entry_v4 import (
    FIRST_PRICE, project_first_price_entry, restore_first_price_entry,
    FirstPriceEntryAuthority, seal_first_price_rows,
)


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
    assert restore_first_price_entry(rows, current, selection, expected_price=price, expected_price_source_token=rows[0]['price_source_token']) == price
    assert rows[0]['first_setup_boundary_ms'] == 31000
    assert rows[0]['boundary_ms'] == 41000
    assert dict(FIRST_PRICE.columns)['current_close_int'] == 'UInt64'
    assert dict(FIRST_PRICE.columns)['prior_high_int'] == 'UInt64'


@pytest.mark.parametrize('field,value', [
    ('first_setup_boundary_ms', 41000), ('current_close_int', 100),
    ('current_price_valid', 2), ('prior_extremes_valid', True),
    ('selection_token', 'f' * 64), ('strategy_number', 19),
    ('boundary_ms', 41000.0), ('record_id', str(UUID(int=15))),
    ('price_source_token', 'f' * 64), ('bars_attempt_id', str(UUID(int=15))),
    ('current_close_int', 102), ('prior_high_int', 99),
])
def test_changed_price_anchor_or_entry_identity_rejected(field, value):
    current, selection, price, rows = unit()
    changed = dict(rows[0], **{field: value})
    with pytest.raises(ValueError):
        restore_first_price_entry((changed,), current, selection, expected_price=price, expected_price_source_token=rows[0]['price_source_token'])


def test_wrong_build_and_missing_companion_rejected():
    current, selection, price, rows = unit()
    with pytest.raises(ValueError):
        restore_first_price_entry((), current, selection, expected_price=price, expected_price_source_token=rows[0]['price_source_token'])
    changed = dict(rows[0], source_build_id='f' * 64)
    with pytest.raises(ValueError):
        restore_first_price_entry((changed,), current, selection, expected_price=price, expected_price_source_token=rows[0]['price_source_token'])


def graph():
    current, selection, price, rows = unit()
    row = rows[0]
    parent = row['parent_record_id']
    scope = {name: row[name] for name in ('run_id', 'batch_id', 'event_month')}
    entry = dict(scope, parent_record_id=parent, strategy_number=20,
                 boundary_ms=41000, episode_start_ms=30000, assignment_id='test')
    identity = 'strategy-20:2026-08-18:test:account:AAA:41000:30000'
    intent_id = str(uuid5(NAMESPACE_URL, identity))
    intent = dict(scope, record_id=parent, action='enter_long', reason='strategy_one_entry',
                  ticker='AAA', account_id='account', intent_id=intent_id)
    event = dict(scope, record_id=parent, category='strategy', entity_type='strategy_intent',
                 entity_id=intent_id, account_id='account', event_time='2026-08-18T08:00:41+00:00')
    authority = FirstPriceEntryAuthority(parent, current, selection, price, row['price_source_token'])
    return rows, (entry,), (intent,), (event,), (authority,)


@pytest.fixture
def staged_contract(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer
    monkeypatch.setitem(writer._CONTRACTS, FIRST_PRICE.name, FIRST_PRICE)


def test_entire_entry_graph_roundtrip_and_hash(staged_contract):
    args = graph()
    sealed = seal_first_price_rows(*args)
    assert len(sealed[0]['content_hash']) == 64
    assert seal_first_price_rows(sealed, *args[1:]) == sealed


@pytest.mark.parametrize('component,field,value', [
    (0, 'current_close_int', 102), (0, 'price_source_token', 'f' * 64),
    (0, 'run_id', 'another-run'), (1, 'boundary_ms', 42000),
    (2, 'ticker', 'BBB'), (2, 'account_id', 'another-account'),
    (3, 'event_time', '2026-08-18T08:00:42+00:00'),
    (3, 'event_time', '2026-08-18T08:00:41'),
])
def test_entire_graph_rejects_source_and_parent_changes(staged_contract, component, field, value):
    args = list(graph())
    args[component] = (dict(args[component][0], **{field: value}),)
    with pytest.raises(ValueError):
        seal_first_price_rows(*args)


def test_missing_extra_duplicate_and_unbound_authority_fail_closed(staged_contract):
    args = graph()
    for changed in ((), args[0] * 2):
        with pytest.raises(ValueError):
            seal_first_price_rows(changed, *args[1:])
    with pytest.raises(ValueError):
        seal_first_price_rows(*args[:4], ())
    with pytest.raises(ValueError):
        seal_first_price_rows(*args[:4], args[4] * 2)
    sealed = seal_first_price_rows(*args)
    changed = (dict(sealed[0], content_hash='f' * 64),)
    with pytest.raises(ValueError):
        seal_first_price_rows(changed, *args[1:])


def test_afterhours_retains_momentum_without_price_companion():
    from tests.test_strategy_initial_strong_momentum import witness
    from src.trading_runtime.strategy_initial_strong_momentum import (
        InitialStrongMomentumWitness, InitialMomentumSelectionWitness,
    )
    current, first = witness(43240100), witness(43230100)
    selection = InitialMomentumSelectionWitness(
        InitialStrongMomentumWitness(43230000, first), 'c' * 64, 'd' * 64, 'e' * 64)
    assert project_first_price_entry(current, selection, None,
        price_source_token='f' * 64, run_id='ah-run', batch_id=str(UUID(int=12)),
        parent_record_id=str(UUID(int=11)), event_month='2026-08-01') == ()
    assert restore_first_price_entry((), current, selection,
        expected_price=None, expected_price_source_token='f' * 64) is None
    with pytest.raises(ValueError, match='After-hours'):
        restore_first_price_entry(unit()[3], current, selection,
            expected_price=None, expected_price_source_token='f' * 64)
