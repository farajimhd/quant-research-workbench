"""Reusable retained identity reconciliation, separate from live admission.

Resolved means a unique canonical security/conid association, not a broker
execution entitlement or a certified historical listing interval. OTC and
inactive entities are retained. No provider requests or canonical overwrites.
"""
from collections import Counter, defaultdict
from datetime import date
import re

from .contracts import digest, identifier, literal
from .schema import Table, c
from services.reference_gateway.ibkr_contract_identity import normalize_equity_symbol

VERSION = 'retained-massive-conid-v2'
TABLE = 'research_massive_conid_reconciliation_v1'


def table():
    return Table(TABLE, (
        c('snapshot_hash', 'FixedString(64)'), c('entity_key', 'String'),
        c('ticker', 'LowCardinality(String)'), c('provider_type', 'LowCardinality(String)'),
        c('provider_active', 'UInt8'), c('provider_exchange', 'LowCardinality(String)'),
        c('status', 'LowCardinality(String)'), c('reason', 'LowCardinality(String)'),
        c('security_id', 'String'), c('conid', 'UInt64'), c('listing_ids', 'Array(String)'),
        c('candidate_conids', 'Array(UInt64)'), c('evidence_hash', 'FixedString(64)'),
    ), ('snapshot_hash', 'entity_key'))


def receipt_table():
    return Table('research_massive_conid_snapshot_v1', (
        c('snapshot_hash', 'FixedString(64)'), c('version', 'LowCardinality(String)'),
        c('source_hash', 'FixedString(64)'), c('range_start', 'Date'), c('range_end', 'Date'),
        c('entities', 'UInt32'), c('resolved', 'UInt32'), c('rows_hash', 'FixedString(64)'),
    ), ('snapshot_hash',))


def load(query, start, end, *, database='q_live', maximum_entities=50000):
    identifier(database)
    if type(start) is not date or type(end) is not date or start > end:
        raise ValueError('Invalid reconciliation range')
    if not 1 <= maximum_entities <= 50000:
        raise ValueError('Invalid reconciliation metadata bound')
    # Projection and aggregation happen in CH; no tick data is transported.
    sources = {
        'entities': f"SELECT provider_entity_key AS entity_key,current_ticker AS ticker,entity_name AS name,"
            "active,ifNull(composite_figi,'') AS composite_figi,ifNull(share_class_figi,'') AS share_class_figi,"
            "ifNull(cik,'') AS cik,ifNull(primary_exchange,'') AS exchange,source_content_sha256 AS source_hash,"
            "JSONExtractString(source_payload_json,'type') AS type FROM "
            f"{database}.market_ticker_event_entity_v1 FINAL WHERE is_deleted=0 "
            "AND JSONExtractString(source_payload_json,'market')='stocks' "
            "AND JSONExtractString(source_payload_json,'locale')='us'",
        'identifiers': f"SELECT security_id,lower(identifier_kind) AS kind,identifier_value_normalized AS value,"
            f"valid_from_date,valid_to_date_exclusive,source_content_sha256 AS source_hash FROM {database}.id_security_identifier_v1 FINAL "
            "WHERE lower(identifier_kind) IN ('figi','composite_figi','share_class_figi')",
        'listings': f"SELECT l.listing_id AS listing_id,l.security_id AS security_id,ifNull(l.ibkr_conid,'') AS conid,"
            "l.currency_code AS currency,l.exchange_code AS exchange,s.product_type AS product,s.issuer_id AS issuer_id,"
            "l.source_content_sha256 AS listing_hash,s.source_content_sha256 AS security_hash "
            f"FROM {database}.id_listing_v1 l FINAL INNER JOIN {database}.id_security_v1 s FINAL ON l.security_id=s.security_id",
        'coverage': f"SELECT provider_entity_key AS entity_key,ifNull(security_id,'') AS security_id,"
            "ifNull(listing_id,'') AS listing_id,mapping_status,source_status,"
            f"ifNull(source_response_sha256,'') AS source_hash FROM {database}.market_ticker_event_entity_coverage_v1 FINAL",
        'issues': f"SELECT source_entity_key,issue_type,source_content_sha256 AS source_hash FROM {database}.id_mapping_issue_v1 FINAL "
            "WHERE lower(issue_status) NOT IN ('resolved','closed','ignored')",
        'sec_tickers': "SELECT cik,ticker,exchange,mapping_source,source_file_id FROM "
            "sec_core.sec_bulk_mirror_company_ticker_v3 FINAL WHERE is_active=1 "
            "AND mapping_source IN ('company_tickers','company_tickers_exchange')",
        'sec_bridge': f"SELECT cik,ifNull(ticker,'') AS ticker,ifNull(security_id,'') AS security_id,"
            "ifNull(listing_id,'') AS listing_id,issuer_id,mapping_method,mapping_status,ambiguity_status,"
            f"valid_from_date,valid_to_date_exclusive,source_content_sha256 AS source_hash FROM {database}.id_sec_market_bridge_v3 FINAL",
        'events': "SELECT ticker,min(source_date) AS first_date,max(source_date) AS last_date "
            "FROM market_sip_compact.events_ticker_day_index FINAL WHERE event_count>0 AND "
            f"source_date>={literal(start)} AND source_date<={literal(end)} GROUP BY ticker",
    }
    result = {}
    for name, sql in sources.items():
        count = int(query('SELECT count() AS n FROM (' + sql + ')')[0]['n'])
        if count > (maximum_entities if name == 'entities' else 200000):
            raise ValueError('Reconciliation metadata bound exceeded: ' + name)
        result[name] = []
        for offset in range(0, count, 4096):
            result[name].extend(query('SELECT * FROM (' + sql + ') ORDER BY ALL LIMIT 4096 OFFSET ' + str(offset)))
        if len(result[name]) != count:
            raise ValueError('Reconciliation source census changed: ' + name)
    return result


