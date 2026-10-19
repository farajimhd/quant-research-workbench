from copy import deepcopy
import re
from types import SimpleNamespace
import pytest
from research.level_book.v7.clickhouse_persistence import normalized_row_pages,epoch_ns
from scripts import backfill_structural_sessions as runner


def page_query(rows,identity):
    calls=[]
    def query(sql):
        calls.append(sql)
        limit=int(re.search(r'LIMIT (\d+)',sql)[1]);assert limit<=5000
        match=re.search(r"toDateTime64\('([^']+)',9,'UTC'\),'([^']+)'\)",sql)
        after=tuple(match.groups()) if match else None
        result=sorted((deepcopy(row) for row in rows if row['valid_to'] is None),key=lambda r:(r['valid_from'],r[identity]))
        return [row for row in result if after is None or (row['valid_from'],row[identity])>after][:limit]
    return query,calls


def rows(count,identity='observation_id'):
    return [dict(valid_from=f'2026-07-18 00:00:00.{i%2:09d}',valid_to=None,
        **{identity:f'{i:064x}'},source_checkpoint_hash='unchanged',price=2.97,
        row_revision=1784332800000000000) for i in range(count)]


def test_keyset_pages_preserve_nanosecond_versions_and_every_row():
    source=rows(10003);query,calls=page_query(source,'observation_id')
    actual=[r for page in normalized_row_pages(query,table='arte.example',prewhere="ticker='TEST' AND checkpoint_boundary=''",predicate='isNull(valid_to)',identity='observation_id') for r in page]
    assert actual==sorted(source,key=lambda r:(r['valid_from'],r['observation_id']))
    assert len(calls)==3
    assert all(' FINAL ' in q and 'ORDER BY valid_from,observation_id' in q for q in calls)


def test_pages_reject_nonadvancing_or_oversized_response():
    row=rows(1)[0]
    with pytest.raises(ValueError,match='did not advance'):
        list(normalized_row_pages(lambda q:[row,row],table='arte.x',prewhere='1',predicate='1',identity='observation_id'))
    with pytest.raises(ValueError,match='exceeded its limit'):
        list(normalized_row_pages(lambda q:[row]*5001,table='arte.x',prewhere='1',predicate='1',identity='observation_id'))


def test_open_interval_closure_resumes_after_partial_page_write(monkeypatch):
    data={runner.LEVELS_TABLE:rows(3,'level_id'),runner.OBSERVATIONS_TABLE:rows(10003)}
    before=deepcopy(data);queries=[];insert_count=0;interrupt=True;published=[]
    def query(sql):
        queries.append(sql)
        if 'SELECT count()' in sql:return [{'n':0}]
        table=runner.OBSERVATIONS_TABLE if runner.OBSERVATIONS_TABLE in sql else runner.LEVELS_TABLE
        identity='observation_id' if table==runner.OBSERVATIONS_TABLE else 'level_id'
        return page_query(data[table],identity)[0](sql)
    def insert(client,table,values):
        nonlocal insert_count
        if table==runner.COVERAGE_TABLE:published.extend(values);return
        if not values:return
        insert_count+=1
        if interrupt and insert_count==3:raise RuntimeError('interrupted between pages')
        identity='observation_id' if table==runner.OBSERVATIONS_TABLE else 'level_id'
        by_key={(row['valid_from'],row[identity]):row for row in data[table]}
        for value in values:by_key[(value['valid_from'],value[identity])].update(value)
    monkeypatch.setattr(runner.storage,'insert',insert)
    monkeypatch.setattr(runner.storage,'storage_preflight',lambda c:None)
    monkeypatch.setattr(runner,'compact_checkpoints',lambda *args:([],[],[{'state':'complete'}]))
    book={'ticker':'TEST','available_at':'1789420800.000000001'}
    with pytest.raises(RuntimeError,match='interrupted'):runner.publish_ah(SimpleNamespace(query=query),book,'f'*64)
    assert not published
    assert sum(row['valid_to'] is not None for row in data[runner.OBSERVATIONS_TABLE])==5000
    interrupt=False
    runner.publish_ah(SimpleNamespace(query=query),book,'f'*64)
    assert len(published)==1
    for table,values in data.items():
        for actual,original in zip(values,before[table]):
            assert actual['valid_to']=='2026-09-14 21:20:00.000000001'
            assert actual['row_revision']==epoch_ns(book['available_at'])
            assert {k:v for k,v in actual.items() if k not in ('valid_to','row_revision')}=={k:v for k,v in original.items() if k not in ('valid_to','row_revision')}
