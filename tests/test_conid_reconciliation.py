from copy import deepcopy
from datetime import date

import pytest

from pipelines.certified_research import conid_reconciliation as r
from scripts.prepare_certified_research import parse_args

START = date(2026, 1, 1)
END = date(2026, 10, 2)


def sources():
    return dict(entities=[dict(entity_key='figi:F1', ticker='ABC.A', name='ABC', active=1,
        composite_figi='F1', share_class_figi='S1', cik='1', exchange='XNAS',
        source_hash='a'*64, type='CS')],
        identifiers=[dict(security_id='s1', kind='composite_figi', value='F1',
            valid_from_date=None, valid_to_date_exclusive=None, source_hash='b'*64)],
        listings=[dict(listing_id='l1', security_id='s1', conid='123', currency='USD',
            exchange='NASDAQ', product='STK', issuer_id='i1', listing_hash='c'*64, security_hash='d'*64)],
        coverage=[], issues=[], events=[dict(ticker='ABC.A', first_date=str(START), last_date=str(END))])


def run(data):
    return r.reconcile(data, START, END)


def test_unique_figi_resolves_without_historical_claim():
    result=run(sources())
    assert result['rows'][0]['conid']==123
    assert result['summary']['resolved']==1
    assert not result['summary']['broker_contract_validation']
    assert not result['summary']['historical_membership_certification']


@pytest.mark.parametrize('conid', ['', '0', '-1', '01', '1.1', str(2**64)])
def test_invalid_conid_fails_closed(conid):
    data=sources();data['listings'][0]['conid']=conid
    assert run(data)['rows'][0]['reason']=='missing_valid_conid'


def test_ticker_match_alone_never_resolves():
    data=sources();data['identifiers']=[]
    assert run(data)['rows'][0]['reason']=='missing_security_figi_binding'


def test_inactive_otc_identity_is_retained():
    data=sources();data['entities'][0].update(active=0,exchange='XOTC')
    data['listings'][0]['exchange']='OTC'
    assert run(data)['rows'][0]['status']=='resolved'


def test_conflicting_figis_block():
    data=sources();data['identifiers'].append(dict(data['identifiers'][0],value='S1',security_id='s2'))
    assert run(data)['rows'][0]['reason']=='conflicting_provider_figis'


def test_multiple_conids_block():
    data=sources();data['listings'].append(dict(data['listings'][0],listing_id='l2',conid='456'))
    assert run(data)['rows'][0]['reason']=='ambiguous_security_conid'


@pytest.mark.parametrize('key',['figi:F1','ABC.A','s1','l1','i1'])
def test_open_issue_blocks_every_identity_layer(key):
    data=sources();data['issues']=[dict(source_entity_key=key,issue_type='conflict',source_hash='e'*64)]
    assert run(data)['rows'][0]['reason']=='open_mapping_issue'


@pytest.mark.parametrize('mapping,security,listing,reason',[
    ('source_conflict','s1','l1','retained_source_mapping_conflict'),
    ('mapped','s2','l1','coverage_security_mismatch'),
    ('mapped','s1','l2','coverage_listing_mismatch'),
])
def test_coverage_disagreements_block(mapping,security,listing,reason):
    data=sources();data['coverage']=[dict(entity_key='figi:F1',mapping_status=mapping,
        security_id=security,listing_id=listing,source_status='completed',source_hash='e'*64)]
    assert run(data)['rows'][0]['reason']==reason


def test_competing_active_entities_block_even_with_same_spelling():
    data=sources();data['entities'].append(dict(data['entities'][0],entity_key='other'))
    assert all(row['reason']=='competing_active_provider_entities' for row in run(data)['rows'])


def test_expired_identifier_does_not_resolve_current_identity():
    data=sources();data['identifiers'][0]['valid_to_date_exclusive']='2025-01-01'
    assert run(data)['rows'][0]['status']=='blocked'


def test_input_order_does_not_change_snapshot():
    data=sources();data['listings'].append(dict(data['listings'][0],listing_id='l2'))
    reverse=deepcopy(data)
    for rows in reverse.values():rows.reverse()
    assert run(data)==run(reverse)


def test_duplicate_entity_rejected():
    data=sources();data['entities']*=2
    with pytest.raises(ValueError,match='Duplicate'):run(data)


def test_bounded_paginated_loader_and_range_validation():
    calls=[]
    def query(sql):
        calls.append(sql)
        return [{'n':0}] if sql.startswith('SELECT count()') else []
    assert all(not values for values in r.load(query,START,END).values())
    assert len(calls)==8
    with pytest.raises(ValueError,match='range'):r.load(query,END,START)
    with pytest.raises(ValueError,match='bound'):r.load(lambda sql:[{'n':200001}],START,END)


def test_launcher_exposes_opt_in_publication_and_acquisition_separately():
    args=parse_args(['reconcile-conids','--publish-conid-mappings'])
    assert args.publish_conid_mappings
    assert not args.acquire_missing
    assert 'live_market_ssd' in r.table().ddl('q_live')
    assert 'ZSTD(1)' in r.table().ddl('q_live')
    with pytest.raises(SystemExit):parse_args(['preflight','--publish-conid-mappings'])
    with pytest.raises(SystemExit):parse_args(['reconcile-conids','--acquire-missing'])


