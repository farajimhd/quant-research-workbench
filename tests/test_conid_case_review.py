from copy import deepcopy
from datetime import date
import json

import pytest

from pipelines.certified_research import conid_reconciliation as r, conid_case_review as v
from test_conid_reconciliation import sources


def fixture():
    data=sources()
    data['listings'].append(dict(data['listings'][0],listing_id='l2',conid='456'))
    baseline=r.reconcile(data,date(2026,1,1),date(2026,10,2))
    definition=dict(conid=456,ticker='ABC.A',name='ABC',assetClass='STK',currency='USD',
        countryCode='US',listingExchange='NASDAQ',isUS=True,type='COMMON')
    payload=dict(ticker='ABC.A',composite_figi='F1',share_class_figi='S1',cik='1',ibkr_candidates=[definition])
    receipt=dict(source_mapping_id='m1',source_system='massive',source_entity_key='ABC.A',
        mapped_entity_id='sym2',mapping_status='active',source_hash='e'*64,evidence_json=json.dumps(payload))
    symbol=dict(symbol_id='sym2',ticker='ABC.A',listing_id='l2',security_id='s1',conid='456',
        country='US',currency='USD',product='STK',issuer_id='i1',name='ABC',
        symbol_hash='a'*64,listing_hash='b'*64,security_hash='c'*64)
    evidence=dict(cases=baseline['rows'],symbols=[symbol],contracts=[receipt],issues=[],issuer_ciks=[],security_isins=[])
    return data,baseline,evidence


def run(data,baseline,evidence):return v.review(baseline,data,evidence)


def test_verified_new_contract_selects_from_retained_alternatives():
    data,baseline,evidence=fixture()
    result=run(data,baseline,evidence)
    assert result['rows'][0]['conid']==456
    assert result['summary']['identity_resolved']==1
    assert not result['summary']['canonical_issue_closure']


@pytest.mark.parametrize('change',['wrong_figi','wrong_share','wrong_cik','wrong_country','wrong_class','missing_provenance','wrong_name'])
def test_contract_reconciliation_fails_closed(change):
    data,baseline,evidence=fixture()
    payload=json.loads(evidence['contracts'][0]['evidence_json'])
    if change=='wrong_figi':payload['composite_figi']='other'
    if change=='wrong_share':payload['share_class_figi']='other'
    if change=='wrong_cik':payload['cik']='2'
    if change=='wrong_country':evidence['symbols'][0]['country']='GB'
    if change=='wrong_class':payload['ibkr_candidates'][0]['ticker']='ABC.B'
    if change=='missing_provenance':evidence['contracts'][0]['source_hash']=''
    if change=='wrong_name':payload['ibkr_candidates'][0]['name']='OTHER INDUSTRIES'
    evidence['contracts'][0]['evidence_json']=json.dumps(payload)
    assert run(data,baseline,evidence)['rows'][0]['decision']=='blocked'


def test_mapping_issues_are_never_automatically_closed():
    data,baseline,evidence=fixture()
    evidence['issues']=[dict(source_entity_key='ABC.A',issue_type='old_issue',source_hash='f'*64)]
    result=run(data,baseline,evidence)
    assert result['rows'][0]['decision']=='identity_resolved'
    assert 'canonical_mapping_issues_require_separate_closure' in result['rows'][0]['remaining_issues']


def test_timeline_conflict_keeps_historical_blocker():
    data,baseline,evidence=fixture()
    data['listings']=data['listings'][:1]
    data['coverage']=[dict(entity_key='figi:F1',security_id='s1',listing_id='l1',
        mapping_status='source_conflict',mapping_reason='latest_provider_ticker=OLD@2005-01-01; inventory_ticker=ABC.A',
        source_status='completed',source_hash='f'*64)]
    baseline=r.reconcile(data,date(2026,1,1),date(2026,10,2))
    evidence['contracts']=[];evidence['cases']=baseline['rows']
    result=run(data,baseline,evidence)
    assert result['rows'][0]['conid']==123
    assert 'historical_ticker_timeline_conflict' in result['rows'][0]['remaining_issues']


def test_actual_identity_conflict_is_not_relabelled_as_timeline_only():
    data,baseline,evidence=fixture()
    data['listings']=data['listings'][:1]
    data['coverage']=[dict(entity_key='figi:F1',security_id='s1',listing_id='l1',mapping_status='source_conflict',
        mapping_reason='multiple_security_figis',source_status='completed',source_hash='f'*64)]
    baseline=r.reconcile(data,date(2026,1,1),date(2026,10,2))
    evidence['contracts']=[];evidence['cases']=baseline['rows']
    assert run(data,baseline,evidence)['rows'][0]['decision']=='blocked'


