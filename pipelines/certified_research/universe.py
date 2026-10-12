"""Historical research membership from the Gateway's conid and interval graph.

The conid pool is observed from 2026 onward. It anchors identity; listing and
symbol evidence determine dated membership. Current eligibility flags are not
projected backward. Missing lifecycle or mapping evidence is reported explicitly.
"""
from __future__ import annotations

from collections import Counter
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
import re

from .contracts import digest,identifier,literal
from .identity import ListingInterval,validate_intervals
from services.reference_gateway.tradability import otc_venue_predicate_sql


def candidates_sql(context_start,end,anchor_date,*,database='q_live',maximum_rows=100000):
    identifier(database)
    if (any(type(v) is not date for v in (context_start,end,anchor_date)) or context_start>end
            or anchor_date<date(2026,1,1) or type(maximum_rows) is not int or not 1<=maximum_rows<=1000000):
        raise ValueError('Invalid historical universe scope')
    # Current valid conids and retained historical conids both contribute. A
    # listing with conflicting anchors is quarantined, never picked arbitrarily.
    otc=otc_venue_predicate_sql('l.exchange_code','e.acronym','e.mic','e.operating_mic','e.name')
    return f"""WITH anchors AS (
      SELECT listing_id,ibkr_conid FROM {database}.id_listing_v1 FINAL
      WHERE match(ifNull(ibkr_conid,''),'^[1-9][0-9]*$')
        AND toDate(last_seen_at_utc) BETWEEN '2026-01-01' AND toDate({literal(anchor_date)})
      UNION DISTINCT SELECT listing_id,ibkr_conid FROM {database}.feature_tradable_universe_v1 FINAL
      WHERE universe_date BETWEEN '2026-01-01' AND toDate({literal(anchor_date)})
        AND match(ifNull(ibkr_conid,''),'^[1-9][0-9]*$')
    ), pool AS (
      SELECT listing_id,arraySort(groupUniqArray(ibkr_conid)) conids FROM anchors GROUP BY listing_id
    ), durable AS (
      SELECT issuer_id,count() durable_ids FROM {database}.id_issuer_identifier_v1 FINAL
      WHERE lower(identifier_kind) IN ('cik','lei','ein') AND notEmpty(identifier_value_normalized)
      GROUP BY issuer_id
    ), duplicates AS (
      SELECT DISTINCT arrayJoin(issuers) issuer_id FROM (
        SELECT groupUniqArray(issuer_id) issuers FROM {database}.id_issuer_identifier_v1 FINAL
        WHERE lower(identifier_kind) IN ('cik','lei','ein') AND notEmpty(identifier_value_normalized)
        GROUP BY lower(identifier_kind),identifier_value_normalized HAVING length(issuers)>1)
    ), issues AS (
      SELECT DISTINCT source_entity_key FROM {database}.id_mapping_issue_v1 FINAL
      WHERE lower(issue_status) NOT IN ('resolved','closed','ignored') AND notEmpty(source_entity_key)
    )
    SELECT p.listing_id listing_id,l.security_id security_id,s.issuer_id issuer_id,p.conids,
      l.exchange_code exchange_code,l.currency_code,
      l.listing_status,l.list_date,l.delisted_date,s.product_type,s.status security_status,
      e.iso_country_code exchange_country,e.acronym,e.mic,e.operating_mic,e.name exchange_name,
      i.symbol_interval_id,i.ticker_normalized ticker,i.valid_from_date,i.valid_to_date_exclusive,
      i.security_id interval_security_id,c.security_id history_security_id,c.listing_id history_listing_id,
      i.mapping_status interval_mapping,c.source_status history_status,c.mapping_status history_mapping,
      multiIf(notEmpty(ifNull(e.iso_country_code,'')) AND upper(e.iso_country_code)!='US','non_us_exchange',
        notEmpty(l.currency_code) AND upper(l.currency_code)!='USD','non_usd_currency',
        notEmpty(s.product_type) AND upper(s.product_type) NOT IN ('STK','STOCK','STOCKS'),'unsupported_product_type',
        {otc},'otc_venue','') scope_reason,
      d.durable_ids>0 durable_issuer,dup.issuer_id!='' duplicate_issuer,
      (match(l.source_content_sha256,'^[0-9a-f]{{64}}$') AND match(s.source_content_sha256,'^[0-9a-f]{{64}}$')
        AND match(i.source_content_sha256,'^[0-9a-f]{{64}}$')) source_hashes_valid,
      (l.listing_id IN (SELECT source_entity_key FROM issues) OR l.security_id IN (SELECT source_entity_key FROM issues)
        OR s.issuer_id IN (SELECT source_entity_key FROM issues) OR i.ticker_normalized IN (SELECT source_entity_key FROM issues)) mapping_issue,
      toJSONString(tuple(l.listing_id,l.security_id,s.issuer_id,p.conids,l.exchange_code,l.currency_code,
        l.listing_status,l.list_date,l.delisted_date,s.product_type,s.status,e.exchange_id,e.iso_country_code,
        e.acronym,e.mic,e.operating_mic,e.name,i.symbol_interval_id,i.provider_entity_key,i.ticker_normalized,
        i.valid_from_date,i.valid_to_date_exclusive,i.security_id,i.mapping_status,c.security_id,c.listing_id,
        c.source_status,c.mapping_status,
        c.source_response_sha256,l.source_content_sha256,s.source_content_sha256,i.source_content_sha256,
        durable_issuer,duplicate_issuer,mapping_issue)) source_proof,
      lower(hex(SHA256(source_proof))) evidence_hash
    FROM pool p LEFT JOIN {database}.id_listing_v1 l FINAL ON l.listing_id=p.listing_id
      LEFT JOIN {database}.id_security_v1 s FINAL ON s.security_id=l.security_id
      LEFT JOIN {database}.ref_exchange_v1 e FINAL ON e.exchange_code=l.exchange_code
      LEFT JOIN {database}.id_symbol_interval_v1 i FINAL ON i.listing_id=l.listing_id AND i.is_deleted=0
        AND i.valid_from_date<=toDate({literal(end)})
        AND (i.valid_to_date_exclusive IS NULL OR i.valid_to_date_exclusive>toDate({literal(context_start)}))
      LEFT JOIN {database}.market_ticker_event_entity_coverage_v1 c FINAL ON c.provider_entity_key=i.provider_entity_key
      LEFT JOIN durable d ON d.issuer_id=s.issuer_id LEFT JOIN duplicates dup ON dup.issuer_id=s.issuer_id
    ORDER BY p.listing_id,i.valid_from_date,i.symbol_interval_id LIMIT {maximum_rows+1}"""