def publisher_fixture(monkeypatch):
    import json
    from pipelines.certified_research.publication import DictionaryClaim
    from pipelines.certified_research import sec_share_capture
    monkeypatch.setattr(sec_share_capture,'verify',lambda *args,**kwargs:None)
    claim=object.__new__(DictionaryClaim)
    claim.dictionary_database='q_live';claim.check=lambda:None
    stored={r.TABLE:[],r.receipt_table().name:[]}
    def query(sql,read=True):
        if 'system.disks' in sql:return [dict(free_space=str(1024**3),keep_free_space='0')]
        if sql.startswith('CREATE'):return []
        name=r.receipt_table().name if r.receipt_table().name in sql else r.TABLE
        if sql.startswith('INSERT'):
            stored[name].extend(json.loads(line) for line in sql.split('FORMAT JSONEachRow\n')[1].splitlines())
            return []
        if sql.startswith('SELECT count()'):return [{'n':len(stored[name])}]
        return deepcopy(stored[name])
    return claim,stored,query


def test_publication_exact_readback_and_repeat_without_inserts(monkeypatch):
    claim,stored,query=publisher_fixture(monkeypatch)
    result=run(sources())
    first=r.publish(query,'q_live',result,claim,lambda:None)
    second=r.publish(query,'q_live',result,claim,lambda:None)
    assert first==dict(inserted=1,reused=0,verified=1)
    assert second==dict(inserted=0,reused=1,verified=1)
    assert len(stored[r.receipt_table().name])==1


def test_interrupted_publication_resumes_without_duplicate_rows(monkeypatch):
    claim,stored,query=publisher_fixture(monkeypatch)
    result=run(sources())
    def interrupted(sql,read=True):
        output=query(sql,read)
        if sql.startswith('INSERT') and r.TABLE in sql:raise RuntimeError('lost response')
        return output
    with pytest.raises(RuntimeError,match='lost response'):
        r.publish(interrupted,'q_live',result,claim,lambda:None)
    assert not stored[r.receipt_table().name]
    assert r.publish(query,'q_live',result,claim,lambda:None)['inserted']==0


def test_conflicting_persisted_mapping_is_never_overwritten(monkeypatch):
    claim,stored,query=publisher_fixture(monkeypatch)
    result=run(sources())
    stored[r.TABLE]=[dict(result['rows'][0],conid=999)]
    with pytest.raises(ValueError,match='Conflicting'):
        r.publish(query,'q_live',result,claim,lambda:None)
    assert stored[r.TABLE][0]['conid']==999


def test_source_change_prevents_receipt_after_partial_rows(monkeypatch):
    claim,stored,query=publisher_fixture(monkeypatch)
    calls=[]
    def guard():
        calls.append(1)
        if len(calls)>1:raise ValueError('source changed')
    with pytest.raises(ValueError,match='source changed'):
        r.publish(query,'q_live',run(sources()),claim,guard)
    assert len(stored[r.TABLE])==1
    assert not stored[r.receipt_table().name]


def test_shared_conid_across_securities_blocks_both():
    data=sources()
    data['entities'].append(dict(data['entities'][0],entity_key='figi:F2',ticker='XYZ',composite_figi='F2',share_class_figi=''))
    data['identifiers'].append(dict(data['identifiers'][0],value='F2',security_id='s2'))
    data['listings'].append(dict(data['listings'][0],security_id='s2',listing_id='l2'))
    assert all(row['reason']=='conid_owned_by_multiple_securities' for row in run(data)['rows'])


def sec_sources():
    data=sources();data['identifiers']=[]
    data['sec_tickers']=[dict(cik='0000000001',ticker='ABC.A',exchange='Nasdaq',
        mapping_source='company_tickers_exchange',source_file_id='sec-file-1')]
    data['sec_bridge']=[dict(cik='0000000001',ticker='ABC A',security_id='s1',listing_id='l1',
        issuer_id='i1',mapping_method='issuer_cik_to_active_market_listing',mapping_status='active',
        ambiguity_status='unique',valid_from_date=None,valid_to_date_exclusive=None,source_hash='f'*64)]
    return data


def test_sec_cik_and_class_symbol_bridge_resolves_missing_figi():
    result=run(sec_sources())
    assert result['rows'][0]['conid']==123
    assert result['summary']['sec_bridge_resolved_without_figi']==1


@pytest.mark.parametrize('change', ['no_sec','no_bridge','wrong_class','wrong_cik','no_receipt',
    'ambiguous_cik','ambiguous_security','listed_parent','inactive','expired_bridge'])
def test_sec_fallback_requires_independent_class_specific_evidence(change):
    data=sec_sources()
    if change=='no_sec':data['sec_tickers']=[]
    elif change=='no_bridge':data['sec_bridge']=[]
    elif change=='wrong_class':data['sec_bridge'][0]['ticker']='ABC.B'
    elif change=='wrong_cik':data['sec_tickers'][0]['cik']='2'
    elif change=='no_receipt':data['sec_tickers'][0]['source_file_id']=''
    elif change=='ambiguous_cik':data['sec_tickers'].append(dict(data['sec_tickers'][0],cik='2'))
    elif change=='ambiguous_security':data['sec_bridge'].append(dict(data['sec_bridge'][0],security_id='s2'))
    elif change=='listed_parent':data['sec_bridge'][0]['mapping_method']='filing_issuer_to_listed_parent'
    elif change=='inactive':data['entities'][0]['active']=0
    elif change=='expired_bridge':data['sec_bridge'][0]['valid_to_date_exclusive']='2025-01-01'
    assert run(data)['rows'][0]['status']=='blocked'


def test_sec_and_existing_figi_disagreement_blocks():
    data=sec_sources();data['identifiers']=sources()['identifiers']
    data['sec_bridge'][0]['security_id']='s2'
    assert run(data)['rows'][0]['reason']=='sec_figi_security_mismatch'


def test_expired_figi_cannot_be_reassigned_by_sec_fallback():
    data=sec_sources();data['identifiers']=[dict(sources()['identifiers'][0],
        security_id='s2',valid_to_date_exclusive='2025-01-01')]
    assert run(data)['rows'][0]['reason']=='sec_figi_security_mismatch'
