from datetime import date
from uuid import uuid4

import pytest

from pipelines.certified_research import indicator_warmup as warmup
from pipelines.certified_research.publication import Scope


def fixture(monkeypatch, per_day):
    dataset, attempt = str(uuid4()), str(uuid4())
    sessions = warmup.exchange_sessions(date(2025, 12, 1), date(2025, 12, 31))
    parents = [(Scope(dataset, day, 1, 'bars', 'a' * 64),
                dict(attempt_id=attempt, output_hash='b' * 64)) for day in sessions]
    receipts = {scope: receipt for scope, receipt in parents}

    class Claim:
        def check(self):
            pass

    class Publisher:
        def __init__(self, *args):
            pass

        def receipt(self, scope):
            return receipts[scope]

    monkeypatch.setattr(warmup, 'Publisher', Publisher)
    read = []

    def query(sql):
        day = next(day for day in sessions if str(day) in sql)
        read.append(day)
        return [dict(session_date=day, resolution_ms=frame, bucket_index=index,
                     low_int=100, close_int=110, high_int=120)
                for frame, count in per_day.items()
                if f'{frame}' in sql.split('resolution_ms IN (')[1].split(')')[0].split(',')
                for index in reversed(range(count))]

    return query, Claim(), parents, read


def test_dense_small_frame_stops_after_one_day(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {100: 50})
    result = warmup.load(query, 'arte', claim, parents, frames=(100,))
    assert len(read) == 1
    assert len(result['selected'][100]) == 50
    assert result['policy']['historical_exact'] is False


def test_hourly_frame_gets_only_50_bars_while_small_frame_stops(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {100: 50, 3600000: 16})
    result = warmup.load(query, 'arte', claim, parents[-4:], frames=(100, 3600000))
    assert len(read) == 4
    assert {frame: len(rows) for frame, rows in result['selected'].items()} == {100: 50, 3600000: 50}
    assert len({row['session_date'] for row in result['selected'][100]}) == 1
    assert len({row['session_date'] for row in result['selected'][3600000]}) == 4


def test_sparse_december_cannot_borrow_january_or_invent_rows(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {3600000: 1})
    with pytest.raises(ValueError, match='Insufficient December observed bars'):
        warmup.load(query, 'arte', claim, parents, frames=(3600000,))


def test_missing_december_session_is_not_silently_skipped(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {100: 50})
    with pytest.raises(ValueError, match='consecutive December'):
        warmup.load(query, 'arte', claim, parents[:-1], frames=(100,))
    assert not read


def test_duplicate_prices_are_rejected(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {100: 49})

    def duplicate(sql):
        rows = query(sql)
        return rows + [rows[0]]

    with pytest.raises(ValueError, match='duplicate'):
        warmup.load(duplicate, 'arte', claim, parents[-1:], frames=(100,))


def test_january_observations_are_rejected(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {100: 50})

    def future(sql):
        rows = query(sql)
        rows[0]['session_date'] = date(2026, 1, 2)
        return rows

    with pytest.raises(ValueError, match='out-of-scope'):
        warmup.load(future, 'arte', claim, parents[-1:], frames=(100,))


def test_nonminimal_short_frame_rows_not_fetched_after_first_day(monkeypatch):
    query, claim, parents, read = fixture(monkeypatch, {100: 50, 3600000: 16})
    statements = []

    def record(sql):
        statements.append(sql)
        return query(sql)

    warmup.load(record, 'arte', claim, parents[-4:], frames=(100, 3600000))
    assert 'IN (100,3600000)' in statements[0]
    assert all('IN (3600000)' in sql for sql in statements[1:])
