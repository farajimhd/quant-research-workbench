"""Allowlisted native projections. Payloads stay in ClickHouse.

Unknown publication clocks remain NULL. Recorded observations are NOT certified
public facts; SEC values remain unqualified until the original-document producer
publishes its independently verified research facts.
"""
from dataclasses import dataclass, replace
from datetime import date
from pipelines.certified_research.contracts import digest, literal

NULL_TIME = "CAST(NULL AS Nullable(DateTime64(9,'UTC')))"
NULL_DATE = 'CAST(NULL AS Nullable(Date))'


@dataclass(frozen=True)
class Adapter:
    family: str
    table: str
    key: str
    entity: str
    kind: str
    day: str
    metrics: tuple  # (metric, expression, unit, is_text)
    public: str = NULL_TIME
    observed: str = 'inserted_at'
    start: str = NULL_DATE
    end: str = NULL_DATE
    taxonomy: str = "''"
    dimensions: str = "'{}'"
    quality: str = "'recorded_only'"
    reason: str = "'public_time_unqualified'"
    evidence: str = 'source_evidence_ref'
    source_hash: str = 'source_content_sha256'
    scan_key: str = ''


def numeric(name, column, unit):
    return (name, column, unit, False)


def text(name, column):
    return (name, column, "'text'", True)


