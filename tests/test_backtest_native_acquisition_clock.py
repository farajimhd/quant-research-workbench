"""Exact normalized acquisition projection; not native admission proof."""
from datetime import date, datetime, timezone
import json

import pytest

from src.backend.backtest_native_acquisition_clock import (
    project_native_acquisition_clock, load_native_acquisition_clock,
)
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.ibkr_schema import OrderRequest


RUN = '11111111-1111-4111-8111-111111111111'
BATCH = '22222222-2222-4222-8222-222222222222'


def facts():
    prefix = V4CommittedPrefix(RUN, 20, BATCH, '', 'running', (BATCH,))
    request = OrderRequest(acctId='DU1', conid=123, orderType='MKT', side='BUY',
        quantity=10, ticker='TEST', cOID='current', raw={'canonical_run_id': RUN})
    fill = dict(run_id=RUN, batch_id=BATCH, sequence=7, execution_id='SIM-1',
        client_order_id='current', broker_order_id='ORDER-1', account_id='DU1',
        ticker='TEST', conid=123, side='B', quantity='4.0',
        source_event_time='2026-08-04T08:01:00.000123+00:00')
    return prefix, request, fill


def call(fills=None, **kwargs):
    prefix, request, fill = facts()
    return project_native_acquisition_clock((fill,) if fills is None else fills,
        prefix, (request,), session_date=date(2026, 8, 4),
        decision_at=datetime(2026, 8, 4, 8, 2, tzinfo=timezone.utc), max_fills=100,
        coid_by_broker_id=kwargs.pop('coid_by_broker_id', {'ORDER-1': 'current'}), **kwargs)


def test_microsecond_clock_is_preserved_without_float_conversion():
    result = call()
    assert result.acquisition_day_us == 14460000123
    assert result.source_event_time.endswith('00.000123+00:00')
    assert result.execution_sequence == 7
    assert result.prefix_sequence == 20


def test_prior_position_and_later_partial_fill_do_not_move_acquisition():
    _, _, fill = facts()
    prior = {**fill, 'sequence': 5, 'execution_id': 'SIM-0', 'client_order_id': 'prior',
             'source_event_time': '2026-08-04T08:00:00+00:00'}
    later = {**fill, 'sequence': 8, 'execution_id': 'SIM-2',
             'source_event_time': '2026-08-04T08:01:10+00:00'}
    assert call((prior, fill, later)) == call()


@pytest.mark.parametrize('change', [
    {'source_event_time': '2026-08-04T08:03:00+00:00'},
    {'source_event_time': '2026-08-04T08:01:00'},
    {'source_event_time': '2026-08-03T08:01:00+00:00'},
    {'run_id': 'foreign'}, {'batch_id': 'foreign'}, {'sequence': 21},
    {'account_id': 'foreign'}, {'side': 'S'}, {'quantity': '0'},
    {'quantity': 'NaN'}, {'quantity': 1.0}, {'client_order_id': 'prior'},
])
def test_foreign_future_and_non_acquired_facts_fail_closed(change):
    _, _, fill = facts()
    with pytest.raises(ValueError):
        call(({**fill, **change},))


def test_duplicate_and_foreign_broker_order_rejected():
    _, _, fill = facts()
    with pytest.raises(ValueError):
        call((fill, fill))
    with pytest.raises(ValueError, match='broker order ownership'):
        call(coid_by_broker_id={'ORDER-1': 'foreign'})


def test_original_committed_reader_checks_event_detail_envelope():
    prefix, request, fill = facts()
    record = '33333333-3333-4333-8333-333333333333'
    detail = {**fill, 'record_id': record, 'event_month': '2026-08-01'}
    event = dict(record_id=record, batch_id=BATCH, sequence=7,
        event_month='2026-08-01', account_id='DU1', event_time=fill['source_event_time'],
        entity_id=fill['execution_id'])
    class Transport:
        def __init__(self):
            self.sql = []
        def execute(self, sql):
            self.sql.append(sql)
            if 'FROM arte.trading_event_v1 ' in sql:
                return json.dumps(event)
            if 'FROM arte.trading_execution_v1 ' in sql:
                return json.dumps(detail)
            raise AssertionError(sql)
    client = Transport()
    def load():
        return load_native_acquisition_clock(client, prefix, (request,),
            session_date=date(2026, 8, 4),
            decision_at=datetime(2026, 8, 4, 8, 2, tzinfo=timezone.utc), max_fills=100,
            coid_by_broker_id={'ORDER-1': 'current'})
    assert load() == call()
    assert len(client.sql) == 2
    assert all(sql.startswith('SELECT') and 'trading_commit_v4' in sql for sql in client.sql)
    detail['source_event_time'] = '2026-08-04T08:01:00.000124+00:00'
    with pytest.raises(RuntimeError, match='event envelope'):
        load()
