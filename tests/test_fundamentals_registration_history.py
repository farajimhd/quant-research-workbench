from datetime import datetime, timezone
from pipelines.certified_research.contracts import digest
from pipelines.certified_research.sec_html_reviewed_registration_history import REVIEWED
from pipelines.certified_research.sec_html_reviewed import plan_for
from tests.test_fundamentals_retained import live


def test_exact_registration_plans_preserve_source_identity_and_owned_headings():
    assert len(REVIEWED)==25
    for h,p in REVIEWED.items():
        assert p['document_hash']==h and p['native_scope_policy']=='nearest_financial_heading_v2'
        assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
        assert plan_for(h,p['cik'])==p
        assert plan_for(h,'9999999999') is None
        assert all(f.get('table_proof',{}).get('heading') for f in p['facts'])


def test_live_exact_registration_source_publication_and_reuse(live):
    from pipelines.fundamentals.retained_sec import publish
    q,db,_,_=live
    doc=dict(cik='0001776612',accession='0001104659-26-017032',document='tm2516611-20_s1a.htm',
        form='S-1/A',day='2026-02-18',source_version_key='',
        content_sha256='8b0af925eb9d5194aa85f224470bb54f642a2e75f6736dbe0456858f20f1e27c')
    class Claim:
        def check(self):pass
    first=publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())
    assert first['state']=='published' and first['rows']==12
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
