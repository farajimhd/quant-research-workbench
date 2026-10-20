from urllib.error import HTTPError

import pytest

from scripts import backfill_structural_sessions as runner


def test_unfinished_tickers_precede_audit_without_dropping_any_work():
    days=['2026-01-02','2026-01-05']
    scope={t:days for t in ('AAA','BBB','ZZZ')}
    calls=[]
    def query(sql):
        calls.append(sql)
        assert 'streaming_state' not in sql and ' FINAL ' in sql
        return [
            dict(ticker='AAA',day=d,boundaries=['PM','REGULAR']) for d in days
        ]+[dict(ticker='BBB',day=days[0],boundaries=['REGULAR','PM']),
           dict(ticker='ZZZ',day=days[1],boundaries=['PM']),
           dict(ticker='ZZZ',day='2025-12-31',boundaries=['PM','REGULAR'])]
    plan=runner.admission_plan(query,scope)
    assert plan['order']==['ZZZ','BBB','AAA']
    assert plan['persisted_pair_hints']==3
    assert plan['missing_pair_hints']==3
    assert plan['audit_only_tickers']==1
    assert plan['integrity_verified'] is False
    assert len(calls)==1


def test_empty_and_bounded_admission():
    assert runner.admission_plan(lambda _:pytest.fail('empty query'),{})['order']==[]
    calls=[]
    plan=runner.admission_plan(lambda sql:calls.append(sql) or [],
        {str(i):['2026-01-02'] for i in range(65)})
    assert len(calls)==3 and len(plan['order'])==65
    with pytest.raises(ValueError):
        runner.admission_plan(lambda _:[],{},batch_size=33)


def test_read_failure_restarts_both_boundaries(monkeypatch):
    seen=[]
    def verify(client,row,**kwargs):
        seen.append(row)
        if len(seen)==2:raise ConnectionResetError('midstream')
    monkeypatch.setattr(runner.storage,'verify_reused_boundary',verify)
    runner.verify_reuse(None,['PM','REGULAR'],{},'parent')
    assert seen==['PM','REGULAR','PM','REGULAR']


@pytest.mark.parametrize('error',[
    ValueError('corrupt output'),HTTPError('url',500,'server failure',{},None)])
def test_integrity_and_server_errors_are_not_retried(monkeypatch,error):
    seen=[]
    def verify(*args,**kwargs):
        seen.append(1)
        raise error
    monkeypatch.setattr(runner.storage,'verify_reused_boundary',verify)
    with pytest.raises(type(error)):
        runner.verify_reuse(None,['PM','REGULAR'],{},'parent')
    assert len(seen)==1


def test_retry_is_bounded(monkeypatch):
    seen=[]
    def verify(*args,**kwargs):
        seen.append(1)
        raise ConnectionResetError('persistent failure')
    monkeypatch.setattr(runner.storage,'verify_reused_boundary',verify)
    with pytest.raises(ConnectionResetError):
        runner.verify_reuse(None,['PM'],{},'parent')
    assert len(seen)==2
