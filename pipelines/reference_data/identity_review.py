"""Conservative direct-issuer corrections from retained SEC ticker evidence.

These are observed-date corrections, not inferred historical lineage. Preserve
the original evidence and quarantine conflicting bridges instead of relabeling
their historical facts.
"""
from datetime import date
from pipelines.certified_research.contracts import digest,literal

METHOD='reviewed_sec_ticker_to_market_listing'


def ensure_retained_issuer(query, ticker, cik, stamp):
    """Add one missing canonical issuer from direct SEC metadata, never an alias.

    Both writes have deterministic keys. A retry after either write verifies and
    reuses it; competing canonical identities stop the correction.
    """
    import hashlib
    import json
    if len(cik)!=10 or not cik.isdigit():raise ValueError('Expected ten-digit CIK')
    evidence=query(f'SELECT * FROM sec_core.sec_bulk_mirror_company_ticker_v3 FINAL WHERE ticker={literal(ticker)} AND is_active=1 ORDER BY mapping_id')
    if not evidence or {x['cik'] for x in evidence}!={cik} or any(not x['source_file_id'] for x in evidence):
        raise ValueError('Missing or competing direct SEC identity evidence')
    companies=query(f'SELECT * FROM sec_core.sec_bulk_mirror_company_v3 FINAL WHERE cik={literal(cik)}')
    if len(companies)!=1 or not companies[0]['entity_name'] or not companies[0]['source_file_id']:
        raise ValueError('Missing canonical SEC company evidence')
    company=companies[0];issuer='issuer:cik:'+cik
    identifiers=query(f"SELECT * FROM q_live.id_issuer_identifier_v1 FINAL WHERE identifier_kind='cik' AND identifier_value_normalized={literal(cik)}")
    if identifiers and {x['issuer_id'] for x in identifiers}!={issuer}:
        raise ValueError('Existing canonical identifier competes with retained issuer')
    existing=query(f'SELECT * FROM q_live.id_issuer_v1 FINAL WHERE issuer_id={literal(issuer)}')
    proof=dict(company=company,tickers=evidence,policy='retained_sec_missing_issuer_v1')
    source_hash=digest(proof)
    if existing and (len(existing)!=1 or existing[0]['source_content_sha256']!=source_hash):
        raise ValueError('Canonical issuer already exists; review instead of overwriting')
    for table in ('id_issuer_v1','id_issuer_identifier_v1'):
        layout=query(f"SELECT engine,storage_policy FROM system.tables WHERE database='q_live' AND name={literal(table)}")
        disks=query(f"SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='q_live' AND table={literal(table)}")
        if layout!=[dict(engine='ReplacingMergeTree',storage_policy='live_market_ssd')] or any(x['disk_name']!='live_market_ssd' for x in disks):
            raise ValueError('Canonical identity requires SSD storage')
    observed=str(company['last_seen_at_utc'])[:23]
    common=dict(first_seen_at_utc=observed,last_seen_at_utc=observed,
                source_run_id='retained_sec_missing_issuer_v1',source_content_sha256=source_hash,inserted_at=stamp)
    row=dict(issuer_id=issuer,issuer_name=company['entity_name'],issuer_name_normalized=company['entity_name'].upper(),
        legal_name=company['entity_name'],branding_name=None,entity_type=None,domicile_country_code=None,
        state_of_incorporation=company['state_of_incorporation'],sic_code=company['sic'],sic_description=company['sic_description'],
        sector=None,industry=None,industry_group=None,website_url=None,investor_website_url=None,logo_asset_id=None,
        status='active',last_verified_at_utc=stamp,**common)
    identity=dict(issuer_identifier_id='issuer_identifier:'+hashlib.sha256((issuer+'|cik|'+cik).encode()).hexdigest(),
        issuer_id=issuer,identifier_kind='cik',identifier_value=cik,identifier_value_normalized=cik,source_system='sec',
        confidence_score=1,is_primary=1,valid_from_date=None,valid_to_date_exclusive=None,
        evidence_json=json.dumps(proof,sort_keys=True),**common)
    written=0
    for table,old,value in [('id_issuer_v1',existing,row),('id_issuer_identifier_v1',identifiers,identity)]:
        if not old:
            query(f'INSERT INTO q_live.{table} FORMAT JSONEachRow\n'+json.dumps(value),read=False);written+=1
    actual=query(f"SELECT i.issuer_id AS issuer_id,d.identifier_value_normalized AS cik FROM q_live.id_issuer_v1 AS i FINAL INNER JOIN q_live.id_issuer_identifier_v1 AS d FINAL ON i.issuer_id=d.issuer_id WHERE i.issuer_id={literal(issuer)} AND d.identifier_kind='cik'")
    if actual!=[dict(issuer_id=issuer,cik=cik)]:raise ValueError('Canonical issuer readback differs')
    return dict(issuer_id=issuer,cik=cik,written=written,evidence_hash=source_hash)


