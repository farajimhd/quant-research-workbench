from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.backend import backtest_v4_query as query
from src.trading_runtime import arte_journal_reader as reader
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix

RUN = "00000000-0000-0000-0000-000000000001"
BATCH = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def authority(monkeypatch):
    prefix = V4CommittedPrefix(RUN, 20, BATCH, "bar:1", "completed", (BATCH,))
    monkeypatch.setattr(query, "_terminal_attestation", lambda *_: {"prefix": prefix})
    monkeypatch.setattr(query, "_head_matches", lambda *_: True)
    return prefix


def test_facets_do_not_load_event_payloads(monkeypatch, authority):
    statements = []
    def rows(_, sql):
        statements.append(sql)
        return ([{"category": "strategy", "entity_type": "intent"}]
                if "GROUP BY" in sql else [{"ticker": "BBNX"}])
    monkeypatch.setattr(query, "_rows", rows)
    monkeypatch.setattr(query, "_detail_family", lambda *_: "trading_strategy_intent_v1")
    payloads = Mock(side_effect=AssertionError("Must not load payloads"))
    monkeypatch.setattr(query, "load_typed_event_page", payloads)
    result = query.query_saved_journal(None, RUN, facets=True)
    assert result['tickers'] == ['BBNX']
    assert all('batch_id' in sql and RUN in sql for sql in statements)
    payloads.assert_not_called()


def test_query_pushes_filters_and_uses_lookahead(monkeypatch, authority):
    statements = []
    def rows(_, sql):
        statements.append(sql)
        return [] if 'GROUP BY' in sql else [{'sequence': 3}, {'sequence': 9}]
    monkeypatch.setattr(query, '_rows', rows)
    payloads = Mock(return_value=(SimpleNamespace(event={'sequence': 3}, detail=None, detail_family=None),))
    monkeypatch.setattr(query, 'load_typed_event_page', payloads)
    result = query.query_saved_journal(None, RUN, event_type='intent',
        start='2026-08-18T08:00:00Z', end='2026-08-18T09:00:00Z', limit=1)
    assert not result['complete'] and result['next_sequence'] == 3
    assert payloads.call_args.kwargs['selected_sequences'] == (3,)
    assert "entity_type='intent'" in statements[-1]
    assert 'parseDateTime64BestEffort' in statements[-1]
    assert 'LIMIT 2' in statements[-1]


@pytest.mark.parametrize("facets", [False, True])
def test_exact_recorded_queries_select_shared_price_families_and_programme_decisions(monkeypatch, authority, facets):
    from src.backend import backtest_recorded_journal
    monkeypatch.setattr(backtest_recorded_journal, "load_recorded_attestation", lambda *_: {"prefix": authority})
    statements = []
    def rows(_, sql):
        statements.append(sql)
        if "GROUP BY" in sql:
            return [{"category": "strategy_decision", "entity_type": "signal"},
                {"category": "execution", "entity_type": "fill"}]
        return [{"ticker": "ABC"}] if "DISTINCT ticker" in sql else [{"sequence": 3}]
    monkeypatch.setattr(query, "_rows", rows)
    payloads = Mock(return_value=(SimpleNamespace(event={"sequence": 3}, detail=None, detail_family=None),))
    monkeypatch.setattr(query, "load_typed_event_page", payloads)
    result = query.query_saved_journal(None, RUN, journal_only=True, exact_prices=True,
        facets=facets, ticker="ABC")
    combined = " ".join(statements)
    assert "'strategy_decision'" in combined
    assert "arte.trading_strategy_signal_exact_price_v2" in combined
    assert "arte.trading_execution_exact_price_v1" in combined
    assert "arte.trading_strategy_signal_v2 " not in combined
    assert "arte.trading_execution_v1 " not in combined
    if facets:
        assert result["tickers"] == ["ABC"]
        payloads.assert_not_called()
    else:
        assert payloads.call_args.kwargs["exact_prices"] is True


def test_exact_query_requires_explicit_read_authority_before_io():
    with pytest.raises(ValueError, match="recorded-journal"):
        query.query_saved_journal(None, RUN, exact_prices=True)


@pytest.mark.parametrize('kwargs', [dict(domain='invalid'), dict(limit=501),
    dict(start='2026-08-18T08:00:00'), dict(start='2026-08-19T08:00:00Z', end='2026-08-18T08:00:00Z')])
def test_invalid_query_rejected_before_reads(kwargs):
    with pytest.raises(ValueError):
        query.query_saved_journal(None, RUN, **kwargs)


def test_selected_event_reader_checks_exact_requested_sequences(monkeypatch, authority):
    record = '00000000-0000-0000-0000-000000000003'
    event = dict(run_id=RUN, sequence=3, record_id=record, batch_id=BATCH,
                 category='run', entity_type='transition')
    monkeypatch.setattr(reader, '_rows', lambda *_: [event])
    monkeypatch.setattr(reader, '_verified_row', lambda _, row: row)
    monkeypatch.setattr(reader, '_detail_family', lambda *_: None)
    assert len(reader.load_typed_event_page(None, authority, selected_sequences=(3,))) == 1
    with pytest.raises(RuntimeError, match='missing committed'):
        reader.load_typed_event_page(None, authority, selected_sequences=(3, 9))
    with pytest.raises(RuntimeError, match='committed prefix'):
        reader.load_typed_event_page(None, authority)


def test_metadata_bootstrap_does_not_read_journal_rows(monkeypatch, authority):
    from src.backend import backtest_v4_saved_review as review
    monkeypatch.setattr(review, '_terminal_attestation', lambda *_: {
        'context': {}, 'prefix': authority, 'cursor': None, 'initial_cash': 10000,
        'accounts': {}, 'financial_accounts': {},
    })
    monkeypatch.setattr(review, '_head_matches', lambda *_: True)
    payloads = Mock(side_effect=AssertionError('No startup journal rows'))
    monkeypatch.setattr(review, 'load_typed_event_page', payloads)
    result = review.load_v4_terminal_review_page(None, RUN, metadata_only=True)
    assert not result['events'] and not result['complete']
    payloads.assert_not_called()