def resolve(rows,context_start,end,*,maximum_rows=100000):
    if type(context_start) is not date or type(end) is not date or context_start>end:
        raise ValueError('Invalid historical membership range')
    if len(rows)>maximum_rows:raise ValueError('Historical universe metadata exceeds its configured bound')
    intervals=[];rejected=[];outside=0;outside_scope=Counter()
    ny=ZoneInfo('America/New_York')
    parse=lambda v:v if type(v) is date else date.fromisoformat(v) if v else None
    for row in rows:
        reason=''
        from services.reference_gateway.tradability import is_otc_venue,SUPPORTED_PRODUCT_TYPES
        scope_reason=next((reason for condition,reason in (
            (bool(row.get('exchange_country')) and row['exchange_country'].upper()!='US','non_us_exchange'),
            (bool(row.get('currency_code')) and row['currency_code']!='USD','non_usd_currency'),
            (bool(row.get('product_type')) and row['product_type'].upper() not in SUPPORTED_PRODUCT_TYPES,'unsupported_product_type'),
            (is_otc_venue(row.get('exchange_code'),row.get('acronym'),row.get('mic'),row.get('operating_mic'),row.get('exchange_name')),'otc_venue'))
            if condition),'')
        if scope_reason:
            outside_scope[scope_reason]+=1;continue
        conids=row['conids']
        listing_start=parse(row.get('list_date'));listing_end=parse(row.get('delisted_date'))
        interval_start=parse(row.get('valid_from_date'));interval_end=parse(row.get('valid_to_date_exclusive'))
        invalid_dates=(listing_start is not None and listing_end is not None and listing_start>=listing_end)
        invalid_dates=invalid_dates or (interval_start is not None and interval_end is not None and interval_start>=interval_end)
        if not invalid_dates and listing_start and (listing_start>end or (listing_end and listing_end<=context_start)):
            outside+=1;continue
        if invalid_dates:reason='invalid_identity_date_order'
        elif not row.get('security_id') or not row.get('issuer_id'):reason='missing_canonical_identity'
        elif not row.get('exchange_country') or not row.get('currency_code') or not row.get('product_type'):
            reason='unresolved_market_scope'
        elif len(conids)!=1 or not re.fullmatch('[1-9][0-9]*',str(conids[0])) or int(conids[0])>=2**64:
            reason='conflicting_or_invalid_conid_anchor'
        elif not row.get('symbol_interval_id') or row.get('interval_mapping')!='mapped':reason='missing_dated_symbol_mapping'
        elif row.get('history_status')!='completed' or row.get('history_mapping')!='mapped':reason='uncertified_ticker_history'
        elif (row.get('interval_security_id')!=row['security_id'] or row.get('history_security_id')!=row['security_id']
                or row.get('history_listing_id')!=row['listing_id']):reason='historical_identity_mismatch'
        elif not row.get('source_hashes_valid'):reason='missing_source_hashes'
        elif listing_start is None:reason='missing_listing_start'
        elif listing_end is None and row.get('listing_status')!='active':reason='missing_listing_end'
        elif interval_start is None:reason='missing_symbol_start'
        elif row.get('security_status')!='active' and listing_end is None:reason='undated_security_inactivation'
        elif row.get('mapping_issue'):reason='open_mapping_issue'
        if reason:
            rejected.append(dict(listing_id=row['listing_id'],ticker=row.get('ticker',''),reason=reason,evidence_hash=row['evidence_hash']))
            continue
        first=max(context_start,listing_start,interval_start)
        last=min(end+timedelta(days=1),listing_end or end+timedelta(days=1),interval_end or end+timedelta(days=1))
        if first>=last:
            outside+=1;continue # Proven outside context, not an unresolved exclusion.
        exchange=row['exchange_code']
        if is_otc_venue(exchange,row.get('acronym'),row.get('mic'),row.get('operating_mic'),row.get('exchange_name')):
            exchange='OTC'
        intervals.append(ListingInterval(row['listing_id'],row['security_id'],row['issuer_id'],row['ticker'],
            int(conids[0]),datetime.combine(first,time(),ny),datetime.combine(last,time(),ny),
            row.get('exchange_country') or '',exchange,row['currency_code'],row['product_type'],True,
            bool(row['durable_issuer']),bool(row['duplicate_issuer']),bool(row['mapping_issue']),row['evidence_hash']))
    validate_intervals(intervals)
    return dict(intervals=tuple(intervals),rejected=tuple(rejected),
                rejection_counts=dict(Counter(r['reason'] for r in rejected)),
                outside_context=outside,outside_scope=dict(outside_scope),ready=not rejected,
                source_hash=digest(sorted(r['evidence_hash'] for r in rows)))


