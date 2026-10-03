"""The retained cohort is a hint; the saved structure digest is authority."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from src.backend import backtest_recorded_v7 as subject


ROWS = [dict(ticker=ticker, attempt_id=f'00000000-0000-0000-0000-{index:012d}',
             clock_hash='a'*64, interval_hash='b'*64)
        for index, ticker in enumerate(('AAA', 'MIMI'), 1)]


@pytest.mark.parametrize('damage', [None, 'coverage_hash', 'cohort', 'seed', 'saved_pin', 'parts'])
def test_saved_structure_root_and_selected_child_certificate_are_both_required(monkeypatch, damage):
    market = SimpleNamespace(build_id='build', token='market', tickers=('AAA', 'MIMI'))
    seeds = SimpleNamespace(token='seed')
    pin = subject.recorded_structure_digest(market.token, seeds.token, '2026-09-03', ROWS)
    rows = deepcopy(ROWS)
    if damage == 'coverage_hash':
        rows[0]['clock_hash'] = 'c'*64
    elif damage == 'seed':
        seeds.token = 'different'
    elif damage == 'saved_pin':
        pin = '0'*64
    read_children = []
    class Reader:
        def execute(self, query):
            if 'source_market_token' in query:
                values = [{'ticker': row['ticker']} for row in ROWS]
                if damage == 'cohort':
                    values.pop()
            else:
                values = rows
            return '\n'.join(json.dumps(row) for row in values)
    fingerprints = iter(('before', 'changed' if damage == 'parts' else 'before'))
    monkeypatch.setattr(subject, 'selected_product_inventory_fingerprint', lambda *_a, **_k: next(fingerprints))
    monkeypatch.setattr(subject, 'project_market_day_plan', lambda *_: market)
    monkeypatch.setattr(subject, 'certified_seed_plan', lambda *_: seeds)
    def certify(*_a, **kwargs):
        read_children.append(kwargs['candidate_tickers'])
        return SimpleNamespace(intervals=(('MIMI', ('sealed-child',)),))
    monkeypatch.setattr(subject, 'certify_v7_interval_plan', certify)
    def read():
        return subject.recorded_v7_ticker_intervals(Reader(), market=market,
            session='2026-09-03', ticker='MIMI', structure_pin=pin)
    if damage:
        with pytest.raises(RuntimeError):
            read()
        if damage != 'parts':
            assert read_children == []
    else:
        assert read() == ('sealed-child',)
        assert read_children == [('MIMI',)]


def test_structure_digest_rejects_duplicate_cohort():
    with pytest.raises(RuntimeError, match='duplicated'):
        subject.recorded_structure_digest('market', 'seed', '2026-09-03', [ROWS[0], ROWS[0]])