def reviewed_identity(ticker, evidence, issuers, snapshot_day):
    rows=[x for x in evidence if x['ticker']==ticker and x['is_active']==1]
    ciks={x['cik'] for x in rows}
    if len(ciks)!=1:raise ValueError('Retained SEC ticker identity is absent or competing')
    cik=next(iter(ciks))
    matching={x['issuer_id'] for x in issuers if x['cik']==cik}
    if len(matching)!=1:raise ValueError('Canonical issuer identity is absent or competing')
    if any(not x['source_file_id'] for x in rows):raise ValueError('SEC identity evidence lacks source provenance')
    # Do not move the observed current identity backward to listing inception.
    observed=max(date.fromisoformat(str(x['last_seen_at_utc'])[:10]) for x in rows)
    if observed>snapshot_day:raise ValueError('Identity evidence postdates the reviewed population')
    return dict(cik=cik,issuer_id=next(iter(matching)),valid_from_date=str(observed),
                evidence=rows,evidence_hash=digest(rows))


def applicability(forms, *, series_id=None, class_id=None):
    forms=set(forms)
    corporate=bool(forms & {'10-K','10-Q','20-F','40-F','10-K/A','10-Q/A','20-F/A','40-F/A'})
    fund=bool(forms & {'N-CSR','N-CSRS','N-CEN','NPORT-P','N-1A','N-2','N-2/A','N-1A/A','N-CSR/A','NPORT-P/A'})
    if corporate:return 'corporate_financials_applicable'
    if fund:return 'fund_specific_not_corporate_v1'
    # Names, fee taxonomy and shareholder filings do not prove fund status.
    return 'unresolved_applicability'


def prepare(query,tickers,snapshot_day):
    if not 1<=len(tickers)<=16:raise ValueError('Review at most 16 explicit ticker conflicts')
    ticks=','.join(map(literal,sorted(set(tickers))))
    evidence=query(f'SELECT * FROM sec_core.sec_bulk_mirror_company_ticker_v3 FINAL WHERE ticker IN ({ticks}) ORDER BY ticker,cik,mapping_id')
    ciks=','.join(map(literal,sorted({x['cik'] for x in evidence})))
    if not ciks:raise ValueError('No retained identity evidence')
    issuers=query(f"SELECT DISTINCT identifier_value_normalized cik,issuer_id FROM q_live.id_issuer_identifier_v1 FINAL WHERE identifier_kind='cik' AND identifier_value_normalized IN ({ciks}) ORDER BY cik,issuer_id")
    listings=query(f'''SELECT u.ticker AS ticker,u.symbol_id AS symbol_id,u.security_id AS security_id,
        s.listing_id AS listing_id,l.list_date AS list_date,l.delisted_date AS delisted_date
        FROM q_live.feature_tradable_universe_v1 AS u FINAL
        INNER JOIN q_live.id_symbol_v1 AS s FINAL ON u.symbol_id=s.symbol_id
        INNER JOIN q_live.id_listing_v1 AS l FINAL ON s.listing_id=l.listing_id
        WHERE u.universe_date=toDate({literal(snapshot_day)}) AND u.is_tradable=1
        AND u.ticker IN ({ticks}) ORDER BY u.ticker,u.symbol_id''')
    prior=query(f'SELECT * FROM q_live.id_sec_market_bridge_v3 FINAL WHERE ticker IN ({ticks}) ORDER BY ticker,cik,bridge_id')
    decisions=[]
    for ticker in sorted(set(tickers)):
        identity=reviewed_identity(ticker,evidence,issuers,snapshot_day)
        targets=[x for x in listings if x['ticker']==ticker]
        if len(targets)!=1:raise ValueError('Reviewed listing is absent or competing')
        target=targets[0]
        begin=max(identity['valid_from_date'],str(target['list_date'] or identity['valid_from_date']))
        if begin>str(snapshot_day):raise ValueError('Listing/identity begins after snapshot')
        decisions.append(dict(ticker=ticker,identity=identity,listing=target,valid_from_date=begin))
    body=dict(tickers=sorted(set(tickers)),snapshot_day=str(snapshot_day),decisions=decisions,prior=prior)
    return dict(body,plan_hash=digest(body))


