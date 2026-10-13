"""Prepared canonical inputs are verified, never reacquired by derived workers."""
from datetime import date
import json
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock

import pytest

from pipelines.certified_research import source
from pipelines.certified_research.contracts import digest,REPORTING_REVISION
from pipelines.certified_research.handlers import CanonicalSourceHandlers


def prepared():
    day=date(2026,1,2);row=dict(source_date=str(day),trade_event_rows=4,updated_at='now')
    source_hash=digest({k:v for k,v in row.items() if k!='updated_at'})
    proof=dict(source_date=str(day),revision=REPORTING_REVISION,source_digest=source_hash,status='complete',
        details=json.dumps(dict(counts=dict(n=4,bad=0),evidence_version='original-reporting-nanosecond-evidence-v1',evidence_hash='b'*64)))
    queries=[];mutation=[]
    def query(sql):
        queries.append(sql)
        if 'events_source_day_stats' in sql:return [row]
        if 'events_build_manifest' in sql:return [dict(day=str(day),statuses=['ok'])]
        if 'historical_trade_reporting_coverage' in sql:return [proof]
        if 'system.mutations' in sql:return mutation
        raise AssertionError('Unexpected source access: '+sql)
    canonical=source.inventory(query,day,day,sessions=(day,))
    reporting=source.reporting_inventory(query,canonical)
    plans,parents=source.certified_units(canonical,reporting)
    return plans[0],parents,query,queries,proof,mutation


def test_prepared_source_root_verifies_metadata_and_closes_private_client():
    plan,parents,query,queries,proof,mutation=prepared()
    client=SimpleNamespace(query=query,close=Mock());claim=SimpleNamespace(check=Mock());guard=Mock()
    handler=CanonicalSourceHandlers(claim,lambda:client,guard)
    result=handler.verifier(plan,{})
    assert result['output_hash']==plan.arguments['source_hash']
    assert parents=={date(2026,1,2):(plan.unit.key,)}
    client.close.assert_called_once()
    assert all(sql.startswith('SELECT') for sql in queries)
    assert all('FROM market_sip_compact.events_2026' not in sql and 'file(' not in sql for sql in queries)


def test_counts_only_legacy_reporting_receipt_cannot_authorize_market_reads():
    plan,parents,query,queries,proof,mutation=prepared()
    proof['details']=json.dumps(dict(counts=dict(n=4,bad=0)))
    day=date(2026,1,2)
    canonical=source.inventory(query,day,day,sessions=(day,))
    reporting=source.reporting_inventory(query,canonical)
    assert reporting['missing']==(day,)
    assert reporting['unqualified'][str(day)]=='original_reporting_evidence_migration_required'
    assert not reporting['verified']
    with pytest.raises(ValueError,match='prerequisites'):
        source.certified_units(canonical,reporting)
    assert all(sql.startswith('SELECT') for sql in queries)
    assert all('FROM market_sip_compact.events_2026' not in sql and 'file(' not in sql for sql in queries)


@pytest.mark.parametrize('failure',('changed','missing','mutating','stop'))
def test_prepared_source_root_rejects_changed_or_unready_reporting(failure):
    plan,_,query,_,proof,mutation=prepared()
    client=SimpleNamespace(query=query,close=Mock())
    handler=CanonicalSourceHandlers(SimpleNamespace(check=Mock()),lambda:client,Mock())
    stop=Event()
    if failure=='changed':
        details=json.loads(proof['details']);details['evidence_hash']='c'*64;proof['details']=json.dumps(details)
    elif failure=='missing':proof['status']='incomplete'
    elif failure=='mutating':mutation.append(dict(mutation_id='active'))
    else:stop.set()
    with pytest.raises((ValueError,InterruptedError)):handler.producer(plan,{},stop)
    assert client.close.call_count==(0 if failure=='stop' else 1)
