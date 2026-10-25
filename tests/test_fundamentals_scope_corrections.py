import copy,json
from datetime import datetime,timezone
import pytest
from pipelines.certified_research import sec_html_additions as registry
from pipelines.certified_research.contracts import digest,literal
from pipelines.fundamentals.authority_corrections import retired_keys,exclusion
from tests.test_fundamentals_retained import live


def correction():
    return next((s,e) for s,e in registry.REGISTERED.items() if e.get('mode')=='native_statement_scope_correction' and e['replaces_fact_keys'])


def test_corrections_are_exact_native_metadata_and_retire_only_unbacked_groups(monkeypatch):
    pairs=[(s,e) for s,e in registry.REGISTERED.items() if e.get('mode')=='native_statement_scope_correction']
    assert len(pairs)==16 and sum(len(e['replaces_fact_keys']) for _,e in pairs)==65
    for s,e in pairs:assert registry.entry_for(s,e['plan']['cik'],e['plan']['document_hash'])==e
    assert retired_keys(['0000320193'])==[] and exclusion(['0000320193'])==''
    scope,entry=correction();bad=copy.deepcopy(entry);bad['replaces_fact_keys']=[['fake','0'*64]]
    body=copy.deepcopy(bad);body['plan'].pop('plan_hash');body['plan'].pop('review_scope_id');newscope=digest(body)
    bad['plan']['review_scope_id']=newscope;bad['plan']['plan_hash']=digest({k:v for k,v in bad['plan'].items() if k!='plan_hash'});monkeypatch.setitem(registry.REGISTERED,newscope,bad)
    with pytest.raises(ValueError,match='retirement keys'):registry.entry_for(newscope,bad['plan']['cik'],bad['plan']['document_hash'])


def test_live_scope_patch_preserves_parent_and_masks_bad_scope(live):
    from pipelines.fundamentals.retained_sec import publish
    from pipelines.fundamentals.schema import SOURCE,FACT,RECEIPT,CONTEXT,METRIC
    from pipelines.fundamentals.producer import names
    from pipelines.fundamentals.native_observations import financial_sql
    q,db,_,_=live;scope,e=correction();p=e['plan'];parent=e['parent_authority'];cik=p['cik']
    for table,columns,where in [('reference_source_v1',SOURCE,f"family='sec_original_qualified' AND entity_kind='issuer_cik' AND entity_id={literal(cik)} AND source_id={literal(parent)}"),('reference_fact_v1',FACT,f"entity_kind='issuer_cik' AND entity_id={literal(cik)} AND source_id={literal(parent)}"),('reference_batch_receipt_v1',RECEIPT,f'source_signature={literal(parent)}'),('reference_metric_definition_v1',METRIC,f'issuer_cik={literal(cik)}')]:
        q(f'INSERT INTO {db}.{table} ({names(columns)}) SELECT {names(columns)} FROM arte.{table} FINAL WHERE '+where,read=False)
    q(f'INSERT INTO {db}.reference_context_v1 ({names(CONTEXT)}) SELECT {names(CONTEXT)} FROM arte.reference_context_v1 FINAL WHERE context_id IN (SELECT context_id FROM {db}.reference_fact_v1 FINAL)',read=False)
    before=q(f'SELECT * FROM {db}.reference_fact_v1 FINAL ORDER BY metric,context_id');h=q(f'SELECT event_key,JSONExtractString(evidence_ref,\'document\') document FROM {db}.reference_source_v1 FINAL')[0]
    doc=dict(cik=cik,accession=h['event_key'],document=h['document'],content_sha256=p['document_hash'],review_scope_id=scope)
    class Claim:
        def check(self):pass
    first=publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim());assert first['state']=='published'
    assert publish(q,db,doc,'a'*64,datetime.now(timezone.utc),Claim())['state']=='reused'
    assert q(f'SELECT * FROM {db}.reference_fact_v1 FINAL WHERE source_id={literal(parent)} ORDER BY metric,context_id')==before
    rows=q(financial_sql([cik],datetime(2026,10,25,23,59,tzinfo=timezone.utc),database=db))
    assert any('CHANGES IN TRUST CORPUS' in r['statement_scope'].upper() for r in rows if first['source_id'] in r['source_ids'])
    retired={(parent,m,c) for m,c in e['replaces_fact_keys']}
    assert not any(any((s,r['metric'],r['context_id']) in retired for s in r['source_ids']) for r in rows)
