import json
import re
from datetime import date
from dataclasses import replace
import pytest

from src.market_engine import columnar_history_product as history
from src.market_engine.feature_source_plan import FeatureDependency, FeatureSourcePlan, UNIT, SEAL, TABLES
from pipelines.market_sip.events.feature_source_publication import publish_feature_source_plan
from src.backend.qmd_feature_source_plan import read_feature_source_plan
from src.backend.qmd_columnar_history import permission_preflight


def encoded(rows):return '\n'.join(json.dumps(r,default=str) for r in rows)


class Owner:
    def assert_owner(self):pass


class Store:
    def __init__(self):
        self.rows={t.name:[] for t in TABLES};self.writes=[];self.bad_disk=False


class Client:
    def __init__(self,store,writer=False):self.store,self.writer=store,writer

    def execute(self,query):
        user='writer' if self.writer else 'reader'
        if query=='SELECT currentUser()':return user
        if query=='SHOW GRANTS FINAL':
            reads=[('q_live',t.name) for t in history.TABLES+TABLES]+[('system',n) for n in ('tables','parts','columns','storage_policies','data_skipping_indices')]
            result=[f'GRANT SELECT ON {db}.{name} TO {user}' for db,name in reads]
            if self.writer:result.extend(f'GRANT INSERT ON q_live.{t.name} TO {user}' for t in history.TABLES+TABLES)
            return '\n'.join(result)
        if 'FROM system.storage_policies' in query:return encoded([dict(disks=['live_market_ssd'])])
        if 'FROM system.tables' in query:return encoded([dict(name=t.name,engine='MergeTree',storage_policy='live_market_ssd',partition_key=t.partition,sorting_key=t.order) for t in TABLES])
        if 'FROM system.columns' in query:return encoded([dict(table=t.name,name=n,type=k) for t in TABLES for n,k in t.columns])
        if 'FROM system.data_skipping_indices' in query:return ''
        if 'FROM system.parts' in query:return encoded([dict(table=UNIT.name,disk_name='default')]) if self.store.bad_disk else ''
        if query.startswith('INSERT INTO'):
            assert self.writer
            header,body=query.split('\n',1)
            table=next(t.name for t in TABLES if t.name in header)
            self.store.rows[table].extend(json.loads(line) for line in body.splitlines() if line)
            self.store.writes.append(table)
            return ''
        table=next((t.name for t in TABLES if f'FROM q_live.{t.name} ' in query),None)
        if table:
            rows=list(self.store.rows[table])
            found=re.search(r"plan_hash='([a-f0-9]+)'",query)
            rows=[r for r in rows if not found or r['plan_hash']==found[1]]
            if 'count() AS n' in query:return encoded([dict(n=str(len(rows)),u=str(len({r['ordinal'] for r in rows})))])
            bounds=re.search(r'ordinal>=(\d+) AND ordinal<(\d+)',query)
            if bounds:rows=[r for r in rows if int(bounds[1])<=r['ordinal']<int(bounds[2])]
            if 'ORDER BY ordinal' in query:rows.sort(key=lambda r:r['ordinal'])
            limit=re.search(r'LIMIT (\d+)',query)
            if limit:rows=rows[:int(limit[1])]
            return encoded(rows)
        raise AssertionError(query)


def plan():
    dependency=FeatureDependency('bars','arte','bars_v1',date(2026,7,30),'ABC','attempt',3,'a'*64)
    return FeatureSourcePlan('b'*64,'c'*64,date(2026,7,30),14400,14800,2,(dependency,))


def test_source_children_precede_seal_and_cold_readback_and_reuse_are_exact():
    store=Store();reader,writer=Client(store),Client(store,True);source=plan();calls=[]
    assert publish_feature_source_plan(reader,writer,source,ownership=Owner(),verify_sources=lambda r:calls.append(r))=='published'
    assert store.writes==[UNIT.name,SEAL.name]
    assert read_feature_source_plan(reader,source.plan_hash)==source
    assert len(calls)==2
    assert publish_feature_source_plan(reader,writer,source,ownership=Owner(),verify_sources=lambda r:None)=='skipped_verified'
    assert store.writes==[UNIT.name,SEAL.name]
    store.rows[UNIT.name].append(store.rows[UNIT.name][0])
    with pytest.raises(ValueError,match='duplicate'):
        read_feature_source_plan(reader,source.plan_hash)


def test_changed_source_leaves_unsealed_attempt_and_retry_requires_reconciliation():
    store=Store();reader,writer=Client(store),Client(store,True);source=plan();calls=[]
    def verify(reader):
        calls.append(1)
        if len(calls)==2:raise ValueError('source changed')
    with pytest.raises(ValueError,match='source changed'):
        publish_feature_source_plan(reader,writer,source,ownership=Owner(),verify_sources=verify)
    assert store.rows[UNIT.name] and not store.rows[SEAL.name]
    with pytest.raises(ValueError,match='reconciliation'):
        publish_feature_source_plan(reader,writer,source,ownership=Owner(),verify_sources=lambda r:None)
    assert store.writes==[UNIT.name]


def test_wrong_storage_fails_before_any_writer_and_legacy_grant_gate_accepts_exact_extension():
    store=Store();store.bad_disk=True
    with pytest.raises(ValueError,match='outside'):
        publish_feature_source_plan(Client(store),Client(store,True),plan(),ownership=Owner(),verify_sources=lambda r:None)
    assert not store.writes
    permission_preflight(Client(store,True),writable=True)
    permission_preflight(Client(store,True),writable=True,feature_sources=True)
    permission_preflight(Client(store),feature_sources=True)


def test_owned_plan_cache_reuses_only_unchanged_actual_parts_and_seals():
    from src.backend.qmd_feature_source_plan import FeatureSourcePlanCache
    store=Store();store.epoch=0;store.cold_reads=0
    class PartsClient(Client):
        def execute(self,query):
            if 'FROM system.parts' in query and 'hash_of_all_files' in query:
                return encoded([dict(table=t.name,name='part',disk_name='live_market_ssd',hash_of_all_files=str(store.epoch)) for t in TABLES])
            if f'SELECT * FROM q_live.{UNIT.name}' in query:store.cold_reads+=1
            return super().execute(query)
    reader,writer=PartsClient(store),Client(store,True)
    publish_feature_source_plan(reader,writer,plan(),ownership=Owner(),verify_sources=lambda r:None)
    cache=FeatureSourcePlanCache()
    assert cache.read(reader,plan().plan_hash)==plan()
    before=store.cold_reads
    assert cache.read(reader,plan().plan_hash)==plan() and store.cold_reads==before
    store.epoch+=1
    assert cache.read(reader,plan().plan_hash)==plan() and store.cold_reads==before+1
    store.rows[UNIT.name][0]['content_hash']='d'*64;store.epoch+=1
    with pytest.raises(ValueError,match='hash'):
        cache.read(reader,plan().plan_hash)
