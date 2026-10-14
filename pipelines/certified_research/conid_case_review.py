"""One explicit evidence decision per unresolved event-backed stock identity.

Contract identity, issuer identity and historical timeline defects are separate.
No canonical issue is closed and no timeline is repaired by a conid decision.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
import json
import re

from .contracts import digest, literal
from .schema import Table, c
from services.reference_gateway.ibkr_contract_identity import (
    normalize_equity_symbol, resolve_massive_ibkr_contract, company_names_compatible,
)

VERSION='retained-conid-case-review-v4'


class ReviewTable(Table):
    """Identical retries compact; distinct competing payloads fail readback."""
    def ddl(self,database):
        return super().ddl(database).replace('ENGINE=MergeTree ','ENGINE=ReplacingMergeTree ',1)


def valid_us_isin(value):
    return value.startswith('US') and valid_isin(value)


def valid_isin(value):
    if not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]',value):return False
    digits=''.join(str(ord(ch)-55) if ch.isalpha() else ch for ch in value)
    total=0
    for index,ch in enumerate(reversed(digits)):
        number=int(ch)*(2 if index%2 else 1)
        total+=number//10+number%10
    return total%10==0


def table():
    return ReviewTable('research_conid_case_review_v1', (
        c('review_hash','FixedString(64)'), c('entity_key','String'),
        c('ticker','LowCardinality(String)'), c('provider_active','UInt8'),
        c('original_reason','LowCardinality(String)'), c('decision','LowCardinality(String)'),
        c('method','LowCardinality(String)'), c('reason','LowCardinality(String)'),
        c('security_id','String'), c('conid','UInt64'), c('listing_ids','Array(String)'),
        c('candidate_conids','Array(UInt64)'),c('historical_aliases','Array(String)'),
        c('remaining_issues','Array(String)'), c('evidence_hash','FixedString(64)'),c('record_hash','FixedString(64)'),
    ), ('review_hash','entity_key','record_hash'))


def receipt_table():
    return ReviewTable('research_conid_case_review_receipt_v1', (
        c('review_hash','FixedString(64)'),c('version','LowCardinality(String)'),
        c('baseline_hash','FixedString(64)'),c('source_evidence_hash','FixedString(64)'),
        c('range_start','Date'),c('range_end','Date'),
        c('reviewed','UInt16'),c('identity_resolved','UInt16'),c('blocked','UInt16'),
        c('rows_hash','FixedString(64)'),c('record_hash','FixedString(64)'),
    ), ('review_hash','record_hash'))


def evidence_table():
    return ReviewTable('research_conid_case_evidence_v1', (
        c('evidence_hash','FixedString(64)'),c('payload','String'),
    ), ('evidence_hash',))


def load(query, baseline, sources, *, database='q_live'):
    from .contracts import identifier
    identifier(database)
    events={r['ticker'] for r in sources['events']}
    cases=[r for r in baseline['rows'] if r['status']=='blocked'
           and r['provider_type'] in ('CS','ADRC') and r['ticker'] in events]
    if len(cases)>2000:raise ValueError('Stock identity review exceeds 2000-case bound')
    tickers={r['ticker'] for r in cases}
    # Filter source mappings before transporting evidence. All country/product
    # constraints remain independent of OTC admission.
    result={'cases':cases}
    result['aliases']=[]
    keys=sorted({r['entity_key'] for r in cases})
    for offset in range(0,len(keys),128):
        selected=','.join(literal(k) for k in keys[offset:offset+128])
        rows=query(f"SELECT provider_entity_key AS entity_key,event_date,ifNull(ticker,'') AS ticker,"
            f"source_content_sha256 AS source_hash,source_response_sha256 AS response_hash FROM {database}.market_ticker_event_v1 FINAL "
            f"WHERE is_deleted=0 AND event_type='ticker_change' AND provider_entity_key IN ({selected}) LIMIT 10001")
        if len(rows)>10000:raise ValueError('Historical alias evidence exceeds packet bound')
        result['aliases'].extend(rows)
    tickers.update(r['ticker'] for r in result['aliases'] if r['ticker'])
    if len(tickers)>6000:raise ValueError('Historical symbol review exceeds 6000-symbol bound')
    queries={
        'symbols': f"SELECT sym.symbol_id AS symbol_id,sym.ticker AS ticker,sym.listing_id AS listing_id,"
            "l.security_id AS security_id,ifNull(l.ibkr_conid,'') AS conid,l.currency_code AS currency,"
            "s.product_type AS product,s.issuer_id AS issuer_id,s.security_name AS name,"
            "sym.source_content_sha256 AS symbol_hash,l.source_content_sha256 AS listing_hash,"
            "s.source_content_sha256 AS security_hash,e.iso_country_code AS country "
            f"FROM {database}.id_symbol_v1 sym FINAL INNER JOIN {database}.id_listing_v1 l FINAL ON sym.listing_id=l.listing_id "
            f"INNER JOIN {database}.id_security_v1 s FINAL ON l.security_id=s.security_id "
            f"LEFT JOIN {database}.ref_exchange_v1 e FINAL ON l.exchange_code=e.exchange_code "
            "WHERE sym.ticker IN ({tickers})",
        'contracts': f"SELECT source_mapping_id,source_system,source_entity_key,mapped_entity_id,mapping_status,"
            f"evidence_json,source_content_sha256 AS source_hash FROM {database}.id_source_mapping_v1 FINAL "
            "WHERE source_system IN ('massive','ibkr') AND mapping_status='active' "
            "AND JSONExtractString(evidence_json,'ticker') IN ({tickers})",
        'issues': f"SELECT mapping_issue_id,source_entity_key,issue_type,issue_status,evidence_json,"
            f"source_content_sha256 AS source_hash FROM {database}.id_mapping_issue_v1 FINAL "
            "WHERE lower(issue_status) NOT IN ('resolved','closed','ignored') AND source_entity_key IN ({tickers})",
    }
    for name,sql in queries.items():
        rows=[]
        for offset in range(0,len(tickers),128):
            selected=','.join(literal(t) for t in sorted(tickers)[offset:offset+128])
            packet=query(sql.format(tickers=selected)+' LIMIT 10001')
            if len(packet)>10000:raise ValueError('Identity evidence packet exceeds bound: '+name)
            rows.extend(packet)
        result[name]=rows
    result['issuer_ciks']=[]
    issuers=sorted({r['issuer_id'] for r in result['symbols']})
    for offset in range(0,len(issuers),512):
        selected=','.join(literal(i) for i in issuers[offset:offset+512])
        result['issuer_ciks'].extend(query(f"SELECT issuer_id,identifier_value_normalized AS cik,source_content_sha256 AS source_hash "
            f"FROM {database}.id_issuer_identifier_v1 FINAL WHERE lower(identifier_kind)='cik' "
            f"AND valid_to_date_exclusive IS NULL AND issuer_id IN ({selected}) LIMIT 10001"))
    if len(result['issuer_ciks'])>10000:raise ValueError('Issuer identity review exceeds bound')
    result['security_isins']=[]
    securities=sorted({r['security_id'] for r in result['symbols']})
    for offset in range(0,len(securities),512):
        selected=','.join(literal(i) for i in securities[offset:offset+512])
        result['security_isins'].extend(query(f"SELECT security_id,identifier_value_normalized AS isin,source_content_sha256 AS source_hash "
            f"FROM {database}.id_security_identifier_v1 FINAL WHERE lower(identifier_kind)='isin' "
            f"AND valid_to_date_exclusive IS NULL AND security_id IN ({selected}) LIMIT 10001"))
    if len(result['security_isins'])>10000:raise ValueError('Security identity review exceeds bound')
    result['broker_public']=[]
    conids=sorted({r['conid'] for r in result['symbols'] if re.fullmatch('[1-9][0-9]*',r['conid'])})
    for offset in range(0,len(conids),256):
        selected=','.join(literal('conid:'+i) for i in conids[offset:offset+256])
        packet=query(f"SELECT source_mapping_id,source_entity_key,mapped_entity_id,evidence_json,source_content_sha256 AS source_hash "
            f"FROM {database}.id_source_mapping_v1 FINAL WHERE source_system='ibkr_public' AND source_entity_kind='issuer' "
            f"AND mapping_status IN ('confirmed','active') AND source_entity_key IN ({selected}) LIMIT 257")
        if len(packet)>256:raise ValueError('Competing broker-public issuer receipts exceed packet bound')
        result['broker_public'].extend(packet)
    return result


def review(baseline, sources, evidence):
    """Exhaustively decide every frozen entity; name alone never resolves."""
    by_entity={r['entity_key']:r for r in sources['entities']}
    from .conid_reconciliation import reconcile
    from datetime import date
    current=reconcile(sources,date.fromisoformat(baseline['range_start']),date.fromisoformat(baseline['range_end']))
    current_rows={r['entity_key']:r for r in current['rows']}
    def cik_key(value):
        text=str(value or '')
        return str(int(text)) if re.fullmatch('0*[1-9][0-9]{0,9}',text) else ''
    issuer_ciks=defaultdict(set);qualified_ciks=defaultdict(set)
    for row in evidence.get('issuer_ciks',[]):
        if cik_key(row['cik']):issuer_ciks[row['issuer_id']].add(cik_key(row['cik']))
        if row['source_hash'] and cik_key(row['cik']):qualified_ciks[row['issuer_id']].add(cik_key(row['cik']))
    sec_pairs={(cik_key(r['cik']),normalize_equity_symbol(r['ticker'])) for r in sources.get('sec_tickers',[]) if r['source_file_id']}
    sec_names=defaultdict(set)
    for row in sources.get('sec_tickers',[]):
        if row['source_file_id'] and row.get('company_name'):
            sec_names[(cik_key(row['cik']),normalize_equity_symbol(row['ticker']))].add(row['company_name'])
    def name_agrees(entity,owner):
        return (company_names_compatible(entity['name'],owner['name']) or any(
            company_names_compatible(name,owner['name']) for name in sec_names[(cik_key(entity['cik']),normalize_equity_symbol(entity['ticker']))]))
    us_isins=defaultdict(set);all_isins=defaultdict(set)
    for row in evidence.get('security_isins',[]):
        if row['source_hash'] and valid_isin(row['isin']):all_isins[row['security_id']].add(row['isin'])
        if row['source_hash'] and valid_us_isin(row['isin']):
            us_isins[row['security_id']].add(row['isin'])
    public_receipts=defaultdict(list)
    for row in evidence.get('broker_public',[]):
        body=json.loads(row['evidence_json'])
        def nested(name):
            value=body.get(name,{})
            return json.loads(value) if isinstance(value,str) else value
        source=nested('sourceEvidence');issuer=nested('issuerEvidence')
        if row['source_hash'] and isinstance(source,dict) and isinstance(issuer,dict):
            public_receipts[str(source.get('conid',''))].append((row,source,issuer))
    by_symbol=defaultdict(list)
    for row in evidence['symbols']:
        by_symbol[normalize_equity_symbol(row['ticker'])].append(row)
    contracts=defaultdict(list)
    for row in evidence['contracts']:
        payload=json.loads(row['evidence_json'])
        if not isinstance(payload,dict):raise ValueError('Malformed retained broker identity evidence')
        contracts[normalize_equity_symbol(payload.get('ticker'))].append((row,payload))
    issues=defaultdict(list)
    for row in evidence['issues']:issues[row['source_entity_key']].append(row)
    coverage=defaultdict(list)
    for row in sources['coverage']:coverage[row['entity_key']].append(row)
    aliases=defaultdict(list)
    for row in evidence.get('aliases',[]):aliases[row['entity_key']].append(row)
    decisions=[];proofs={}
    for case in sorted(evidence['cases'],key=lambda r:r['entity_key']):
        entity=by_entity[case['entity_key']];symbol=normalize_equity_symbol(entity['ticker'])
        peers=by_symbol[symbol]
        historical_peers=[p for a in aliases[case['entity_key']] if a['ticker']
                          for p in by_symbol[normalize_equity_symbol(a['ticker'])]]
        candidates=[];diagnostics=[]
        for receipt,payload in sorted(contracts[symbol],key=lambda pair:digest(pair[0])):
            overview=payload.get('overview') or {}
            # The receipt binds the broker definition to the exact provider
            # security, not just an issuer or a same-spelled ticker overseas.
            composite=payload.get('composite_figi') or overview.get('composite_figi')
            share=payload.get('share_class_figi') or overview.get('share_class_figi')
            if not entity['composite_figi'] or composite!=entity['composite_figi']:
                diagnostics.append('receipt_provider_figi_mismatch');continue
            if entity['share_class_figi'] and share and share!=entity['share_class_figi']:
                diagnostics.append('receipt_share_class_mismatch');continue
            if entity['cik'] and (payload.get('cik') or overview.get('cik')) and int(entity['cik'])!=int(payload.get('cik') or overview['cik']):
                diagnostics.append('receipt_cik_mismatch');continue
            if not receipt['source_hash']:
                diagnostics.append('receipt_provenance_missing');continue
            definitions=payload.get('ibkr_candidates',[])
            resolution=resolve_massive_ibkr_contract(massive_ticker=entity['ticker'],
                massive_name=entity['name'],massive_exchange=entity['exchange'],definitions=definitions)
            diagnostics.append(resolution.reason)
            name_matches=resolution.company_name_match
            if resolution.accepted and not name_matches:
                selected_definition=next((d for d in definitions if str(d.get('conid'))==str(resolution.conid)),{})
                if (str(selected_definition.get('type','')).upper()=='ADR'
                        and re.search(r'\bDepositary\b',entity['name'],re.I)):
                    provider_issuer=re.split(r'\s+American\s+Depositary\b',entity['name'],flags=re.I)[0]
                    broker_issuer=re.sub(r'(?:-S)?\s+ADR$','',resolution.broker_company_name,flags=re.I)
                    name_matches=company_names_compatible(provider_issuer,broker_issuer)
            if not resolution.accepted or not name_matches:continue
            owners=[r for r in peers if r['conid']==str(resolution.conid) and r['currency']=='USD'
                    and r['product'].upper() in ('STK','STOCK','STOCKS') and r['country']=='US']
            if case['security_id']:owners=[r for r in owners if r['security_id']==case['security_id']]
            if not owners or len({r['security_id'] for r in owners})!=1:
                diagnostics.append('contract_has_no_unique_canonical_security');continue
            if any(not r['symbol_hash'] or not r['listing_hash'] or not r['security_hash'] for r in owners):
                diagnostics.append('canonical_owner_provenance_missing');continue
            candidates.append(dict(conid=resolution.conid,security_id=owners[0]['security_id'],method='retained_verified_primary_contract',
                listing_ids=sorted({r['listing_id'] for r in owners}),receipt=receipt,resolution=asdict(resolution)))
        # A quarantined ticker-change timeline does not invalidate an already
        # unique FIGI/security/conid identity. It remains a historical blocker.
        if not candidates and case['reason'] in ('retained_source_mapping_conflict','open_mapping_issue') and len(case['candidate_conids'])==1:
            bound=coverage[case['entity_key']]
            timeline_only=bound and all(r['mapping_status']=='source_conflict' and
                str(r.get('mapping_reason','')).startswith(('latest_provider_ticker=','multiple_provider_tickers_on_date='))
                and r['security_id']==case['security_id'] and r['listing_id'] in case['listing_ids']
                and r['source_hash'] for r in bound)
            owners=[r for r in sources['listings'] if r['security_id']==case['security_id']
                and r['listing_id'] in case['listing_ids'] and r['conid']==str(case['candidate_conids'][0])]
            graph_identity=(case['reason']=='open_mapping_issue' and case['security_id'] and entity['composite_figi'])
            if (timeline_only or graph_identity) and owners and all(r['listing_hash'] and r['security_hash'] for r in owners):
                candidates.append(dict(conid=case['candidate_conids'][0],security_id=case['security_id'],
                    listing_ids=case['listing_ids'],method='canonical_figi_identity_history_quarantined' if timeline_only
                    else 'canonical_figi_conid_issue_closure_pending',
                    receipt=bound,resolution={'identity_only':True}))
        if not candidates and case['reason']=='ambiguous_security_conid':
            # A security can have old/new symbol-specific contracts. Do not
            # substitute a differently named contract for this exact wire.
            owners=[r for r in peers if r['security_id']==case['security_id'] and r['country']=='US'
                    and r['currency']=='USD' and r['product'].upper() in ('STK','STOCK','STOCKS')
                    and re.fullmatch('[1-9][0-9]*',r['conid']) and int(r['conid'])<2**64]
            bound=coverage[case['entity_key']]
            if (len({r['conid'] for r in owners})==1 and owners and
                    any(r['mapping_status']=='mapped' and r['security_id']==case['security_id'] and r['source_hash']
                        and r['listing_id'] in {p['listing_id'] for p in owners} for r in bound)
                    and all(r['symbol_hash'] and r['listing_hash'] and r['security_hash'] for r in owners)):
                candidates.append(dict(conid=int(owners[0]['conid']),security_id=case['security_id'],
                    listing_ids=sorted({r['listing_id'] for r in owners}),method='exact_wire_symbol_figi_contract',
                    receipt=bound,resolution={'identity_only':True,'other_symbol_conid_versions_retained':True}))
        if not candidates and case['reason']=='missing_security_figi_binding' and entity['active']:
            cik=cik_key(entity['cik'])
            supported=[r for r in peers if cik and (cik,symbol) in sec_pairs
                and qualified_ciks[r['issuer_id']]=={cik} and issuer_ciks[r['issuer_id']]=={cik} and r['country']=='US'
                and r['currency']=='USD' and r['product'].upper() in ('STK','STOCK','STOCKS')
                and re.fullmatch('[1-9][0-9]*',r['conid']) and int(r['conid'])<2**64
                and name_agrees(entity,r)
                and r['symbol_hash'] and r['listing_hash'] and r['security_hash']]
            if len({(r['security_id'],r['conid']) for r in supported})==1:
                owner=supported[0]
                figis={entity['composite_figi'],entity['share_class_figi']}-{''}
                conflicting=any(r['value'] in figis and r['security_id']!=owner['security_id'] for r in sources['identifiers'])
                if not conflicting:
                    candidates.append(dict(conid=int(owner['conid']),security_id=owner['security_id'],
                        listing_ids=sorted({r['listing_id'] for r in supported}),
                        method='sec_cik_class_symbol_canonical_issuer',receipt=supported,
                        resolution={'issuer_cik':cik,'sec_symbol_pair':True,'identity_only':True}))
            # A weak broker-public issuer can still have a durable US ISIN.
            # Retain the missing CIK binding as a separate repair dependency.
            if not candidates:
                supported=[r for r in peers if cik and (cik,symbol) in sec_pairs
                    and len(us_isins[r['security_id']])==1 and r['currency']=='USD'
                    and r['product'].upper() in ('STK','STOCK','STOCKS')
                    and re.fullmatch('[1-9][0-9]*',r['conid']) and int(r['conid'])<2**64
                    and name_agrees(entity,r)
                    and (not issuer_ciks[r['issuer_id']] or issuer_ciks[r['issuer_id']]=={cik})
                    and r['symbol_hash'] and r['listing_hash'] and r['security_hash']]
                if len({(r['security_id'],r['conid']) for r in supported})==1:
                    owner=supported[0];figis={entity['composite_figi'],entity['share_class_figi']}-{''}
                    if not any(r['value'] in figis and r['security_id']!=owner['security_id'] for r in sources['identifiers']):
                        candidates.append(dict(conid=int(owner['conid']),security_id=owner['security_id'],
                            listing_ids=sorted({r['listing_id'] for r in supported}),
                            method='sec_symbol_us_isin_canonical_contract',receipt=supported,
                            resolution={'issuer_cik':cik,'us_isin':sorted(us_isins[owner['security_id']]),'identity_only':True}))
            if not candidates and cik and (cik,symbol) in sec_pairs:
                supported=[]
                for owner in peers:
                    if (owner['currency']!='USD' or owner['product'].upper() not in ('STK','STOCK','STOCKS')
                            or not re.fullmatch('[1-9][0-9]*',owner['conid']) or int(owner['conid'])>=2**64
                            or len(all_isins[owner['security_id']])!=1
                            or (issuer_ciks[owner['issuer_id']] and issuer_ciks[owner['issuer_id']]!={cik})
                            or not all(owner[k] for k in ('symbol_hash','listing_hash','security_hash'))):continue
                    figis={entity['composite_figi'],entity['share_class_figi']}-{''}
                    if any(r['value'] in figis and r['security_id']!=owner['security_id'] for r in sources['identifiers']):continue
                    for receipt,source,issuer in public_receipts[owner['conid']]:
                        raw_symbol=normalize_equity_symbol(source.get('symbol'))
                        alias_symbols={normalize_equity_symbol(a['ticker']) for a in aliases[case['entity_key']]
                                       if a['source_hash'] and a['response_hash']}
                        if (receipt['mapped_entity_id']!=owner['issuer_id'] or issuer.get('countryCode')!='US'
                                or source.get('product_type')!='STK' or raw_symbol not in {symbol,*alias_symbols}
                                or source.get('isin') not in all_isins[owner['security_id']]):continue
                        # Strong issuer CIK + exact class symbol supersedes a
                        # stale issuer name; weak issuers still need name agreement.
                        if qualified_ciks[owner['issuer_id']]!={cik} and not name_agrees(entity,owner):continue
                        supported.append((owner,receipt,source,issuer))
                if len({(p['security_id'],p['conid']) for p,_,_,_ in supported})==1:
                    owner=supported[0][0]
                    candidates.append(dict(conid=int(owner['conid']),security_id=owner['security_id'],
                        listing_ids=sorted({p['listing_id'] for p,_,_,_ in supported}),
                        method='retained_broker_public_country_isin_sec_identity',receipt=supported,
                        resolution={'issuer_cik':cik,'identity_only':True,'country_assertion':'US'}))
        candidates.sort(key=digest)
        identities={(r['security_id'],r['conid']) for r in candidates}
        remaining=[]
        if any(r['mapping_status']=='source_conflict' for r in coverage[case['entity_key']]):
            remaining.append('historical_ticker_timeline_conflict')
        if issues[entity['ticker']] or case['reason']=='open_mapping_issue':remaining.append('canonical_mapping_issues_require_separate_closure')
        if not entity['active']:remaining.append('inactive_listing_lifecycle_requires_dated_evidence')
        selected=candidates[0] if len(identities)==1 else None
        reason='' if selected else 'multiple_verified_contracts' if len(identities)>1 else 'no_verified_retained_contract'
        if not selected and not candidates:
            stock_peers=[p for p in peers if p['currency']=='USD' and p['product'].upper() in ('STK','STOCK','STOCKS')]
            if not entity['active']:reason='inactive_identity_requires_dated_contract_or_alias_proof'
            elif not peers:reason='historical_alias_requires_dated_contract_link' if historical_peers else 'no_retained_canonical_symbol'
            elif not stock_peers:reason='canonical_symbol_outside_usd_stock_shape'
            elif not cik_key(entity['cik']):reason='provider_cik_missing_and_no_verified_contract'
            elif (cik_key(entity['cik']),symbol) not in sec_pairs:reason='independent_sec_class_symbol_evidence_missing'
            elif len({p['conid'] for p in stock_peers})>1:reason='multiple_retained_symbol_conids_require_vintage_evidence'
            elif not any(us_isins[p['security_id']] for p in stock_peers):reason='durable_us_security_identifier_missing'
            else:reason='issuer_or_class_identity_evidence_disagrees'
        if case['reason']=='competing_active_provider_entities':
            raw=current_rows[case['entity_key']]
            if raw['status']=='resolved':
                selected=dict(conid=raw['conid'],security_id=raw['security_id'],listing_ids=raw['listing_ids'],
                    method='case_sensitive_massive_wire_symbol',receipt=raw,resolution={'identity_only':True})
                candidates.append(selected);reason=''
            else:selected=None;reason='competing_provider_identity_requires_reconciliation'
        # Issuer/mapping issues are retained rather than automatically closed.
        if selected and issues[entity['ticker']]:
            remaining.append('verified_conid_proposal_pending_issue_closure')
        if selected and selected['method']=='sec_symbol_us_isin_canonical_contract':
            remaining.append('canonical_issuer_requires_sec_cik_binding')
        if selected and selected['method']=='exact_wire_symbol_figi_contract':
            remaining.append('other_symbol_conid_versions_require_dated_link')
        if selected and selected['method']=='retained_broker_public_country_isin_sec_identity':
            selected_owners=[p for p in peers if p['listing_id'] in selected['listing_ids']]
            if any(p['country']!='US' for p in selected_owners):remaining.append('canonical_exchange_country_requires_reconciliation')
            if any(qualified_ciks[p['issuer_id']]!={cik_key(entity['cik'])} for p in selected_owners):
                remaining.append('canonical_issuer_requires_sec_cik_binding')
        case_proof=dict(entity=entity,original=case,contracts=candidates,diagnostics=sorted(set(diagnostics)),
            symbols=sorted(peers,key=digest),issues=sorted(issues[entity['ticker']],key=digest),remaining=remaining,
            issuer_ciks=sorted([r for r in evidence.get('issuer_ciks',[]) if r['issuer_id'] in {p['issuer_id'] for p in peers}],key=digest),
            security_isins=sorted([r for r in evidence.get('security_isins',[]) if r['security_id'] in {p['security_id'] for p in peers}],key=digest))
        case_proof['historical_aliases']=sorted(aliases[case['entity_key']],key=digest)
        case_proof['historical_canonical_symbols']=sorted(historical_peers,key=digest)
        case_proof['sec_symbol_evidence']=sorted([r for r in sources.get('sec_tickers',[])
            if normalize_equity_symbol(r['ticker'])==symbol and cik_key(r['cik'])==cik_key(entity['cik'])],key=digest)
        proof_hash=digest(case_proof)
        proofs[proof_hash]=dict(evidence_hash=proof_hash,payload=json.dumps(case_proof,sort_keys=True,
            separators=(',',':'),allow_nan=False,default=str))
        decisions.append(dict(entity_key=case['entity_key'],ticker=case['ticker'],provider_active=case['provider_active'],
            original_reason=case['reason'],decision='identity_resolved' if selected else 'blocked',
            method=selected['method'] if selected else 'retained_evidence_review',reason=reason,
            security_id=selected['security_id'] if selected else case['security_id'],
            conid=selected['conid'] if selected else 0,listing_ids=selected['listing_ids'] if selected else case['listing_ids'],
            candidate_conids=sorted(set(case['candidate_conids']) | {int(p['conid']) for p in peers+historical_peers
                if re.fullmatch('[1-9][0-9]*',p['conid']) and int(p['conid'])<2**64}),
            historical_aliases=sorted({r['ticker'] for r in aliases[case['entity_key']] if r['ticker']}),
            remaining_issues=sorted(set(remaining)),evidence_hash=proof_hash))
    if len(decisions)!=len(evidence['cases']) or len({r['entity_key'] for r in decisions})!=len(decisions):
        raise ValueError('Per-entity review census differs')
    owners=defaultdict(set)
    for row in current['rows']:
        if row['status']=='resolved':owners[row['conid']].add(row['security_id'])
    for row in decisions:
        if row['decision']=='identity_resolved':owners[row['conid']].add(row['security_id'])
    for row in decisions:
        if row['decision']=='identity_resolved' and len(owners[row['conid']])>1:
            row.update(decision='blocked',reason='conid_shared_by_competing_canonical_securities',conid=0)
    for row in decisions:row['record_hash']=digest(row)
    evidence_hash=digest({k:sorted(value,key=digest) for k,value in evidence.items()})
    fingerprint=digest(dict(version=VERSION,baseline=baseline['snapshot_hash'],evidence_hash=evidence_hash,decisions=decisions))
    return dict(version=VERSION,review_hash=fingerprint,baseline_hash=baseline['snapshot_hash'],
        source_evidence_hash=evidence_hash,range_start=baseline['range_start'],range_end=baseline['range_end'],
        proofs=sorted(proofs.values(),key=lambda r:r['evidence_hash']),
        rows=[dict(review_hash=fingerprint,**r) for r in decisions],
        summary=dict(reviewed=len(decisions),distinct_tickers=len({r['ticker'] for r in decisions}),
            identity_resolved=sum(r['decision']=='identity_resolved' for r in decisions),
            blocked=sum(r['decision']=='blocked' for r in decisions),
            original_reasons=dict(Counter(r['original_reason'] for r in decisions)),
            blocked_reasons=dict(Counter(r['reason'] for r in decisions if r['decision']=='blocked')),
            remaining_issues=dict(Counter(issue for r in decisions for issue in r['remaining_issues'])),
            canonical_issue_closure=False,historical_timeline_certification=False))


def verify(query,database,*,required=False):
    from .contracts import identifier
    identifier(database)
    if database!='q_live' and not database.startswith('certified_research_test_'):
        raise ValueError('Case review requires q_live or an isolated test database')
    catalog={t.name:t for t in (table(),receipt_table(),evidence_table())};names=','.join(map(literal,catalog))
    policy=query("SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name WHERE policy_name='live_market_ssd'")
    if not policy or any(r['disk_name']!='live_market_ssd' for r in policy):raise ValueError('Review SSD policy is unavailable')
    tables=query(f'SELECT name,engine,sorting_key,partition_key,storage_policy FROM system.tables WHERE database={literal(database)} AND name IN ({names})')
    if required and {r['name'] for r in tables}!=set(catalog):raise ValueError('Review tables are missing')
    compact=lambda value:value.replace(' ','').replace('`','')
    for row in tables:
        t=catalog[row['name']]
        if (row['engine']!='ReplacingMergeTree' or row['storage_policy']!='live_market_ssd'
                or compact(row['sorting_key'])!=','.join(t.order) or compact(row['partition_key'])!=t.partition):
            raise ValueError('Review schema requires explicit migration')
    columns=query(f'SELECT table,name,type,compression_codec FROM system.columns WHERE database={literal(database)} AND table IN ({names})')
    actual={(r['table'],r['name']):(compact(r['type']),compact(r['compression_codec'])) for r in columns}
    present={r['name'] for r in tables}
    expected={(t.name,c.name):(compact(c.type),'CODEC('+compact(c.codec)+')') for t in catalog.values() if t.name in present for c in t.columns}
    if actual!=expected:raise ValueError('Review columns/codecs require explicit migration')
    parts=query(f'SELECT table,disk_name FROM system.parts WHERE database={literal(database)} AND table IN ({names}) GROUP BY table,disk_name')
    if any(r['disk_name']!='live_market_ssd' for r in parts):raise ValueError('Review parts are outside required SSD')


def publish(query,result,source_guard,*,database='q_live'):
    """Persist exact decisions plus a completion receipt; never updates graph."""
    from .publication import insert_rows
    from .contracts import identifier
    identifier(database)
    rows=result['rows'];decisions=[{k:v for k,v in row.items() if k!='review_hash'} for row in rows]
    expected_hash=digest(dict(version=VERSION,baseline=result['baseline_hash'],evidence_hash=result['source_evidence_hash'],decisions=decisions))
    if (result['version']!=VERSION or expected_hash!=result['review_hash'] or len(rows)>2000
            or len({r['entity_key'] for r in rows})!=len(rows) or any(r['review_hash']!=expected_hash for r in rows)
            or any(r['record_hash']!=digest({k:v for k,v in r.items() if k not in ('review_hash','record_hash')}) for r in rows)):
        raise ValueError('Per-case review fingerprint/key differs')
    summary=result['summary']
    if (summary['reviewed']!=len(rows)
            or summary['identity_resolved']!=sum(r['decision']=='identity_resolved' for r in rows)
            or summary['blocked']!=sum(r['decision']=='blocked' for r in rows)
            or any(r['decision'] not in ('identity_resolved','blocked') or
                (r['decision']=='identity_resolved' and not 0<r['conid']<2**64) or
                (r['decision']=='blocked' and r['conid']!=0) for r in rows)):
        raise ValueError('Per-case review counts/status differ from records')
    source_guard()
    disks=query("SELECT free_space,keep_free_space FROM system.disks WHERE name='live_market_ssd'")
    if len(disks)!=1 or int(disks[0]['free_space'])-int(disks[0]['keep_free_space'])<128*1024**2:
        raise ValueError('Per-case publication needs 128 MiB unreserved SSD headroom')
    catalog=(table(),receipt_table(),evidence_table());verify(query,database)
    for t in catalog:query(t.ddl(database),read=False)
    verify(query,database,required=True)
    where='review_hash='+literal(result['review_hash'])
    def readback():
        actual=query(f'SELECT DISTINCT * FROM {database}.{table().name} WHERE {where} ORDER BY entity_key LIMIT 2001')
        for row in actual:
            row['conid']=int(row['conid']);row['candidate_conids']=list(map(int,row['candidate_conids']))
        return actual
    expected={r['entity_key']:r for r in rows};old=readback();keys=set()
    for row in old:
        if row['entity_key'] in keys or expected.get(row['entity_key'])!=row:
            raise ValueError('Conflicting persisted case decision; no overwrite permitted')
        keys.add(row['entity_key'])
    pending=[r for r in rows if r['entity_key'] not in keys]
    for offset in range(0,len(pending),256):
        insert_rows(query,database,table().name,pending[offset:offset+256])
    proofs=result['proofs'];expected_proofs={p['evidence_hash']:p for p in proofs}
    if (len(expected_proofs)!=len(proofs) or set(expected_proofs)!={r['evidence_hash'] for r in rows}
            or any(p['evidence_hash']!=digest(json.loads(p['payload'])) for p in proofs)):
        raise ValueError('Case evidence preimage/fingerprint differs')
    for offset in range(0,len(proofs),128):
        packet=proofs[offset:offset+128];hashes=','.join(literal(p['evidence_hash']) for p in packet)
        existing=query(f'SELECT DISTINCT * FROM {database}.{evidence_table().name} WHERE evidence_hash IN ({hashes}) LIMIT 129')
        existing_hashes=set()
        for p in existing:
            if p!=expected_proofs.get(p['evidence_hash']) or p['evidence_hash'] in existing_hashes:
                raise ValueError('Persisted case evidence conflicts')
            existing_hashes.add(p['evidence_hash'])
        insert_rows(query,database,evidence_table().name,[p for p in packet if p['evidence_hash'] not in existing_hashes])
        verified=query(f'SELECT DISTINCT * FROM {database}.{evidence_table().name} WHERE evidence_hash IN ({hashes}) LIMIT 129')
        if sorted(verified,key=lambda p:p['evidence_hash'])!=sorted(packet,key=lambda p:p['evidence_hash']):
            raise ValueError('Case evidence exact readback failed')
    stored=readback()
    if len(stored)!=len(rows) or {r['entity_key']:r for r in stored}!=expected:
        raise ValueError('Per-case decision exact readback failed')
    source_guard()
    s=result['summary'];header=dict(review_hash=result['review_hash'],version=VERSION,
        baseline_hash=result['baseline_hash'],source_evidence_hash=result['source_evidence_hash'],
        range_start=result['range_start'],range_end=result['range_end'],
        reviewed=s['reviewed'],identity_resolved=s['identity_resolved'],blocked=s['blocked'],
        rows_hash=digest(sorted(stored,key=lambda r:r['entity_key'])))
    header['record_hash']=digest(header)
    headers=query(f'SELECT DISTINCT * FROM {database}.{receipt_table().name} WHERE {where} LIMIT 2')
    if headers and headers!=[header]:raise ValueError('Competing case review receipt')
    if not headers:insert_rows(query,database,receipt_table().name,[header])
    if query(f'SELECT DISTINCT * FROM {database}.{receipt_table().name} WHERE {where} LIMIT 2')!=[header]:
        raise ValueError('Case review receipt readback failed')
    verify(query,database,required=True)
    return dict(inserted=len(pending),reused=len(old),verified=len(stored))
