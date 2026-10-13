"""Gateway coverage and original clocks gate prepared short dependencies."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

import pytest

from pipelines.certified_research.short_sql import publication_coverage
from pipelines.certified_research.handlers import PreparedShortSourceHandlers
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


def record(key,left,right,**changes):
    return dict(coverage_id=key,coverage_start_date=left,coverage_end_date=right,status='completed',
        rows_read=10,rows_written=10,rows_failed=0,source_object='CNMS.txt',source_run_id='run',
        finished_at_utc='2026-01-07 00:00:00',**changes)


def test_coverage_merges_exclusive_intervals_and_preserves_uncovered_sessions():
    days=(date(2026,1,2),date(2026,1,5),date(2026,1,6))
    rows=[record('a','2026-01-02','2026-01-03'),record('b','2026-01-05','2026-01-06')]
    query=Mock(return_value=rows)
    result=publication_coverage(query,days[0],days[-1],'short_volume',sessions=days)
    assert result['missing']==(days[-1],)
    rows.append(record('c','2026-01-06','2026-01-07'))
    assert publication_coverage(query,days[0],days[-1],'short_volume',sessions=days)['ready']
    rows[-1]['status']='bootstrap_trusted'
    assert not publication_coverage(query,days[0],days[-1],'short_volume',sessions=days)['ready']


@pytest.mark.parametrize('changes',({'rows_failed':1},{'finished_at_utc':None},
    {'status':'covered_empty'},{'source_run_id':''}))
def test_invalid_completion_is_not_coverage(changes):
    row=record('a','2026-01-02','2026-01-03');row.update(changes)
    with pytest.raises(ValueError):publication_coverage(lambda sql:[row],date(2026,1,2),date(2026,1,2),
        'short_volume',sessions=(date(2026,1,2),))


@pytest.mark.parametrize('failure',(None,'unknown','changed','coverage','gap'))
def test_prepared_short_handler_rechecks_both_authorities_and_always_closes(failure):
    plan=PlannedUnit(Unit('short','short_volume_acquisition'),dict(entity_key=1,start='2026-01-02',
        end='2026-01-02',source_hash='a'*64,coverage_hash='b'*64))
    audit=dict(n=2,unique_events=2,unknown_public_time=0,invalid_public_time=0,missing_evidence=0,source_hash='a'*64)
    coverage=dict(ready=True,coverage_hash='b'*64)
    if failure=='unknown':audit['unknown_public_time']=1
    elif failure=='changed':audit['source_hash']='c'*64
    elif failure=='coverage':coverage['coverage_hash']='c'*64
    elif failure=='gap':coverage['ready']=False
    client=SimpleNamespace(query=Mock(return_value=[audit]),close=Mock())
    handler=PreparedShortSourceHandlers(str(uuid4()),'arte',SimpleNamespace(check=Mock()),lambda:client,Mock())
    with patch('pipelines.certified_research.schema.verify_auxiliary_storage'),patch(
            'pipelines.certified_research.short_sql.publication_coverage',return_value=coverage):
        if failure:
            with pytest.raises(ValueError):handler.verifier(plan,{})
        else:
            result=handler.verifier(plan,{})
            assert result['source_hash']==audit['source_hash'] and len(result['output_hash'])==64
    client.close.assert_called_once()
    assert all(call.args[0].startswith('WITH') for call in client.query.call_args_list)
