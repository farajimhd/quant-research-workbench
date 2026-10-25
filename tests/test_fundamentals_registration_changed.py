from datetime import datetime,timezone
from pipelines.certified_research.contracts import digest
from pipelines.certified_research.sec_html_reviewed_registration_changed import REVIEWED
from pipelines.certified_research.sec_html_reviewed import plan_for
from tests.test_fundamentals_retained import live


def test_plans_bind_native_subjects_units_columns_and_source_hashes():
    assert len(REVIEWED)==26
    for h,p in REVIEWED.items():
        assert plan_for(h,p['cik'])==p
        assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
        assert p['native_scope_policy']=='nearest_financial_heading_v2'
        assert all(f['unit']=='source:$' and f['native']['issuer_cik']==p['cik'] for f in p['facts'])
        assert all(f['native']['statement_scope']==f['table_proof']['heading'] for f in p['facts'])


def test_inception_period_is_not_treated_as_a_full_year():
    p=REVIEWED['9c7b790e9e1a313ead2f509e34387a6ac185f676c966d35b12afdb642aa75128']
    flows=[f for f in p['facts'] if f['period_start'] is not None]
    assert len(flows)==2
    assert {(f['period_start'],f['period_end'],f['expected_value']) for f in flows}=={('2021-06-21','2021-09-30','-274')}


def test_three_and_six_month_columns_and_predecessor_scopes_stay_separate():
    flow=[f for p in REVIEWED.values() if p['cik']=='0001864776' for f in p['facts'] if f['period_end']=='2023-06-30' and f['period_start']]
    assert {'2023-01-01','2023-04-01'} <= {f['period_start'] for f in flow}
    p=REVIEWED['e2dbac30ec6ec985ae99ccae63981f83d96bb39f4b076164dd980d63a7ec577d']
    subjects={f['native']['statement_scope'] for f in p['facts']}
    assert any('LLC and Rhodium JV LLC' in s for s in subjects)
    assert any('Enterprises Inc.' in s for s in subjects)
    assert all(f['scale']==3 for f in p['facts'])


def test_live_changed_registration_publication_and_reuse(live):
    from pipelines.fundamentals.retained_sec import publish
    q,db,_,_=live
    doc=dict(cik='0001873885',accession='0001493152-21-026156',document='forms-1.htm',
        form='S-1',day='2021-10-25',source_version_key='',
        content_sha256='9c7b790e9e1a313ead2f509e34387a6ac185f676c966d35b12afdb642aa75128')
    class Claim:
        def check(self):pass
    first=publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())
    assert first['state']=='published' and first['rows']==6
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