def reconcile(sources, start, end):
    """Use FIGIs or independently supported SEC CIK + class-preserving symbol."""
    def cik_key(value):
        text=str(value or '')
        return str(int(text)) if re.fullmatch('0*[1-9][0-9]{0,9}',text) else ''
    sec_tickers=defaultdict(list)
    ticker_ciks=defaultdict(set)
    for row in sources.get('sec_tickers',[]):
        cik=cik_key(row['cik']);symbol=normalize_equity_symbol(row['ticker'])
        if cik and symbol:
            sec_tickers[(cik,symbol)].append(row);ticker_ciks[symbol].add(cik)
    bridges=defaultdict(list)
    for row in sources.get('sec_bridge',[]):
        if (row['mapping_status']=='active'
                and row['mapping_method']=='issuer_cik_to_active_market_listing'
                and row['valid_to_date_exclusive'] is None):
            bridges[(cik_key(row['cik']),normalize_equity_symbol(row['ticker']))].append(row)
    by_figi = defaultdict(set)
    figi_evidence = defaultdict(list)
    for row in sources['identifiers']:
        figi_evidence[row['value']].append(row)
        # Current identifiers must not be used to silently reinterpret expired
        # identifiers; historical aliases remain owned by the dated graph.
        if row.get('valid_to_date_exclusive') is None:
            by_figi[row['value']].add(row['security_id'])
    listings = defaultdict(list)
    for row in sources['listings']:
        listings[row['security_id']].append(row)
    coverage = defaultdict(list)
    for row in sources['coverage']:
        coverage[row['entity_key']].append(row)
    issue_keys = {r['source_entity_key'] for r in sources['issues']}
    entities = sources['entities']
    if len({r['entity_key'] for r in entities}) != len(entities):
        raise ValueError('Duplicate provider entities')
    rows = []
    for entity in sorted(entities, key=lambda r: r['entity_key']):
        key = entity['entity_key']
        composite = by_figi[entity['composite_figi']] if entity['composite_figi'] else set()
        share = by_figi[entity['share_class_figi']] if entity['share_class_figi'] else set()
        securities = composite or share
        reason = ''
        sec_key=(cik_key(entity['cik']),normalize_equity_symbol(entity['ticker']))
        sec_records=sec_tickers.get(sec_key,[]) if sec_key[0] else []
        bridge_records=bridges.get(sec_key,[]) if sec_records else []
        sec_securities={r['security_id'] for r in bridge_records if r['security_id']}
        # SEC's issuer identity alone never chooses among share classes. Both
        # the independent SEC ticker receipt and a unique market bridge agree.
        sec_supported=(entity['active'] and len(sec_securities)==1 and ticker_ciks[sec_key[1]]=={sec_key[0]}
            and all(r['source_file_id'] for r in sec_records)
            and all(r['source_hash'] and r['listing_id'] and r['security_id'] in sec_securities
                    and r['ambiguity_status']=='unique' for r in bridge_records))
        if composite and share and not composite & share:
            reason = 'conflicting_provider_figis'
        elif composite and share:
            securities = composite & share
        if not reason and sec_supported and securities and securities!=sec_securities:
            reason='sec_figi_security_mismatch'
        if not reason and not securities and entity['active'] and sec_supported:
            securities=sec_securities
            if any(r['security_id'] not in securities for value in
                   {entity['composite_figi'],entity['share_class_figi']}-{''} for r in figi_evidence[value]):
                reason='sec_figi_security_mismatch'
        if not reason and len(securities) != 1:
            reason = 'missing_security_figi_binding' if not securities else 'ambiguous_security_figi_binding'
        security = next(iter(securities)) if len(securities) == 1 else ''
        candidates = [r for r in listings[security] if r['currency'] == 'USD'
                      and r['product'].upper() in ('STK', 'STOCK', 'STOCKS')]
        # No exchange, active-status, or OTC admission filter here.
        eligible = [r for r in candidates if re.fullmatch('[1-9][0-9]*', str(r['conid']))
                    and int(r['conid']) < 2**64]
        conids = sorted({int(r['conid']) for r in eligible})
        listing_ids = sorted({r['listing_id'] for r in eligible})
        if not reason and sec_supported and any(not any(
                candidate['listing_id']==r['listing_id'] and candidate.get('issuer_id')==r['issuer_id']
                for candidate in eligible) for r in bridge_records):
            reason='sec_bridge_listing_mismatch'
        bound = coverage[key]
        if not reason and any(r['mapping_status'] == 'source_conflict' for r in bound):
            reason = 'retained_source_mapping_conflict'
        if not reason and any(r['security_id'] and r['security_id'] != security for r in bound):
            reason = 'coverage_security_mismatch'
        if not reason and any(r['listing_id'] and r['listing_id'] not in listing_ids for r in bound):
            reason = 'coverage_listing_mismatch'
        touched = {key, entity['ticker'], security, *listing_ids, *(r.get('issuer_id','') for r in candidates)}
        if not reason and touched & issue_keys:
            reason = 'open_mapping_issue'
        if not reason and len(conids) != 1:
            reason = 'missing_valid_conid' if not conids else 'ambiguous_security_conid'
        evidence = dict(entity=entity, identifiers=sorted([r for value in
                        sorted({entity['composite_figi'], entity['share_class_figi']} - {''})
                        for r in figi_evidence[value]], key=digest),
                        listings=sorted(candidates, key=digest), coverage=sorted(bound, key=digest),
                        sec_tickers=sorted(sec_records,key=digest),sec_bridge=sorted(bridge_records,key=digest))
        if not reason and (not entity['source_hash'] or any(not r['listing_hash'] or not r['security_hash'] for r in eligible)
                           or any(not r['source_hash'] for r in evidence['identifiers'])):
            reason = 'missing_source_provenance'
        rows.append(dict(entity_key=key, ticker=entity['ticker'], provider_type=entity['type'],
            provider_active=int(entity['active']), provider_exchange=entity['exchange'],
            status='blocked' if reason else 'resolved', reason=reason,
            security_id=security, conid=0 if reason else conids[0], listing_ids=listing_ids,
            candidate_conids=conids, evidence_hash=digest(evidence)))
    # A current spelling cannot simultaneously identify different securities.
    current = defaultdict(list)
    for row in rows:
        if row['provider_active']:
            current[row['ticker']].append(row)
    for peers in current.values():
        if len(peers) > 1:
            for row in peers:
                row.update(status='blocked', reason='competing_active_provider_entities', conid=0)
    owners = defaultdict(set)
    for row in rows:
        if row['status']=='resolved':owners[row['conid']].add(row['security_id'])
    for row in rows:
        if row['status']=='resolved' and len(owners[row['conid']])>1:
            row.update(status='blocked', reason='conid_owned_by_multiple_securities', conid=0)
    source_hash = digest({k: sorted(v, key=digest) for k, v in sources.items()})
    snapshot = digest(dict(version=VERSION, start=start, end=end, source_hash=source_hash, rows=rows))
    records = [dict(snapshot_hash=snapshot, **r) for r in rows]
    resolved_tickers = {r['ticker'] for r in rows if r['status'] == 'resolved'}
    event_tickers = {r['ticker'] for r in sources['events']}
    return dict(version=VERSION, snapshot_hash=snapshot, source_hash=source_hash, range_start=str(start), range_end=str(end), rows=records,
        summary=dict(entities=len(rows), resolved=sum(r['status']=='resolved' for r in rows),
            blocked=sum(r['status']=='blocked' for r in rows),
            reasons=dict(sorted(Counter(r['reason'] for r in rows if r['reason']).items())),
            types={kind:dict(entities=sum(r['provider_type']==kind for r in rows),
                resolved=sum(r['provider_type']==kind and r['status']=='resolved' for r in rows))
                for kind in sorted({r['provider_type'] for r in rows})},
            sec_bridge_resolved_without_figi=sum(row['status']=='resolved' and
                not (by_figi.get(entity['composite_figi']) or by_figi.get(entity['share_class_figi']))
                for row,entity in zip(rows,sorted(entities,key=lambda r:r['entity_key']))),
            event_tickers=len(event_tickers), event_tickers_with_current_identity=len(event_tickers & resolved_tickers),
            event_tickers_requiring_dated_alias_or_identity_resolution=len(event_tickers - resolved_tickers),
            broker_contract_validation=False, historical_membership_certification=False))


