"""Actual completed-risk Arrow reader through the report SELECT-only protocol."""
from datetime import date

import pytest

from scripts.clickhouse.report_strategy_one_trades import SelectOnly, report_select_query
from src.backend.backtest_confirmed_original_risk_source import load_completed_risk_lookup
from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
from test_confirmed_original_risk_source import plan, frame
DAY = date(2026, 1, 1)


class Stream:
    def __init__(self):
        self.batches = iter(frame().to_arrow().to_batches())
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.batches)

    def close(self):
        self.closed = True


class Client:
    base_url = 'synthetic://transport'
    user = 'synthetic'
    password = ''
    confirmed_original_risk_policy = ConfirmedOriginalRiskPolicy()

    def __init__(self):
        self.calls = []
        self.streams = []

    def iter_arrow_record_batches(self, query, *args, **kwargs):
        self.calls.append((query, args, kwargs))
        stream = Stream()
        self.streams.append(stream)
        return stream


def test_actual_completed_risk_select_through_report_wrapper_and_stream_close():
    client = Client()
    wrapped = SelectOnly(client)
    result = load_completed_risk_lookup(wrapped, plan=plan(), session_date=DAY,
                                       tickers=('TEST',), through_boundary_ms=100000)
    assert result.pair_at('TEST', 100000)[0].boundary_ms == 95000
    assert result.pair_at('TEST', 105000) is None
    assert len(client.calls) == 1 and client.streams[0].closed
    query, args, kwargs = client.calls[0]
    assert 'FROM arte.bars_v1' in query and 'FORMAT ArrowStream' in query
    assert args == () and kwargs == {}
    # Same genuine completed-source SQL, preserving positional/keyword protocol.
    stream = wrapped.iter_arrow_record_batches(' '+query+'; ', 'positional-option',
                                               max_block_size=17, settings={'readonly': 1})
    assert stream is client.streams[-1] and not stream.closed
    assert client.calls[-1] == (report_select_query(query), ('positional-option',),
                                {'max_block_size': 17, 'settings': {'readonly': 1}})
    stream.close()
    assert stream.closed


@pytest.mark.parametrize('query', [
    'INSERT INTO arte.example VALUES (1)',
    'ALTER TABLE arte.example DELETE WHERE 1',
    'SELECT 1; INSERT INTO arte.example VALUES (1)',
    'SELECT 1 FORMAT ArrowStream; ALTER TABLE arte.example DELETE WHERE 1',
])
def test_write_and_stacked_write_rejected_before_arrow_transport(query):
    client = Client()
    with pytest.raises(ValueError):
        SelectOnly(client).iter_arrow_record_batches(query, max_block_size=17)
    assert client.calls == [] and client.streams == []


def test_declared_rewrap_retains_selected_attributes_and_guarded_arrow_protocol():
    original = SelectOnly(Client())
    client = Client()
    client.entry_spread_risk_profile = True
    client.automatic_ladder_profile = False
    client.ladder_geometry_policy = None
    client.v4_batched_detail_readback = True
    selected = original.declared_read_wrapper(client)
    assert type(selected) is SelectOnly and selected.client is client
    assert selected.confirmed_original_risk_policy is client.confirmed_original_risk_policy
    assert selected.entry_spread_risk_profile is True
    assert selected.automatic_ladder_profile is False
    assert selected.ladder_geometry_policy is None
    assert selected.v4_batched_detail_readback is True
    stream = selected.iter_arrow_record_batches('SELECT 1 FORMAT ArrowStream')
    assert stream is client.streams[-1]
    stream.close()
    with pytest.raises(ValueError):
        selected.iter_arrow_record_batches('ALTER TABLE arte.example DELETE WHERE 1')
    assert len(client.calls) == 1
