"""Immutable scope resolution must reject edits, ambiguity and late snapshots."""
from copy import deepcopy
from datetime import date
import pytest
from research.vectorized_backtest.v18.indicator_identity import population_map
from research.rl_trading.v1.common import digest


def fixture():
    rows=[dict(ticker='A',listing_id='old-route',symbol_id='s1',security_id='sec',source_run_id='r',inserted_at='2026-09-04 07:00:00'),
          dict(ticker='A',listing_id='certified-listing',symbol_id='s2',security_id='sec',source_run_id='r',inserted_at='2026-09-04 07:00:00')]
    cert=dict(status='certified',revision='preopen-tradable-snapshot-v3',snapshot_id='fixed',row_count=2,tradable_count=2,source_hash='7',
              captured_at_utc='2026-09-04T07:00:00+00:00',available_at_utc='2026-09-04T07:30:00+00:00',cutoff_utc='2026-09-04T08:00:00+00:00')
    source=dict(definition=dict(plan=dict(population=[dict(session_date='2026-09-04',certificate=cert,snapshot_hash=digest(rows))])),units={'2026-09-04':{'A':{}}})
    return source,rows


def test_frozen_map_resolves_alias_without_changing_input_scope(monkeypatch):
    from research.vectorized_backtest.v18 import indicator_identity as m
    source,rows=fixture()
    monkeypatch.setattr(m.sql,'query',lambda c,s:[dict(n=2,tradable=2,source_hash='7')] if 'count()' in s else rows)
    chosen,proof=population_map(None,source,date(2026,9,4),{'certified-listing':'A'})
    assert chosen=={'certified-listing':'A'} and proof['outside_frozen_scope_rows']==1
    assert len(rows)==2


@pytest.mark.parametrize('kind',['changed_hash','wrong_pair','duplicate_id','late','missing'])
def test_frozen_map_rejects_invalid_evidence(monkeypatch,kind):
    from research.vectorized_backtest.v18 import indicator_identity as m
    source,rows=fixture();expected={'certified-listing':'A'}
    if kind=='changed_hash':rows[0]['source_run_id']='changed'
    elif kind=='wrong_pair':expected['certified-listing']='B'
    elif kind=='duplicate_id':rows[0]['listing_id']='certified-listing';source['definition']['plan']['population'][0]['snapshot_hash']=digest(rows)
    elif kind=='late':source['definition']['plan']['population'][0]['certificate']['available_at_utc']='2026-09-04T08:01:00+00:00'
    elif kind=='missing':expected={'absent':'A'}
    monkeypatch.setattr(m.sql,'query',lambda c,s:[dict(n=2,tradable=2,source_hash='7')] if 'count()' in s else rows)
    with pytest.raises(ValueError):population_map(None,source,date(2026,9,4),expected)
