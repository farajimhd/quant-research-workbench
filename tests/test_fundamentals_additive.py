"""Additive publication cannot replace a completed authority or duplicate keys."""
import copy,json
import pytest
from pipelines.certified_research import sec_html_additions as registry
from pipelines.certified_research.contracts import digest
from pipelines.fundamentals.additive import identities,parent_state


def test_registered_addition_binds_parent_and_distinct_cells(monkeypatch):
    scope=next(iter(registry.REGISTERED));entry=registry.REGISTERED[scope];p=entry['plan']
    assert len(registry.entry_for(scope,p['cik'],p['document_hash'])['plan']['facts'])==6
    with pytest.raises(ValueError):registry.entry_for('0'*64,p['cik'],p['document_hash'])
    with pytest.raises(ValueError):registry.entry_for(scope,'0000320193',p['document_hash'])
    bad=copy.deepcopy(entry);bad['parent_authority']='0'*64
    monkeypatch.setitem(registry.REGISTERED,scope,bad)
    with pytest.raises(ValueError,match='seal'):registry.entry_for(scope,p['cik'],p['document_hash'])


def test_parent_exact_values_receipt_and_nonoverlap():
    g=dict(concept='{http://fasb.org/us-gaap/2023}Assets',period_start=None,
           period_end='2023-12-31',dimensions=(),unit='USD',value='123')
    n=dict(g,concept='{http://fasb.org/us-gaap/2023}AssetsCurrent')
    rows=identities([g]);headers=[dict(source_id='parent',available_at='2024-01-01 00:00:00',
        evidence_ref=json.dumps(dict(document='doc')),recipe_hash='recipe')]
    receipts=[dict(batch_key='b',fact_rows=1,output_hash='proof',recipe_hash='recipe',population_hash='pop')]
    def q(sql):
        return headers if 'reference_source_v1' in sql else receipts if 'reference_batch_receipt_v1' in sql else rows
    state=parent_state(q,'arte','0000320193','acc','doc','parent',[g],[n])
    assert state['headers'][0]['available_at']=='2024-01-01 00:00:00'
    with pytest.raises(ValueError,match='repeats'):parent_state(q,'arte','0000320193','acc','doc','parent',[g],[g])
    rows[0]['text_value']='124'
    with pytest.raises(ValueError,match='facts differ'):parent_state(q,'arte','0000320193','acc','doc','parent',[g],[n])


from tests.test_fundamentals_retained import live

def test_live_addition_preserves_parent_reuses_and_rejects_corruption(live):
    from datetime import datetime,timezone
    from pipelines.fundamentals.retained_sec import publish
    from pipelines.fundamentals.schema import SOURCE,FACT,RECEIPT
    from pipelines.fundamentals.producer import names
    q,db,_,_=live
    entry=next(iter(registry.REGISTERED.values()));parent=entry['parent_authority']
    cik=entry['plan']['cik'];scope=entry['plan']['review_scope_id']
    document=dict(cik=cik,accession='0001829126-24-000347',document='amphitritedigital_s1a.htm',
        form='S-1/A',day='2024-01-23',source_version_key='',content_sha256=entry['plan']['document_hash'],review_scope_id=scope)
    for table,columns,predicate in [('reference_source_v1',SOURCE,"family='sec_original_qualified' AND entity_kind='issuer_cik' AND entity_id='"+cik+"' AND source_id='"+parent+"'"),
        ('reference_fact_v1',FACT,"entity_kind='issuer_cik' AND entity_id='"+cik+"' AND source_id='"+parent+"'"),
        ('reference_batch_receipt_v1',RECEIPT,"source_signature='"+parent+"'")]:
        q(f'INSERT INTO {db}.{table} ({names(columns)}) SELECT {names(columns)} FROM arte.{table} FINAL WHERE '+predicate,read=False)
    class Claim:
        def check(self):pass
    before=q(f'SELECT * FROM {db}.reference_fact_v1 FINAL ORDER BY metric,context_id')
    first=publish(q,db,document,'a'*64,datetime.now(timezone.utc),Claim())
    assert first['rows']==6 and first['state']=='published'
    assert publish(q,db,document,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
    assert q(f"SELECT * FROM {db}.reference_fact_v1 FINAL WHERE source_id='{parent}' ORDER BY metric,context_id")==before
    assert q(f"SELECT toString(available_at) at FROM {db}.reference_source_v1 FINAL WHERE source_id='{first['source_id']}'")==[dict(at='2024-01-23 16:18:35.000000000')]
    q(f"ALTER TABLE {db}.reference_fact_v1 UPDATE text_value='CORRUPT' WHERE source_id='{parent}' SETTINGS mutations_sync=2",read=False)
    with pytest.raises(ValueError,match='parent facts differ'):
        publish(q,db,document,'a'*64,datetime.now(timezone.utc),Claim())


def test_all_additions_bind_an_unchanged_parent_and_unique_source_positions():
    from pipelines.certified_research.sec_html_reviewed import plan_for
    for scope,entry in registry.REGISTERED.items():
        p=entry['plan'];verified=registry.entry_for(scope,p['cik'],p['document_hash'])
        assert verified['parent_authority']==entry['parent_authority']
        base=plan_for(p['document_hash'],p['cik'])
        assert base['plan_hash']==entry['parent_plan_hash']
        assert p['plan_hash']==digest({k:v for k,v in p.items() if k!='plan_hash'})
        assert len({digest(f['locator']) for f in p['facts']})==len(p['facts'])