def publish(query, database, result, claim, source_guard):
    """Append exact missing rows under ownership; validate every row on resume."""
    from .publication import DictionaryClaim, insert_rows
    from .sec_share_capture import verify
    if not isinstance(claim, DictionaryClaim) or claim.dictionary_database != database:
        raise ValueError('Reconciliation publication requires database dictionary ownership')
    body=[{k:v for k,v in row.items() if k!='snapshot_hash'} for row in result['rows']]
    fingerprint=digest(dict(version=VERSION,start=result['range_start'],end=result['range_end'],
        source_hash=result['source_hash'],rows=body))
    if (result['version']!=VERSION or result['snapshot_hash']!=fingerprint or
            len({r['entity_key'] for r in result['rows']})!=len(body) or
            any(r['snapshot_hash']!=fingerprint for r in result['rows'])):
        raise ValueError('Reconciliation snapshot fingerprint/key validation failed')
    if any((row['status']=='resolved' and (row['conid']<=0 or row['reason'])) or
           (row['status']=='blocked' and (row['conid']!=0 or not row['reason'])) or
           row['status'] not in ('resolved','blocked') for row in body):
        raise ValueError('Reconciliation mapping status validation failed')
    claim.check(); source_guard()
    capacity=query("SELECT free_space,keep_free_space FROM system.disks WHERE name='live_market_ssd'")
    # Conservative bounded metadata allowance, in addition to disk reservation.
    if len(capacity)!=1 or int(capacity[0]['free_space'])-int(capacity[0]['keep_free_space'])<256*1024**2:
        raise ValueError('Reconciliation publication requires 256 MiB of unreserved SSD headroom')
    catalog=(table(), receipt_table())
    verify(query, database, catalog=catalog)
    for t in catalog:claim.check();query(t.ddl(database), read=False)
    verify(query, database, required=True, catalog=catalog)
    expected = {r['entity_key']: r for r in result['rows']}
    where = 'snapshot_hash=' + literal(result['snapshot_hash'])
    def read_rows():
        count=int(query(f'SELECT count() AS n FROM {identifier(database)}.{TABLE} WHERE {where}')[0]['n'])
        if count>50000:raise ValueError('Stored reconciliation snapshot exceeds bound')
        rows=[]
        for offset in range(0,count,4096):
            rows.extend(query(f'SELECT * FROM {database}.{TABLE} WHERE {where} ORDER BY entity_key LIMIT 4096 OFFSET {offset}'))
        if len(rows)!=count:raise ValueError('Stored reconciliation census changed')
        return rows
    existing = read_rows()
    actual = {}
    for row in existing:
        row['conid']=int(row['conid']);row['candidate_conids']=list(map(int,row['candidate_conids']))
        if row['entity_key'] in actual or row != expected.get(row['entity_key']):
            raise ValueError('Conflicting reconciliation snapshot; no overwrite permitted')
        actual[row['entity_key']] = row
    missing = [r for k, r in expected.items() if k not in actual]
    for offset in range(0, len(missing), 512):
        claim.check()
        insert_rows(query, database, TABLE, missing[offset:offset+512])
    readback = read_rows()
    for row in readback:
        row['conid']=int(row['conid']);row['candidate_conids']=list(map(int,row['candidate_conids']))
    if len(readback) != len(expected) or {r['entity_key']: r for r in readback} != expected:
        raise ValueError('Reconciliation exact readback failed')
    source_guard(); claim.check()
    header=dict(snapshot_hash=result['snapshot_hash'], version=VERSION, source_hash=result['source_hash'],
        range_start=result['range_start'], range_end=result['range_end'], entities=len(expected),
        resolved=result['summary']['resolved'], rows_hash=digest(sorted(readback,key=lambda r:r['entity_key'])))
    old=query(f'SELECT * FROM {database}.{receipt_table().name} WHERE {where} LIMIT 2')
    if old and old!=[header]:raise ValueError('Conflicting reconciliation receipt')
    if not old:insert_rows(query,database,receipt_table().name,[header])
    if query(f'SELECT * FROM {database}.{receipt_table().name} WHERE {where} LIMIT 2')!=[header]:
        raise ValueError('Reconciliation receipt readback failed')
    verify(query, database, required=True, catalog=catalog)
    return dict(inserted=len(missing), reused=len(actual), verified=len(readback))
