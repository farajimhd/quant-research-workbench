from datetime import datetime,timezone
from pipelines.certified_research.sec_html_additions_registration import REGISTERED
from pipelines.certified_research.sec_html_additions import entry_for
from pipelines.fundamentals.additive import identities
from tests.test_fundamentals_retained import live


def test_registered_share_additions_preserve_parent_and_explicit_units():
    assert len(REGISTERED)==8
    rows=[]
    for scope,entry in REGISTERED.items():
        p=entry['plan'];assert entry_for(scope,p['cik'],p['document_hash'])==entry
        for f in p['facts']:
            assert f['unit'] in ('shares','source:$/shares')
            if f['unit']=='source:$/shares':assert f['scale']==0
            if p['cik']=='0002056611' and f['unit']=='shares':assert f['scale']==3
        rows+=identities([dict(f,value=f['expected_value'],dimensions=f.get('dimensions',())) for f in p['facts']])
    assert len(rows)==52


def test_live_share_supplement_preserves_parent_and_reuses(live):
    from pipelines.fundamentals.retained_sec import publish
    from pipelines.fundamentals.schema import SOURCE,FACT,RECEIPT
    from pipelines.fundamentals.producer import names
    q,db,_,_=live;scope=next(iter(REGISTERED));entry=REGISTERED[scope];parent=entry['parent_authority'];cik=entry['plan']['cik']
    doc=dict(cik=cik,accession='0001104659-25-097958',document='tm2516611-8_s1.htm',
        form='S-1',day='2025-10-08',source_version_key='',content_sha256=entry['plan']['document_hash'],review_scope_id=scope)
    for table,columns,predicate in [('reference_source_v1',SOURCE,"entity_kind='issuer_cik' AND entity_id='"+cik+"' AND source_id='"+parent+"'"),
        ('reference_fact_v1',FACT,"entity_kind='issuer_cik' AND entity_id='"+cik+"' AND source_id='"+parent+"'"),
        ('reference_batch_receipt_v1',RECEIPT,"source_signature='"+parent+"'")]:
        q(f'INSERT INTO {db}.{table} ({names(columns)}) SELECT {names(columns)} FROM arte.{table} FINAL WHERE '+predicate,read=False)
    before=q(f"SELECT * FROM {db}.reference_fact_v1 FINAL WHERE source_id='{parent}' ORDER BY metric,context_id")
    class Claim:
        def check(self):pass
    first=publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())
    assert first['state']=='published' and first['rows']==8
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
    assert q(f"SELECT * FROM {db}.reference_fact_v1 FINAL WHERE source_id='{parent}' ORDER BY metric,context_id")==before
