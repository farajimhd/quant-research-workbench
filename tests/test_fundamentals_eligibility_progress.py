from datetime import datetime,timezone,timedelta
import pytest
from pipelines.fundamentals.eligibility import require_eligible
from pipelines.fundamentals.queries import observations_sql
from pipelines.fundamentals.progress import estimate,render
from pipelines.fundamentals.family import retained_family


def test_ubs_is_blocked_by_identity_not_ticker_collision():
    for kind,entity in [('issuer_cik','1610520'),('issuer_cik','0001114446'),
                        ('issuer','issuer:cik:0001610520'),('security','security:d9dcc9a4dff063081993f594')]:
        with pytest.raises(ValueError,match='UBS'):require_eligible(kind,[entity])
    require_eligible('security',['security:edfefaf235f84c6f342de5f3'])
    with pytest.raises(ValueError,match='UBS'):
        observations_sql('arte','issuer_cik',['0001610520'],['revenue'],datetime.now(timezone.utc))


def test_eta_never_uses_reuse_for_new_work():
    s=dict(state='running',workers=4,remaining_by_family={'sec_bulk':100},samples={'sec_bulk':{'unchanged':[.1]*20}})
    assert estimate(s) is None
    s['samples']['sec_bulk']['published']=[10,12,11]
    assert estimate(s)==275
    s['remaining_by_family']['borrow']=1
    assert estimate(s) is None


@pytest.mark.parametrize('width,height',[(40,16),(80,24),(120,30)])
def test_monitor_stale_failure_and_bounds(width,height):
    now=datetime.now(timezone.utc)
    s=dict(state='running',mode='backfill',counts=dict(published=2,unchanged=3,audited=0,failed=1),
           active={},queued=4,elapsed=10,total=10,error='A failed unit needs diagnosis',
           updated_at=(now-timedelta(seconds=90)).isoformat())
    result=render(s,width,height,now)
    assert 'STALE' in result and 'ERROR' in result and '5/10' in result
    assert max(map(len,result.splitlines()))<=width and len(result.splitlines())<=height


def test_family_deduplicates_diamond_paths_and_never_sums():
    root='0000000001';a='0000000002';b='0000000003';c='0000000004'
    def edge(p,ch):return dict(relationship_id=p+ch,parent_cik=p,child_cik=ch,evidence_accession_number='proof',evidence_url='sec')
    calls=[]
    def query(sql):
        calls.append(sql)
        return [edge(root,a),edge(root,b)] if len(calls)==1 else [edge(a,c),edge(b,c)] if len(calls)==2 else []
    result=retained_family(query,root,datetime.now(timezone.utc))
    assert result['entities']==[root,a,b,c] and result['aggregation']=='none'
    assert result['completeness']=='unknown' and not result['public_time_qualified']
    assert all('first_seen_at_utc<=' in s and 'inserted_at<=' in s for s in calls)


def test_family_cycles_and_bounds_fail_without_truncation():
    root='0000000001';child='0000000002';calls=[]
    def query(sql):
        calls.append(sql);p,c=(root,child) if len(calls)==1 else (child,root)
        return [dict(relationship_id=p+c,parent_cik=p,child_cik=c,evidence_accession_number='proof',evidence_url='sec')]
    with pytest.raises(ValueError,match='Cyclic'):retained_family(query,root,datetime.now(timezone.utc))
    calls.clear()
    with pytest.raises(ValueError,match='bound'):retained_family(query,root,datetime.now(timezone.utc),maximum_entities=1)
