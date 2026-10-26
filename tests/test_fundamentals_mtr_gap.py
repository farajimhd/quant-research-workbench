from datetime import datetime,timezone
from pipelines.certified_research.sec_html_reviewed_mtr_gap import REVIEWED
from pipelines.certified_research.sec_html_reviewed import plan_for
from pipelines.certified_research.contracts import digest
from tests.test_fundamentals_retained import live


def test_mtr_june_quarter_native_periods_and_accounting_are_preserved():
    assert len(REVIEWED)==1
    h,p=next(iter(REVIEWED.items()))
    assert plan_for(h,'0000313364')==p and plan_for(h,'0001371782') is None
    assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
    assert p['native_scope_policy']=='nearest_statement_heading_v1'
    assert len(p['facts'])==34 and all(f['unit']=='source:$' and f['scale']==0 for f in p['facts'])
    assert {(f['period_start'],f['period_end']) for f in p['facts'] if f['period_start']}=={
        ('2025-01-01','2025-06-30'),('2025-04-01','2025-06-30'),
        ('2026-01-01','2026-06-30'),('2026-04-01','2026-06-30')}
    cash=[f for f in p['facts'] if f['label']=='Cash and short-term investments' and f['period_end']=='2026-06-30']
    assert len(cash)==1 and cash[0]['expected_value']=='2066701'


def test_live_mtr_june_source_path_public_time_and_reuse(live):
    from pipelines.fundamentals.retained_sec import publish
    q,db,_,_=live
    doc=dict(cik='0000313364',accession='0001104659-26-097086',document='tm2616156-1_10q.htm',
        form='10-Q',day='2026-08-14',
        content_sha256='f1275ee89f87c2ba3f69149f027792f81b3b13ec3beaa1fc98d4ce81859e09e5',
        source_version_key='fb301c46176d8baea4f45ff6e4da3552a79bfc73ba33fb323568a286933be283')
    class Claim:
        def check(self):pass
    first=publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())
    assert first['state']=='published' and first['rows']==34
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
    clock=q(f"SELECT toString(available_at) available_text,observed_at>available_at separate_clocks FROM {db}.reference_source_v1 FINAL WHERE source_id='"+first['source_id']+"'")
    assert clock==[dict(available_text='2026-08-14 20:05:04.000000000',separate_clocks=1)]