def test_review_is_per_entity_and_not_per_distinct_ticker():
    data,baseline,evidence=fixture()
    duplicate=dict(data['entities'][0],entity_key='old',active=0)
    data['entities'].append(duplicate)
    baseline=r.reconcile(data,date(2026,1,1),date(2026,10,2));evidence['cases']=baseline['rows']
    result=run(data,baseline,evidence)
    assert result['summary']['reviewed']==2
    assert result['summary']['distinct_tickers']==1


def test_isin_checksum_not_just_prefix():
    assert v.valid_us_isin('US0378331005')
    assert not v.valid_us_isin('US0378331006')
    assert not v.valid_us_isin('GB0378331005')


def test_review_fingerprint_changes_when_evidence_changes():
    data,baseline,evidence=fixture()
    before=run(data,baseline,evidence)
    evidence['contracts'][0]['source_hash']='f'*64
    assert before['review_hash']!=run(data,baseline,evidence)['review_hash']


def publisher_fixture(monkeypatch):
    from pipelines.certified_research.publication import DictionaryClaim
    from pipelines.certified_research import sec_share_capture
    monkeypatch.setattr(v,'verify',lambda *a,**kw:None)
    claim=object.__new__(DictionaryClaim);claim.dictionary_database='q_live';claim.check=lambda:None
    stored={v.table().name:[],v.receipt_table().name:[],v.evidence_table().name:[]}
    def query(sql,read=True):
        if 'system.disks' in sql:return [{'free_space':1024**3,'keep_free_space':0}]
        if sql.startswith('CREATE'):return []
        name=next(n for n in stored if n in sql)
        if sql.startswith('INSERT'):
            stored[name].extend(json.loads(line) for line in sql.split('FORMAT JSONEachRow\n')[1].splitlines())
            return []
        if sql.startswith('SELECT DISTINCT'):
            return [json.loads(p) for p in sorted({json.dumps(r,sort_keys=True) for r in stored[name]})]
        return deepcopy(stored[name])
    return claim,stored,query


def test_review_publication_exact_resume(monkeypatch):
    data,base,evidence=fixture();result=run(data,base,evidence)
    claim,stored,query=publisher_fixture(monkeypatch)
    assert v.publish(query,result,lambda:None)['inserted']==1
    assert v.publish(query,result,lambda:None)['inserted']==0
    assert len(stored[v.receipt_table().name])==1


def test_changed_source_cannot_seal_partial_cases(monkeypatch):
    data,base,evidence=fixture();result=run(data,base,evidence)
    claim,stored,query=publisher_fixture(monkeypatch);calls=[]
    def guard():
        calls.append(1)
        if len(calls)>1:raise RuntimeError('source changed')
    with pytest.raises(RuntimeError,match='source changed'):v.publish(query,result,guard)
    assert len(stored[v.table().name])==1
    assert not stored[v.receipt_table().name]


def test_us_isin_and_sec_pair_require_explicit_issuer_followup():
    data,base,evidence=fixture()
    data['identifiers']=[];data['sec_tickers']=[dict(cik='1',ticker='ABC.A',source_file_id='sec-1')]
    base=r.reconcile(data,date(2026,1,1),date(2026,10,2));evidence['cases']=base['rows'];evidence['contracts']=[]
    evidence['security_isins']=[dict(security_id='s1',isin='US0378331005',source_hash='e'*64)]
    result=run(data,base,evidence)
    assert result['rows'][0]['conid']==456
    assert 'canonical_issuer_requires_sec_cik_binding' in result['rows'][0]['remaining_issues']
    evidence['security_isins'][0]['isin']='US0378331006'
    assert run(data,base,evidence)['rows'][0]['decision']=='blocked'


def test_conflicting_issuer_cik_blocks_name_and_isin_fallback():
    data,base,evidence=fixture();data['identifiers']=[]
    data['sec_tickers']=[dict(cik='1',ticker='ABC.A',source_file_id='sec-1')]
    base=r.reconcile(data,date(2026,1,1),date(2026,10,2));evidence['cases']=base['rows'];evidence['contracts']=[]
    evidence['security_isins']=[dict(security_id='s1',isin='US0378331005',source_hash='e'*64)]
    evidence['issuer_ciks']=[dict(issuer_id='i1',cik='2',source_hash='e'*64)]
    assert run(data,base,evidence)['rows'][0]['decision']=='blocked'


def test_identical_concurrent_retry_is_idempotent(monkeypatch):
    data,base,evidence=fixture();result=run(data,base,evidence)
    claim,stored,query=publisher_fixture(monkeypatch)
    stored[v.table().name]=deepcopy(result['rows']*2)
    assert v.publish(query,result,lambda:None)['inserted']==0
    assert len(stored[v.receipt_table().name])==1


