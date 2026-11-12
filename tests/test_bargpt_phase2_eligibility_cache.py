from contextlib import nullcontext
import json
from types import SimpleNamespace

import polars as pl
import pytest

from research.bar_gpt.v5.phase2_ar_shards import source
from research.bar_gpt.v5.phase2_ar_shards.eligibility_cache import EligibilityCache,bucket,SOURCE_COLUMNS
from research.bar_gpt.v5.phase2_ar_shards.continuation import semantic_identity
from research.bar_gpt.v5.shards.contract import digest,file_hash,write_json
from research.bar_gpt.v5.shards.sql import clock


def row(day='2021-01-04'):
    return dict(ticker='T',source_date=day,event_count=10000,first_ordinal=0,next_ordinal=10000,
                first_sip_timestamp_us=clock(day,4),last_sip_timestamp_us=clock(day,20)-1,build_step=1)


def cache(tmp_path, count=31):
    parent=tmp_path/'parent'
    plan=dict(version='v',geometry={},parent_binding='p',ordinal_hash='o',authority={},source_query='q',quote_policy='v3',code={})
    plan['continuation']=dict(parents=[dict(root=str(parent),binding='parent-binding')])
    r=row();root=tmp_path/'cache';root.mkdir()
    path=root/'counts.parquet';pl.DataFrame([dict(r,origin_count=count)]).write_parquet(path)
    part=dict(name=path.name,bytes=path.stat().st_size,sha256=file_hash(path),rows=1)
    m=dict(version='sealed-origin-count-cache-v1',semantic_identity=semantic_identity(plan),
           parent=dict(root=str(parent),binding='parent-binding'),parts={bucket('T'):part})
    m['hash']=digest(m);write_json(root/'manifest.json',m)
    return EligibilityCache(root,plan),r,plan,root


def test_exact_row_binding_and_misses_do_not_become_zero(tmp_path):
    c,r,_,_=cache(tmp_path)
    assert c.get('T',r)==31
    assert c.get('T',row('2021-01-05')) is None
    with pytest.raises(ValueError,match='source row differs'):c.get('T',dict(r,event_count=9999))


def test_foreign_authority_ancestry_and_payload_tampering_fail_closed(tmp_path):
    _,r,p,root=cache(tmp_path)
    with pytest.raises(ValueError,match='authority'):EligibilityCache(root,dict(p,quote_policy='changed'))
    with pytest.raises(ValueError,match='ancestor'):EligibilityCache(root,dict(p,continuation={'parents':[]}))
    with (root/'counts.parquet').open('ab') as f:f.write(b'tamper')
    c=EligibilityCache(root,p)
    with pytest.raises(ValueError,match='part changed'):c.get('T',r)


def test_known_short_sessions_do_not_query_unknown_sessions_fail_open(tmp_path):
    c,r,_,_=cache(tmp_path)
    reader=source.CanonicalReader.__new__(source.CanonicalReader);reader.eligibility_cache=c;calls=[]
    def query(ticker,rows,gate):
        calls.append([r['source_date'] for r in rows])
        return {r['source_date']:4096 for r in rows},dict(queries=1,query_seconds=.01)
    reader._origin_counts_uncached=query
    counts,timing=reader.origin_counts('T',[r],lambda:pytest.fail('Cache hit admitted query'))
    assert counts=={r['source_date']:31} and timing['queries']==0 and not calls
    counts,timing=reader.origin_counts('T',[r,row('2021-01-05')],nullcontext)
    assert counts=={r['source_date']:31,'2021-01-05':4096}
    assert calls==[['2021-01-05']] and timing['cached_eligibility_sessions']==1


def test_dense_cached_counts_are_retained_not_filtered_as_short(tmp_path):
    c,r,_,_=cache(tmp_path,4096);reader=source.CanonicalReader.__new__(source.CanonicalReader);reader.eligibility_cache=c
    reader._origin_counts_uncached=lambda *a:pytest.fail('Cache entry should be used')
    counts,_=reader.origin_counts('T',[r],nullcontext)
    assert counts[r['source_date']]==4096  # Compiler still fetches/compiles this candidate.


def test_fragmented_cache_never_multiplies_queries(tmp_path):
    c,r,_,_=cache(tmp_path)
    reader=source.CanonicalReader.__new__(source.CanonicalReader);reader.eligibility_cache=c;calls=[]
    def query(ticker,rows,gate):
        calls.append(list(rows));return {x['source_date']:31 if x==r else 4096 for x in rows},dict(queries=1)
    reader._origin_counts_uncached=query
    rows=[row('2021-01-03'),r,row('2021-01-05')]
    found,timing=reader.origin_counts('T',rows,nullcontext)
    assert len(calls)==1 and calls[0]==rows and found[r['source_date']]==31
    assert timing['eligibility_cache_fragment_fallbacks']==1


def test_inventory_empty_proof_and_unknown_bounds(tmp_path):
    reader=source.CanonicalReader.__new__(source.CanonicalReader);reader.eligibility_cache=None;calls=[]
    def query(ticker,rows,gate):
        calls.extend(rows);return {r['source_date']:4095 for r in rows},dict(queries=1)
    reader._origin_counts_uncached=query
    r=row();empty=dict(r,first_sip_timestamp_us=clock(r['source_date'],20),last_sip_timestamp_us=clock(r['source_date'],20)+100)
    assert reader.origin_counts('T',[empty],nullcontext)[0][r['source_date']]==0 and not calls
    unknown=dict(r);unknown.pop('first_sip_timestamp_us')
    assert reader.origin_counts('T',[unknown],nullcontext)[0][r['source_date']]==4095 and calls


def test_ancestor_report_seal_and_complete_session_count_are_required(tmp_path):
    from research.bar_gpt.v5.run_build_phase2_eligibility_cache import collect_counts
    import time
    r=row();body=dict(binding='b',unit=dict(ticker='T',month=r['source_date']),stats=dict(source_origins=31),
        sampling=dict(reason='session_has_fewer_than4096_origins',session_origins=31))
    rows=pl.DataFrame([dict(key=digest(['T',r['source_date']]),report=json.dumps(body),hash=digest(body))])
    ledger=SimpleNamespace(manifest={'checkpoint':{'deferrals':[{}]},'parts':[]},checked_part=lambda p:rows)
    result,_=collect_counts(ledger,{'hash':'b'},{'T'},0,time.monotonic()+10)
    assert len(result)==1 and result[0]['origin_count']==31
    body['stats']['source_origins']=9  # A sampled suffix cannot stand in for a full session.
    rows=pl.DataFrame([dict(key=digest(['T',r['source_date']]),report=json.dumps(body),hash=digest(body))])
    assert collect_counts(ledger,{'hash':'b'},{'T'},0,time.monotonic()+10)[0]==[]
