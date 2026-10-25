from datetime import datetime,timezone
from pipelines.certified_research.sec_html_reviewed_royalty_more_history import REVIEWED
from pipelines.certified_research.sec_html_reviewed import plan_for
from pipelines.certified_research.contracts import digest
from tests.test_fundamentals_retained import live


def test_all_older_trust_plans_bind_nearest_statement_and_preserve_native_units():
    assert len(REVIEWED)==182
    assert {p['cik'] for p in REVIEWED.values()}=={'0000319654','0000710752','0001371782','0001505413','0001520048','0001724009'}
    for h,p in REVIEWED.items():
        assert plan_for(h,p['cik'])==p and p['native_scope_policy']=='nearest_statement_heading_v1'
        assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
        assert all(f['unit']=='source:$' and f['concept']==f['native']['concept'] for f in p['facts'])


def test_reported_one_dollar_difference_is_not_rewritten_or_merged():
    p=REVIEWED['9f7070180d1a6123035d913186dccbdb1c30ace02cad51a8e10449b4ccc915b8']
    rows=[f for f in p['facts'] if f['label']=='Distributable income' and f['period_start']=='2026-01-01' and f['period_end']=='2026-03-31']
    assert {f['expected_value'] for f in rows}=={'3026801','3026802'}
    assert len({f['concept'] for f in rows})==2
    assert any('CHANGES' in f['native']['statement_scope'].upper() for f in rows)


def test_live_old_trust_native_source_path_and_reuse(live):
    from pipelines.fundamentals.retained_sec import publish
    q,db,_,_=live
    doc=dict(cik='0000319654',accession='0001193125-26-223126',document='pbt_10q_3-31-2026.htm',
        content_sha256='9f7070180d1a6123035d913186dccbdb1c30ace02cad51a8e10449b4ccc915b8',
        form='10-Q',day='2026-05-14',source_version_key='')
    class Claim:
        def check(self):pass
    first=publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())
    assert first['state']=='published' and first['rows']>0
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
