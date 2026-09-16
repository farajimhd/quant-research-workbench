"""History navigation must not hydrate immutable configuration bodies."""
import json
from unittest.mock import patch

import asyncio

from src.trading_runtime.journal import TradingJournal


def test_history_queries_never_select_payloads_and_detail_remains_exact(tmp_path):
    journal = TradingJournal(tmp_path / 'history.sqlite3')
    body = {'strategy': {'profiles': [{'id': 'test', 'data': 'x' * 100_000}]}}
    try:
        for revision in range(1, 21):
            journal.save_trading_configuration_candidate(candidate_id=f'c{revision}',
                candidate_revision=revision, label='candidate', content_hash=str(revision), payload=body)
            journal.publish_trading_configuration(revision_id=f'r{revision}',
                revision=revision, label='published', content_hash=str(revision), payload=body)
        queries = []
        journal._connection.set_trace_callback(queries.append)
        with patch('src.trading_runtime.journal._configuration_candidate', side_effect=AssertionError('hydrated history')), \
             patch('src.trading_runtime.journal._configuration_revision', side_effect=AssertionError('hydrated history')):
            candidates = journal.trading_configuration_candidates()
            revisions = journal.trading_configuration_revisions()
        assert len(candidates) == len(revisions) == 20
        assert candidates[0]['candidate_id'] == 'c20'
        assert revisions[0]['revision_id'] == 'r20'
        assert all('payload_json' not in query and 'SELECT *' not in query for query in queries)
        for query in queries:
            plan = journal._connection.execute('EXPLAIN QUERY PLAN ' + query).fetchall()
            assert any('COVERING INDEX' in row['detail'] for row in plan)
        assert len(json.dumps(candidates)) < 10_000
        assert journal.trading_configuration_candidate('c7')['payload'] == body
        assert journal.trading_configuration_revision('r7')['payload'] == body
    finally:
        journal.close()


def test_summary_endpoints_and_selected_detail_preserve_contract():
    from src.backend import app as api
    summary = {'candidate_id': 'c1', 'candidate_revision': 1, 'label': 'test'}
    with patch.object(api, 'configuration_candidates', return_value=[summary]), \
         patch.object(api, 'configuration_candidate', side_effect=AssertionError('hydrated history')):
        result = asyncio.run(api.trading_configuration_candidate_list(latest_only=False))
    assert result['rows'] == [summary] and result['payloads_included'] is False
    revision = {'revision_id': 'r1', 'revision': 1}
    with patch.object(api, 'configuration_revisions', return_value=[revision]):
        result = asyncio.run(api.trading_configuration_revision_list())
    assert result['rows'] == [revision] and result['payloads_included'] is False
    detail = dict(summary, payload={'accounts': {'bindings': [
        {'source_account_id': 'private', 'source_account_env': 'ACCOUNT'}]}})
    with patch.object(api, 'configuration_candidate', return_value=detail) as selected:
        result = asyncio.run(api.trading_configuration_candidate_detail('c1'))
    selected.assert_called_once_with('c1')
    assert result['payload']['accounts']['bindings'][0]['source_account_id'] == ''
    assert detail['payload']['accounts']['bindings'][0]['source_account_id'] == 'private'


def test_duplicate_publication_returns_the_selected_full_revision():
    from src.backend import trading_configuration_service as service
    full = {'revision_id': 'r1', 'revision': 1, 'label': 'original',
            'content_hash': 'same', 'payload': {'strategy': {'profiles': []}}}
    summary = {key: value for key, value in full.items() if key != 'payload'}
    with patch.object(service, '_build_configuration_release',
                      return_value=({'market_discovery': {}}, full['payload'], 'same')), \
         patch.object(service, 'configuration_revisions', return_value=[summary]), \
         patch.object(service, 'configuration_revision', return_value=full) as selected, \
         patch.object(service, 'materialize_market_discovery'):
        result = service.publish_configuration(label='duplicate', canvas_revision='canvas',
            canvas_profile={}, configuration={})
    assert result == full
    selected.assert_called_once_with('r1')