ADAPTERS = {a.family: a for a in (
    Adapter('sec_reported', 'q_live.sec_xbrl_company_fact_v3', 'company_fact_id', 'cik', 'issuer_cik',
        'coalesce(toDate(filed_at_utc),toDate(recorded_at_utc))', (numeric("concat(taxonomy,':',tag)", 'value', 'unit_code'),),
        observed='recorded_at_utc',
        end='period_end_date', taxonomy='taxonomy', evidence='accession_number',
        quality="'unqualified'", reason="'original_document_parity_required'"),
    Adapter('sec_bulk', 'sec_core.sec_bulk_mirror_xbrl_fact_v3', 'fact_id', 'cik', 'issuer_cik',
        'coalesce(filed_date,toDate(last_seen_at_utc))', (numeric("concat(taxonomy,':',tag)", 'value', 'unit'),),
        observed='last_seen_at_utc', start='start_date', end='end_date', taxonomy='taxonomy',
        dimensions='dimensions_json', evidence='accession_number', source_hash='source_file_id',
        quality="'unqualified'", reason="'original_document_parity_required'"),
    Adapter('massive_financials', 'trading_dashboard_dev.market_financial_statement_snapshot_v1',
        'financial_snapshot_id', 'issuer_id', 'issuer', 'coalesce(filing_date,period_end_date,toDate(inserted_at))',
        (numeric("concat('massive.',endpoint_family,'.',metric_name)", 'metric_value', 'unit'),),
        end='period_end_date', taxonomy="'massive'", quality="'unqualified'",
        reason="'original_vintage_and_public_time_required'"),
    Adapter('short_interest', 'q_live.market_short_interest_v1', 'source_event_key', 'security_id', 'security',
        'settlement_date', tuple(numeric(literal(n), c, literal(u)) for n,c,u in (
            ('short.interest','short_interest','shares'),('short.provider_adv','avg_daily_volume','shares/day'),
            ('short.provider_days_to_cover','days_to_cover','days'))),
        public='published_at_utc', end='settlement_date'),
    Adapter('short_volume', 'q_live.market_short_volume_v1', 'source_event_key', 'security_id', 'security',
        'trade_date', tuple(numeric(literal(n), c, literal(u)) for n,c,u in (
            ('short.volume','short_volume','shares'),('short.total_volume','total_volume','shares'),
            ('short.exempt_volume','exempt_volume','shares'),('short.non_exempt_volume','non_exempt_volume','shares'),
            ('short.volume_ratio','short_volume_ratio','ratio'))), public='published_at_utc', end='trade_date'),
    Adapter('borrow', 'q_live.market_security_borrow_v1', 'source_event_key', 'security_id', 'security',
        'toDate(observed_at_utc)', (
            numeric("'borrow.shortable_shares'", 'shortable_shares', "'shares'"),
            numeric("'borrow.lender_count'", 'lender_count', "'count'"),
            numeric("'borrow.indicative_rate'", 'indicative_borrow_rate', "'provider_rate'"),
            numeric("'borrow.fee_rate'", 'fee_rate', "'provider_rate'"),
            text("'borrow.status'", 'borrow_status')), observed='observed_at_utc',
        reason="'broker_observation_not_account_order_permission'"),
    Adapter('float', 'q_live.market_security_float_v1', 'source_event_key', 'security_id', 'security',
        'effective_date', (numeric("'float.reported'",'free_float',"'shares'"),
            numeric("'float.percent'",'free_float_percent',"'percent'"),
            numeric("'shares.outstanding'",'shares_outstanding',"'shares'")), end='effective_date'),
    Adapter('overview', 'q_live.market_security_market_snapshot_v1', 'security_market_snapshot_id', 'security_id', 'security',
        'toDate(observed_at_utc)', (numeric("'market.cap'",'market_cap',"'provider_currency'"),
            numeric("'market.round_lot'",'round_lot',"'shares'"),
            numeric("'shares.class_outstanding'",'share_class_shares_outstanding',"'shares'"),
            numeric("'shares.weighted_outstanding'",'weighted_shares_outstanding',"'shares'")),
        observed='observed_at_utc', end='as_of_date', evidence='snapshot_evidence_ref'),
    Adapter('splits', 'q_live.market_stock_split_v1', 'source_event_key', 'security_id', 'security',
        'execution_date', (numeric("'split.from'",'split_from',"'shares'"),
            numeric("'split.to'",'split_to',"'shares'")), end='execution_date'),
    Adapter('dividends', 'q_live.market_cash_dividend_v1', 'source_event_key', 'security_id', 'security',
        'ex_dividend_date', (numeric("'dividend.cash'",'cash_amount',"concat(currency_code,'/share')"),
            text("'dividend.declared'",'toString(declaration_date)'),text("'dividend.pay_date'",'toString(pay_date)'),
            text("'dividend.record_date'",'toString(record_date)'),text("'dividend.type'",'dividend_type')),
        end='ex_dividend_date'),
    Adapter('ftd', 'q_live.market_fails_to_deliver_v1', 'source_event_key', 'security_id', 'security',
        'settlement_date', (numeric("'ftd.quantity'",'fails_quantity',"'shares'"),
            numeric("'ftd.previous_close'",'previous_close_price',"'provider_currency/share'")), end='settlement_date'),
    Adapter('reg_sho', 'q_live.market_reg_sho_threshold_v1', 'source_event_key', 'security_id', 'security',
        'threshold_date', (text("'reg_sho.status'",'threshold_status'),), end='threshold_date',
        quality="if(threshold_date>toDate(inserted_at),'invalid','recorded_only')",
        reason="if(threshold_date>toDate(inserted_at),'event_date_after_observation','public_time_unqualified')"),
    Adapter('ipo', 'q_live.market_ipo_v1', 'source_event_key', 'security_id', 'security', 'listing_date',
        (text("'ipo.status'",'ipo_status'),numeric("'ipo.final_price'",'final_issue_price',"concat(ifNull(currency_code,'unknown'),'/share')"),
         text("'ipo.announced_date'",'toString(announced_date)'),
         text("'ipo.issue_start'",'toString(issue_start_date)'),text("'ipo.issue_end'",'toString(issue_end_date)'),
         numeric("'ipo.highest_offer_price'",'highest_offer_price',"concat(ifNull(currency_code,'unknown'),'/share')"),
         numeric("'ipo.lowest_offer_price'",'lowest_offer_price',"concat(ifNull(currency_code,'unknown'),'/share')"),
         numeric("'ipo.min_shares_offered'",'min_shares_offered',"'shares'"),
         numeric("'ipo.max_shares_offered'",'max_shares_offered',"'shares'"),
         numeric("'ipo.shares_outstanding'",'shares_outstanding',"'shares'"),
         numeric("'ipo.total_offer_size'",'total_offer_size',"ifNull(currency_code,'unknown')")), end='listing_date'),
)}

# Keep provider/venue and fiscal bases distinct. A ticker is an access route,
# never a reason to duplicate issuer facts for every listing or trading day.
for _family, _columns in {
    'sec_reported': ('fiscal_year','fiscal_period','form_type'),
    'massive_financials': ('source_system','endpoint_family','statement_type','fiscal_year','fiscal_period','currency_code'),
    'short_interest': ('source_system','source_venue'),
    'short_volume': ('source_system','source_venue'),
    'borrow': ('source_system','broker'),
    'float': ('source_system','float_source_tag'),
    'overview': ('source_system',), 'splits': ('source_system',),
    'dividends': ('source_system','currency_code','frequency'),
    'ftd': ('source_system','cusip'), 'reg_sho': ('source_system','listing_exchange'),
    'ipo': ('source_system','currency_code','security_type','primary_exchange'),
}.items():
    ADAPTERS[_family] = replace(ADAPTERS[_family], dimensions='toJSONString(map('
        + ','.join(f"{literal(c)},ifNull(toString({c}),'')" for c in _columns) + '))')