def load(query,context_start,end,anchor_date,*,database='q_live',maximum_rows=100000,metadata_shards=4):
    # Large repeated provenance stays server-side. Publishers can INSERT SELECT
    # it from candidates_sql; planning fetches only compact fields and hashes.
    projection=candidates_sql(context_start,end,anchor_date,database=database,maximum_rows=maximum_rows)
    if type(metadata_shards) is not int or not 1<=metadata_shards<=64:raise ValueError('Invalid metadata shard count')
    census=query(f'SELECT scope_reason,count() n,lower(hex(SHA256(toJSONString(arraySort(groupArray(evidence_hash)))))) '
                 f'fingerprint FROM ({projection}) GROUP BY scope_reason')
    if sum(int(r['n']) for r in census)>maximum_rows:
        raise ValueError('Historical universe metadata exceeds its configured bound')
    rows=[]
    for shard in range(metadata_shards):
        rows.extend(query(f'SELECT * EXCEPT source_proof FROM ({projection}) '
                          f"WHERE scope_reason='' AND cityHash64(listing_id)%{metadata_shards}={shard}"))
        if len(rows)>maximum_rows:raise ValueError('Historical universe metadata exceeds its configured bound')
    result=resolve(rows,context_start,end,maximum_rows=maximum_rows)
    scoped=next((int(r['n']) for r in census if r['scope_reason']==''),0)
    expected=next((r['fingerprint'] for r in census if r['scope_reason']==''),digest([]))
    if len(rows)!=scoped or digest(sorted(r['evidence_hash'] for r in rows))!=expected:
        raise ValueError('Historical universe changed during metadata reads')
    result['outside_scope']={r['scope_reason']:int(r['n']) for r in census if r['scope_reason']}
    result['source_hash']=digest(sorted(census,key=lambda r:r['scope_reason']))
    result['source_census']=census
    result['source_rows']=rows # Bounded provenance metadata, never event/bar rows.
    return result