def test_competing_content_is_retained_and_rejected(monkeypatch):
    from pipelines.certified_research.contracts import digest
    data,base,evidence=fixture();result=run(data,base,evidence)
    claim,stored,query=publisher_fixture(monkeypatch)
    competing=dict(result['rows'][0],conid=999)
    competing['record_hash']=digest({k:val for k,val in competing.items() if k not in ('review_hash','record_hash')})
    stored[v.table().name]=[result['rows'][0],competing]
    with pytest.raises(ValueError,match='Conflicting'):v.publish(query,result,lambda:None)
    assert not stored[v.receipt_table().name]
    assert len(stored[v.table().name])==2


def test_incorrect_summary_cannot_receive_receipt(monkeypatch):
    data,base,evidence=fixture();result=run(data,base,evidence)
    claim,stored,query=publisher_fixture(monkeypatch)
    result['summary']['reviewed']=999
    with pytest.raises(ValueError,match='counts/status'):v.publish(query,result,lambda:None)


def test_alias_dates_and_source_proofs_are_retained():
    data,base,evidence=fixture()
    evidence['aliases']=[dict(entity_key='figi:F1',ticker='OLD',event_date='2025-01-01',
        source_hash='f'*64,response_hash='e'*64)]
    result=run(data,base,evidence)
    assert result['rows'][0]['historical_aliases']==['OLD']
    proof=json.loads(result['proofs'][0]['payload'])
    assert proof['historical_aliases'][0]['event_date']=='2025-01-01'


def test_other_symbol_contract_is_not_substituted_for_wire_ticker():
    data,base,evidence=fixture()
    data['coverage']=[dict(entity_key='figi:F1',security_id='s1',listing_id='l1',mapping_status='mapped',
        source_status='completed',source_hash='f'*64)]
    base=r.reconcile(data,date(2026,1,1),date(2026,10,2));evidence['cases']=base['rows'];evidence['contracts']=[]
    evidence['symbols'][0].update(listing_id='l1',conid='123')
    result=run(data,base,evidence)
    assert result['rows'][0]['conid']==123
    assert result['rows'][0]['method']=='exact_wire_symbol_figi_contract'
    assert 'other_symbol_conid_versions_require_dated_link' in result['rows'][0]['remaining_issues']


def public_fixture():
    data,base,evidence=fixture();data['identifiers']=[]
    data['sec_tickers']=[dict(cik='1',ticker='ABC.A',source_file_id='sec-1')]
    base=r.reconcile(data,date(2026,1,1),date(2026,10,2));evidence['cases']=base['rows'];evidence['contracts']=[]
    evidence['symbols'][0]['country']='HK'
    evidence['security_isins']=[dict(security_id='s1',isin='CA80401L8033',source_hash='e'*64)]
    evidence['broker_public']=[dict(source_mapping_id='public',source_entity_key='conid:456',
        mapped_entity_id='i1',source_hash='f'*64,evidence_json=json.dumps(dict(
            sourceEvidence=json.dumps(dict(conid=456,symbol='ABC.A',product_type='STK',isin='CA80401L8033')),
            issuerEvidence=json.dumps(dict(countryCode='US')))))]
    return data,base,evidence


def test_us_listing_does_not_require_us_domicile_isin():
    data,base,evidence=public_fixture()
    result=run(data,base,evidence)
    assert result['rows'][0]['conid']==456
    assert 'canonical_exchange_country_requires_reconciliation' in result['rows'][0]['remaining_issues']


def test_unhashed_conflicting_cik_is_not_silently_ignored():
    data,base,evidence=public_fixture()
    evidence['issuer_ciks']=[dict(issuer_id='i1',cik='2',source_hash='')]
    assert run(data,base,evidence)['rows'][0]['decision']=='blocked'


def test_original_foreign_country_cannot_be_overridden_by_massive_locale():
    data,base,evidence=public_fixture()
    body=json.loads(evidence['broker_public'][0]['evidence_json'])
    body['issuerEvidence']=json.dumps(dict(countryCode='HK'))
    evidence['broker_public'][0]['evidence_json']=json.dumps(body)
    assert run(data,base,evidence)['rows'][0]['decision']=='blocked'


def test_sec_name_corroborates_provider_typo_without_fuzzy_name_relaxation():
    data,base,evidence=public_fixture()
    data['entities'][0]['name']='Unmatched abbreviated provider name'
    data['sec_tickers'][0]['company_name']='ABC'
    assert run(data,base,evidence)['rows'][0]['decision']=='identity_resolved'
    data['sec_tickers'][0]['cik']='2'
    assert run(data,base,evidence)['rows'][0]['decision']=='blocked'