for _family, _key in {'short_interest':'short_interest_id','short_volume':'short_volume_id',
        'borrow':'borrow_id','float':'security_float_id','splits':'stock_split_id',
        'dividends':'cash_dividend_id','ftd':'ftd_id','reg_sho':'threshold_id','ipo':'ipo_event_id'}.items():
    ADAPTERS[_family] = replace(ADAPTERS[_family], key=_key)

for _family,_key in {'sec_reported':'cik','sec_bulk':'cik','massive_financials':'issuer_id',
        'short_interest':'symbol_id','short_volume':'symbol_id','borrow':'provider_ticker',
        'float':'symbol_id','overview':'symbol_id','splits':'symbol_id','dividends':'symbol_id',
        'ftd':'provider_ticker','reg_sho':'provider_ticker','ipo':'symbol_id'}.items():
    ADAPTERS[_family]=replace(ADAPTERS[_family],scan_key=_key)


def projection(family, entities, start, end, *, partition_id=None, scan_keys=(), selected=False,observed_since=None,observed_until=None):
    """One source pass per bounded entity cohort; complete contexts, no row loops."""
    a = ADAPTERS[family]
    if not 1 <= len(entities) <= 256 or type(start) is not date or type(end) is not date or end < start:
        raise ValueError('Bounded entities and valid inclusive dates required')
    ids = ','.join(map(literal, sorted(set(entities))))
    if len(scan_keys)>4096:raise ValueError('Too many source index keys in one batch')
    indexed='' if not scan_keys else f'{a.scan_key} IN ({",".join(map(literal,scan_keys))}) AND '
    values = []
    for metric, expression, unit, is_text in a.metrics:
        value = 'CAST(NULL AS Nullable(Float64))' if is_text else f'toFloat64OrNull(toString({expression}))'
        string = f'CAST({expression} AS Nullable(String))' if is_text else 'CAST(NULL AS Nullable(String))'
        values.append(f'tuple({metric},{value},{string},ifNull({unit},\'unknown\'))')
    # Fingerprint the entire normalized source observation, including nulls and
    # clocks. Source-provided hashes are provenance, never trusted as value proof.
    partition = '' if partition_id is None else f' AND _partition_id={literal(partition_id)}'
    if selected:
        from .v1 import predicate
        if family in ('sec_reported','sec_bulk'):
            # Both taxonomy and tag are immutable sorting-key columns in SEC
            # sources. Filter before FINAL deduplication, not after loading all
            # unselected metrics for a multi-decade issuer cohort.
            indexed+='('+predicate(family)+') AND '
        else:partition+=' AND ('+predicate(family)+')'
    for bound,operator in ((observed_since,'>='),(observed_until,'<')):
        if bound is not None:
            from .queries import _timestamp
            partition+=f' AND {a.observed}{operator}{_timestamp(bound)}'
    inner = f"""SELECT {literal(family)} family,{literal(a.kind)} entity_kind,
        toString({a.entity}) entity_id,toString({a.key}) event_key,
        {literal(a.table)} source_table,ifNull(toString({a.source_hash}),'') source_hash,
        ifNull(toString({a.evidence}),'') evidence_ref,
        CAST({a.public} AS Nullable(DateTime64(9,'UTC'))) available_at,
        toDateTime64({a.observed},9,'UTC') observed_at,{a.quality} quality,{a.reason} reason,
        CAST({a.start} AS Nullable(Date)) period_start,CAST({a.end} AS Nullable(Date)) period_end,
        ifNull({a.taxonomy},'') taxonomy,ifNull({a.dimensions},'{{}}') dimensions,
        [{','.join(values)}] metrics
        FROM {a.table} FINAL PREWHERE {indexed}{a.entity} IN ({ids})
        WHERE coalesce({a.day},toDate({a.observed})) BETWEEN toDate({literal(start)}) AND toDate({literal(end)}){partition}"""
    return f"SELECT *,lower(hex(SHA256(toJSONString(tuple(*))))) source_id FROM ({inner})"


def recipe_hash(family=None):
    from dataclasses import asdict
    from . import schema
    # Admission/index tuning and comments do not change fact semantics. Bump the
    # contract version for projection/hash semantic changes; adapter value/unit/
    # clock changes are included automatically.
    adapters={f:{k:v for k,v in asdict(a).items() if k!='scan_key'} for f,a in ADAPTERS.items()
        if family is None or f==family}
    # The additive native-label dictionary does not change existing observation
    # values or hashes. Keep all previously completed normalizer receipts valid.
    definitions={k:v for k,v in schema.DEFINITIONS.items() if k!='reference_metric_definition_v1'}
    return digest({'adapters':adapters,'schema':definitions,'version':schema.VERSION})
