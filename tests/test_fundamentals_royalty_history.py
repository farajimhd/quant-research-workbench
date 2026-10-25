from datetime import datetime,timezone
from pipelines.certified_research.sec_html_reviewed_royalty_history import REVIEWED
from pipelines.certified_research.sec_html_reviewed import plan_for
from pipelines.certified_research.contracts import digest
from tests.test_fundamentals_retained import live


def test_every_retained_trust_plan_preserves_native_units_dates_and_source_positions():
    assert len(REVIEWED)==93
    assert {p['cik'] for p in REVIEWED.values()}=={'0000313364','0000319655','0000881787'}
    for h,p in REVIEWED.items():
        assert plan_for(h,p['cik'])==p
        assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
        assert len({digest(f['locator']) for f in p['facts']})==len(p['facts'])
        for f in p['facts']:
            assert f['unit']=='source:$' and f['concept']==f['native']['concept']
            assert f['native']['issuer_cik']==p['cik'] and f['native']['statement_scope']==f['table_proof']['heading']
            assert f['period_start'] is None or f['period_start']<=f['period_end']
            assert f['scale']==0 and f['unit_locator']['table']==f['locator']['table']


def test_latest_selected_trust_values_keep_source_scope_and_negative_income():
    h='18c30b73d746dea0ec48870aa874b199f43a176af64dece4ff511f8edb1e154c';p=REVIEWED[h]
    f=next(f for f in p['facts'] if f['label']=='Distributable income (Loss)' and f['period_start']=='2026-04-01' and f['period_end']=='2026-06-30')
    assert f['expected_value']=='-193957' and f['unit']=='source:$'
    h='5d88ee273b8bc51acff02ad80b6714f5a4549408663cde71e86f7b0f4f067a61';p=REVIEWED[h]
    f=next(f for f in p['facts'] if f['label']=='Net profits income' and f['period_start']=='2026-01-01' and f['period_end']=='2026-06-30')
    assert f['expected_value']=='1862336'


def test_live_trust_native_publication_and_reuse(live):
    from pipelines.fundamentals.retained_sec import publish
    q,db,_,_=live
    doc=dict(cik='0000313364',accession='0001104659-26-062604',document='tm2610246-1_10q.htm',
        content_sha256='5774f9fa11911aaf86e75f85a79f518230371420b933250154b95af76476e972',
        form='10-Q',day='2026-05-15',source_version_key='')
    class Claim:
        def check(self):pass
    stamp=datetime.now(timezone.utc);first=publish(q,db,doc,'a'*64,stamp,Claim())
    assert first['state']=='published' and first['rows']==16
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
    assert q(f'SELECT DISTINCT unit FROM {db}.reference_context_v1 FINAL')==[dict(unit='source:$')]
