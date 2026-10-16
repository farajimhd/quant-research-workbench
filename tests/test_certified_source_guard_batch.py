from datetime import date
from types import SimpleNamespace
import json

import pytest

from pipelines.certified_research import source,source_guard_batch as batch,verification
from pipelines.certified_research.contracts import digest

DAY=date(2026,1,2)


def responses(*,changed=False,active=False,owned=True):
    row=dict(source_date=str(DAY),trade_event_rows='13' if changed else '12',
        first_sip_timestamp_us='1767373200000000',last_sip_timestamp_us='1767430800000000')
    fingerprint=digest(row)
    marker='a'*64
    queries=batch.statements((DAY,))
    result={queries[0]:[row],queries[1]:[dict(day=str(DAY),statuses=['ok'])],
        queries[2]:[dict(source_date=str(DAY),revision=source.REVISION,source_digest=fingerprint,status='complete',
            details=json.dumps(dict(counts=dict(n=int(row['trade_event_rows']),bad=0),
                evidence_version='original-reporting-nanosecond-evidence-v1',evidence_hash='b'*64)))],
        queries[3]:[dict(mutation_id='m',table='events_2026',command='UPDATE event_meta = 64 /* '+marker+' */',
            parts_to_do_names=['202603_1_1_0'])] if active else [],
        queries[4]:[dict(n=1)]}
    result['SELECT target,marker FROM q_live.historical_native_reporting_repair_v1 LIMIT 10001']=[
        dict(target='market_sip_compact.events_2026',marker=marker if owned else 'c'*64)]
    return result


class Query:
    def __init__(self,*,second_changed=False,active=False,owned=True):
        self.rounds=0;self.headers=0
        self.second_changed=second_changed;self.active=active;self.owned=owned
    def __call__(self,sql):
        if 'guard_payload' in sql:
            self.rounds+=1
            result=responses(changed=self.second_changed and self.rounds%2==0,active=self.active,owned=self.owned)
            rows=[]
            for tag,q in enumerate(batch.statements((DAY,))):
                rows.append(dict(guard_tag=tag,guard_payload=''))
                rows.extend(dict(guard_tag=tag,guard_payload=json.dumps(r)) for r in result[q])
            return rows
        self.headers+=1
        return responses(active=self.active,owned=self.owned)[sql]


def saved():
    answers=responses()
    canonical=source.inventory(answers.__getitem__,DAY,DAY,sessions=(DAY,))
    reporting=source.reporting_inventory(answers.__getitem__,canonical)
    value=source.market_day_hashes(canonical,reporting)[DAY]
    return SimpleNamespace(units=[SimpleNamespace(unit=SimpleNamespace(product='canonical_source'),
        arguments=dict(day=str(DAY),source_hash=value))],
        source_hash=digest(dict(source=canonical['content_hash'],reporting=reporting['content_hash'])))


def test_both_fresh_snapshots_and_no_cross_call_cache():
    query=Query();guard=verification.source_guard(query,saved())
    guard();assert query.rounds==2
    guard();assert query.rounds==4
    assert query.headers==0


def test_second_snapshot_change_still_fails_closed():
    query=Query(second_changed=True)
    with pytest.raises(ValueError,match='inventory changed'):verification.source_guard(query,saved())()
    assert query.rounds==2


def test_disjoint_owned_mutation_checks_headers_in_both_rounds():
    query=Query(active=True)
    verification.source_guard(query,saved())()
    assert query.rounds==2 and query.headers==2
    with pytest.raises(ValueError):verification.source_guard(Query(active=True,owned=False),saved())()


def test_transport_requires_named_objects_and_all_sentinels():
    statements=batch.statements((DAY,))
    with pytest.raises(ValueError,match='incomplete'):batch.fetch(lambda _:[],statements)
    with pytest.raises(ValueError,match='column names'):
        batch.fetch(lambda _:[dict(guard_tag=0,guard_payload='[1]')],statements)
