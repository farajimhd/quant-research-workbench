"""Composed native contracts; cold DB readers mocked, no publication admission."""
import pytest

from src.trading_runtime.strategy_liquidity_fade_publication import prepare_liquidity_fade_publication_rows
from src.trading_runtime.arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE
from src.trading_runtime.arte_journal_writer import typed_row
from test_liquidity_fade_financial_checkpoint import case
from test_liquidity_fade_manager_checkpoint import checkpoint_case
from test_strategy_liquidity_fade_market_source import native_case, Reader


def integrated_case(monkeypatch):
    broker = case(monkeypatch)
    manager = checkpoint_case(monkeypatch)
    row, parent, event, prefix, financial = broker[:5]
    for field in ('source_manager_snapshot_id', 'source_manager_snapshot_hash'):
        row[field] = manager[0][field]
    _, source, bars, indicator, quote, args = native_case()
    row.update(source)
    reader = Reader((bars, (indicator,), (quote,)))
    context = dict(verified_prefix=prefix, market_plan=args['plan'],
                   financial_views={parent['record_id']: financial}, first_price_source='pinned')
    return reader, row, parent, event, context, broker, manager, bars, indicator, quote


def prepare(data):
    reader, row, parent, event, context, *_ = data
    return prepare_liquidity_fade_publication_rows(reader, (row,), (parent,), (event,), **context)


def test_complete_native_graph_checks_broker_manager_entry_and_three_market_queries(monkeypatch):
    data = integrated_case(monkeypatch)
    prepared = prepare(data)
    assert prepared == (typed_row(LIQUIDITY_FADE_FAILURE.name, data[1]),)
    assert len(data[0].queries) == 3
    assert len(data[5][9]) == 3  # context, broker, OMS; cursor reader shared with manager
    assert len(data[6][9]) == 1  # original committed entry loader
    assert len(data[6][8]) == 3  # financial cursor, manager cursor, snapshot


@pytest.mark.parametrize('failure', ['quantity', 'broker_hash', 'manager_hash', 'entry', 'market'])
def test_no_typed_family_return_when_any_independent_authority_fails(monkeypatch, failure):
    data = integrated_case(monkeypatch)
    if failure == 'quantity': data[2]['quantity'] = 37
    elif failure == 'broker_hash': data[5][5].positions[0]['quantity_f64_bits'] += 1
    elif failure == 'manager_hash': data[6][6].first_held_boundaries[0]['first_held_boundary_ms'] += 100
    elif failure == 'entry': data[6][10]['boundary_ms'] += 100
    else: data[7][0]['trade_count'] += 1
    with pytest.raises(ValueError):
        prepare(data)
    if failure != 'market': assert not data[0].queries


@pytest.mark.parametrize('failure', ['duplicate_child', 'missing_child', 'duplicate_parent',
    'missing_event', 'foreign_id', 'extra_view', 'missing_view'])
def test_entire_graph_preflight_rejects_before_any_cold_query(monkeypatch, failure):
    data = integrated_case(monkeypatch)
    reader, row, parent, event, context, *_ = data
    rows, parents, events = (row,), (parent,), (event,)
    if failure == 'duplicate_child': rows = (row, row)
    elif failure == 'missing_child': rows = ()
    elif failure == 'duplicate_parent': parents = (parent, parent)
    elif failure == 'missing_event': events = ()
    elif failure == 'foreign_id': row['record_id'] = '00000000-0000-0000-0000-000000000002'
    elif failure == 'extra_view': context['financial_views']['other'] = context['financial_views'][parent['record_id']]
    else: context['financial_views'] = {}
    with pytest.raises(ValueError):
        prepare_liquidity_fade_publication_rows(reader, rows, parents, events, **context)
    assert not reader.queries and not data[5][9] and not data[6][8]


def test_empty_family_is_noop_without_cold_reads(monkeypatch):
    data = integrated_case(monkeypatch)
    context = dict(data[4], financial_views={})
    assert prepare_liquidity_fade_publication_rows(data[0], (), (), (), **context) == ()
    assert not data[0].queries and not data[5][9] and not data[6][8]
