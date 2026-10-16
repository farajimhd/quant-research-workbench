from datetime import date
from types import SimpleNamespace
import json

import pytest

from pipelines.certified_research import normalized_v7 as nv


class Claim:
    def check(self): pass


def engine():
    return SimpleNamespace(rows=[dict(id='level',origin_session='2025-12-31',qualified=True,
        historical=True,role='support',price=10.,lower=9.,upper=11.,association_radius=2.,
        fit=dict(status='estimated',count=1,center=10.,scale=.5),
        observations=[dict(price=10.,resolution=.01,at=1.,resolved_at=2.,role='support',session='2025-12-31')])])


def test_normalized_geometry_and_observation_relationship():
    levels,observations=nv.project(engine(),{'state_hash':'a'*64},3000000)
    assert levels[0]['level_id']==observations[0]['level_id']=='level'
    assert levels[0]['fit_center']==10.
    assert observations[0]['at_us']==1000000
    assert observations[0]['resolved_at_us']==2000000
    assert 'payload' not in levels[0]
    with pytest.raises(ValueError,match='Future'): nv.project(engine(),{},1500000)


def test_projection_never_truncates_or_admits_duplicate_identity():
    with pytest.raises(ValueError,match='bound'): nv.project(engine(),{},3000000,maximum_observations=0)
    value=engine();value.rows.append(dict(value.rows[0]))
    with pytest.raises(ValueError,match='Duplicate'): nv.project(value,{},3000000)
    value=engine();value.rows[0]['lower']=20.
    with pytest.raises(ValueError,match='geometry'): nv.project(value,{},3000000)


def test_rebuild_origin_and_verified_seed_requirement():
    result=nv.rebuild_requirement(1,'AAPL',date(2026,1,2))
    assert result['status']=='blocked_required_context_bars'
    assert result['required_start']=='2025-01-01'
    result=nv.rebuild_requirement(1,'IPO',date(2026,1,2),first_valid_session=date(2025,8,1),origin_evidence_hash='b'*64)
    assert result['required_start']=='2025-08-01'
    with pytest.raises(ValueError): nv.rebuild_requirement(1,'AAPL',date(2026,1,2),verified_ah=(dict(product='legacy'),{}))


class MemoryQuery:
    def __init__(self): self.rows=[];self.writes=0
    def __call__(self,sql,read=True):
        if sql.startswith('INSERT'):
            self.rows.extend(json.loads(r) for r in sql.split('\n')[1:]);self.writes+=1;return []
        if 'count()' in sql: return [dict(n=len(self.rows))]
        return sorted(self.rows,key=lambda r:r['row_index'])


def test_restart_reconciles_partial_rows_and_compares_geometry():
    rows,_=nv.project(engine(),{},3000000)
    query=MemoryQuery()
    nv._materialize(query,'arte',Claim(),'research_v7_level_v1','listing_key=1',rows)
    assert query.writes==1
    nv._materialize(query,'arte',Claim(),'research_v7_level_v1','listing_key=1',rows)
    assert query.writes==1
    query.rows=[]
    with pytest.raises(ValueError,match='missing'):
        nv._materialize(query,'arte',Claim(),'research_v7_level_v1','listing_key=1',rows,allow_insert=False)
    nv._materialize(query,'arte',Claim(),'research_v7_level_v1','listing_key=1',rows)
    query.rows[0]['price']=99.
    with pytest.raises(ValueError,match='differs'):
        nv._materialize(query,'arte',Claim(),'research_v7_level_v1','listing_key=1',rows)


def test_storage_preflight_rejects_backup_disk():
    def query(sql):
        if 'system.storage_policies' in sql: return [dict(disks=['live_market_ssd'])]
        if 'system.tables' in sql: return [dict(name=n,storage_policy='live_market_ssd') for n in nv.LAYOUTS]
        if 'system.parts' in sql: return [dict(table='research_v7_level_v1',disk_name='default')]
        raise AssertionError(sql)
    with pytest.raises(ValueError,match='outside'): nv.preflight(query,'arte',Claim())


def test_bounded_queue_without_frozen_context_blocks_without_scanning():
    def forbidden(sql): raise AssertionError('No source context permits scanning: '+sql)
    items=[dict(listing_key=1,ticker='AAPL',before='2026-01-02')]
    result=nv.plan_queue(forbidden,'arte',Claim(),items,'a'*64,({'rule':1},))
    assert result['blocked']==1 and result['ready']==0
    assert result['items'][0]['required_start']=='2025-01-01'
    assert 'payload' not in str(result)
    with pytest.raises(ValueError,match='Duplicate'):
        nv.plan_queue(forbidden,'arte',Claim(),items*2,'a'*64,({'rule':1},))