def publish(query,plan,stamp):
    import json
    body=dict(plan);sealed=body.pop('plan_hash')
    if digest(body)!=sealed:raise ValueError('Identity plan changed')
    if prepare(query,plan['tickers'],date.fromisoformat(plan['snapshot_day']))!=plan:
        raise ValueError('Retained identity evidence changed before publication')
    tables=query("SELECT engine,storage_policy FROM system.tables WHERE database='q_live' AND name='id_sec_market_bridge_v3'")
    disks=query("SELECT DISTINCT disk_name FROM system.parts WHERE active AND database='q_live' AND table='id_sec_market_bridge_v3'")
    if tables!=[dict(engine='ReplacingMergeTree',storage_policy='live_market_ssd')] or any(x['disk_name']!='live_market_ssd' for x in disks):
        raise ValueError('Identity authority is not on required SSD')
    rows=[]
    for decision in plan['decisions']:
        i=decision['identity'];l=decision['listing']
        current=[x for x in plan['prior'] if x['symbol_id']==l['symbol_id'] and x['mapping_status']=='active' and x['mapping_method']!='filing_issuer_to_listed_parent']
        if len(current)==1 and all(current[0].get(k)==v for k,v in dict(cik=i['cik'],issuer_id=i['issuer_id'],mapping_method=METHOD,source_content_sha256=i['evidence_hash'],valid_from_date=decision['valid_from_date']).items()):
            continue
        proof=json.dumps(dict(policy=METHOD,scope='observed_identity_not_historical_lineage',plan_hash=sealed,**decision),sort_keys=True)
        rows.append(dict(bridge_id='sec-market-reviewed:'+digest((l['symbol_id'],i['cik'])),cik=i['cik'],issuer_id=i['issuer_id'],
          security_id=l['security_id'],listing_id=l['listing_id'],symbol_id=l['symbol_id'],ticker=l['ticker'],accession_number=None,
          valid_from_date=decision['valid_from_date'],valid_to_date_exclusive=l['delisted_date'],mapping_method=METHOD,
          mapping_status='active',confidence_score=.95,ambiguity_status='unique',evidence_json=proof,
          first_seen_at_utc=stamp,last_seen_at_utc=stamp,source_run_id='fundamentals-targeted-identity-v1',
          source_content_sha256=i['evidence_hash'],inserted_at=stamp))
        for old in plan['prior']:
            if old['symbol_id']==l['symbol_id'] and old['cik']!=i['cik'] and old['mapping_method']!='filing_issuer_to_listed_parent':
                rows.append(dict(old,mapping_status='quarantined',last_seen_at_utc=stamp,inserted_at=stamp,
                    evidence_json=json.dumps(dict(prior=old,correction_plan_hash=sealed,reason='retained_SEC_direct_issuer_conflict'),sort_keys=True)))
    if rows:query('INSERT INTO q_live.id_sec_market_bridge_v3 FORMAT JSONEachRow\n'+'\n'.join(json.dumps(x) for x in rows),read=False)
    for d in plan['decisions']:
        actual=query(f"SELECT DISTINCT cik,issuer_id,mapping_method,valid_from_date FROM q_live.id_sec_market_bridge_v3 FINAL WHERE symbol_id={literal(d['listing']['symbol_id'])} AND mapping_status='active' AND mapping_method!='filing_issuer_to_listed_parent'")
        expected=dict(cik=d['identity']['cik'],issuer_id=d['identity']['issuer_id'],mapping_method=METHOD,valid_from_date=d['valid_from_date'])
        if actual!=[expected]:raise ValueError('Reviewed issuer bridge readback differs')
    return dict(plan_hash=sealed,corrected=len(plan['decisions']),written=len(rows))
