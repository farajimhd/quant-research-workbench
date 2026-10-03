"""V6 US exchange-listed stock scope, independently checked at bank creation.

Historical membership still comes from the certified pre-open snapshot.
Canonical instrument/exchange metadata supplies scope, never current activity
or prices. Missing metadata is excluded with an explicit reason.
"""
from services.reference_gateway.tradability import is_otc_venue
from research.rl_trading.v1.common import digest
import json
from research.mlops.clickhouse import (ClickHouseHttpClient,default_clickhouse_url,
    default_clickhouse_user,default_clickhouse_password)

VERSION = 'rl-v6-us-exchange-listed-stocks-v1'


def exclusion(row):
    if not row:
        return 'missing_canonical_scope_metadata'
    if is_otc_venue(*(row.get(k) for k in ('exchange_code','acronym','mic','operating_mic','exchange_name'))):
        return 'unsupported_otc_venue'
    if str(row.get('country') or '').upper() != 'US':
        return 'non_us_or_unknown_exchange'
    if str(row.get('currency') or '').upper() != 'USD':
        return 'non_usd_currency'
    if str(row.get('product_type') or '').upper() not in ('STK','STOCK','STOCKS'):
        return 'unsupported_product_type'
    if str(row.get('exchange_code') or '').upper() == 'IBEOS' or 'OVERNIGHT' in str(row.get('exchange_name') or '').upper():
        return 'nonstandard_overnight_venue'
    return None


def filter_population(population, metadata):
    groups={}
    for row in metadata:
        distinct=groups.setdefault(row['listing_id'],{})
        distinct[json.dumps(row,sort_keys=True)]=row
    selected=[]; rejected=[]; evidence=[]
    for listing in population:
        identity=listing['listing_id']; rows=list(groups.get(identity,{}).values())
        row=rows[0] if len(rows)==1 else None
        reason='ambiguous_canonical_scope_metadata' if len(rows)>1 else exclusion(row)
        evidence.extend(rows)
        if reason: rejected.append(dict(listing_id=identity,ticker=listing['ticker'],reason=reason))
        else: selected.append(listing)
    if not selected:
        raise ValueError('US exchange-listed stock universe is empty')
    proof=dict(version=VERSION,input_count=len(population),selected_count=len(selected),
        selected_ids=[r['listing_id'] for r in selected],excluded=rejected,
        evidence=evidence,metadata_authority='q_live canonical listing/security/exchange; static scope only; no current activity filter')
    proof['hash']=digest(proof)
    return selected,proof


def scope_population(client,population):
    # Dedicated fixed SELECT for static canonical scope. Preserve the shared
    # ARTE reader's narrower market-data SQL boundary without weakening it.
    statement="""SELECT l.listing_id AS listing_id,
        l.exchange_code AS exchange_code,l.currency_code AS currency,
        sec.product_type AS product_type,ex.iso_country_code AS country,
        ex.acronym AS acronym,ex.mic AS mic,ex.operating_mic AS operating_mic,
        ex.name AS exchange_name
        FROM q_live.id_listing_v1 AS l FINAL
        INNER JOIN q_live.id_security_v1 AS sec FINAL ON sec.security_id=l.security_id
        LEFT JOIN q_live.ref_exchange_v1 AS ex FINAL ON ex.exchange_code=l.exchange_code
        FORMAT JSONEachRow"""
    reader=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),
        default_clickhouse_password(),timeout_seconds=90,
        default_query_params=dict(readonly=1,max_threads=1,max_execution_time=60,
            max_memory_usage=536870912,max_result_rows=100000,result_overflow_mode='throw'))
    try:
        metadata=[json.loads(line) for line in reader.execute(statement).splitlines() if line]
    finally:
        reader.close()
    return filter_population(population,metadata)


def require_scope(plan):
    proof=plan.get('universe_scope',{})
    if proof.get('version')!=VERSION or proof.get('hash')!=digest({k:v for k,v in proof.items() if k!='hash'}):
        raise ValueError('US exchange-listed stock scope receipt required')
    if set(proof['selected_ids'])!=set(plan['census']):
        raise ValueError('Bank membership differs from certified US stock scope')
    evidence={r['listing_id']:r for r in proof['evidence']}
    if any(exclusion(evidence.get(i)) for i in proof['selected_ids']):
        raise ValueError('Out-of-scope listing in training bank')
    return proof
