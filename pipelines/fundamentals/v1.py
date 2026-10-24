"""Selected production features. Existing observations and receipts stay immutable.

Mappings select alternatives, never add alternatives or subsidiary values.
Public filing dates are conservatively usable after the next New York midnight.
Recorded-only reference observations never acquire an invented public date.
"""
from datetime import date, datetime
from .adapters import ADAPTERS
from .eligibility import require_eligible
from .queries import _timestamp
from pipelines.certified_research.contracts import literal, identifier, digest

VERSION='fundamentals-selected-v1'
# Feature, temporal kind, ordered explicit standard tags. Different debt
# components remain separate inputs; no overlapping total is blindly summed.
FINANCIAL={
 'revenue':('duration',('RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','Revenue')),
 'operating_income':('duration',('OperatingIncomeLoss','ProfitLossFromOperatingActivities')),
 'net_income_common':('duration',('NetIncomeLossAvailableToCommonStockholdersBasic',)),
 'net_income_parent':('duration',('NetIncomeLoss','ProfitLossAttributableToOwnersOfParent')),
 'eps_diluted':('duration',('EarningsPerShareDiluted','DilutedEarningsLossPerShare')),
 'cash':('instant',('CashAndCashEquivalentsAtCarryingValue','CashAndCashEquivalents')),
 'assets':('instant',('Assets',)), 'liabilities':('instant',('Liabilities',)),
 'equity_parent':('instant',('StockholdersEquity','EquityAttributableToOwnersOfParent')),
 'current_assets':('instant',('AssetsCurrent','CurrentAssets')),
 'current_liabilities':('instant',('LiabilitiesCurrent','CurrentLiabilities')),
 'debt_total':('instant',('Borrowings',)),
 'long_term_debt_including_current':('instant',('LongTermDebtCurrentAndNoncurrent',)),
 'long_term_debt_and_finance_leases':('instant',('LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities',)),
 'debt_current':('instant',('LongTermDebtCurrent','CurrentPortionOfLongTermDebt')),
 'debt_noncurrent':('instant',('LongTermDebtNoncurrent','NoncurrentBorrowings')),
 'operating_cash_flow':('duration',('NetCashProvidedByUsedInOperatingActivities','CashFlowsFromUsedInOperatingActivities')),
 'capital_expenditure':('duration',('PaymentsToAcquirePropertyPlantAndEquipment','PurchaseOfPropertyPlantAndEquipment')),
 'shares_outstanding':('instant',('CommonStockSharesOutstanding','EntityCommonStockSharesOutstanding')),
 'shares_diluted_weighted':('duration',('WeightedAverageNumberOfDilutedSharesOutstanding','AdjustedWeightedAverageShares')),
}
REFERENCE_FAMILIES=('short_interest','short_volume','borrow','float','overview','splits','dividends','ipo')
FAMILIES=('sec_reported','sec_bulk','massive_financials',*REFERENCE_FAMILIES)
MASSIVE={
 'revenue':'revenue','operating_income':'operating_income','net_income_loss_attributable_common_shareholders':'net_income_common',
 'diluted_earnings_per_share':'eps_diluted','cash_and_equivalents':'cash','total_assets':'assets',
 'total_liabilities':'liabilities','total_equity_attributable_to_parent':'equity_parent',
 'total_current_assets':'current_assets','total_current_liabilities':'current_liabilities',
 'net_cash_from_operating_activities':'operating_cash_flow',
 'purchase_of_property_plant_and_equipment':'capital_expenditure','diluted_shares_outstanding':'shares_diluted_weighted',
}


def contract_hash():return digest((VERSION,FINANCIAL,MASSIVE,FAMILIES))


def predicate(family):
    if family not in FAMILIES:raise ValueError('Family outside selected fundamentals v1')
    if family in ('sec_reported','sec_bulk'):
        tags=sorted({t for _,ts in FINANCIAL.values() for t in ts})
        return "taxonomy IN ('us-gaap','ifrs-full','dei') AND tag IN ("+','.join(map(literal,tags))+')'
    if family=='massive_financials':return 'metric_name IN ('+','.join(map(literal,MASSIVE))+')'
    return '1'


def field_cases(expression):
    cases=[]
    for name,(_,tags) in FINANCIAL.items():
        cases.extend((f'{expression} IN ('+','.join(map(literal,tags))+')',literal(name)))
    return 'multiIf('+','.join(cases+["''"])+')'


def financial_sql(ciks,cutoff,*,since=None,known_at=None,database='arte',include_recorded=False):
    """Selected consolidated contexts. Returns all periods, latest visible vintage.

The retained SEC bulk source supplies start/filing dates omitted by the earlier
company-facts adapter. A bounded CIK predicate is applied before every join.
Qualified originals have precedence within the identical period/unit context.
Ties with different values remain conflicts, never arbitrary winners.
"""
    identifier(database);require_eligible('issuer_cik',ciks)
    if not 1<=len(ciks)<=128:raise ValueError('Use 1..128 issuer CIKs per query')
    end=_timestamp(cutoff);ids=','.join(map(literal,sorted(set(ciks))))
    known='' if known_at is None else ' AND observed_at<='+_timestamp(known_at)
    tag="arrayElement(splitByChar(':',f.metric),-1)"
    selected=field_cases(tag)
    fact_scope=f'''SELECT * FROM {database}.reference_fact_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id IN ({ids})
          AND ({field_cases("arrayElement(splitByChar(':',metric),-1)")})!='' '''
    # Source headers are the existing publication commit boundary; no raw row
    # is served without its completed normalized source header.
    base=f'''SELECT f.entity_id cik,{selected} feature,f.metric source_metric,
      c.period_start,c.period_end,c.unit,c.dimensions,s.source_id AS source_id,s.family,
      if(s.family='sec_original_qualified',s.event_key,s.evidence_ref) accession,s.observed_at,
      coalesce(f.text_value,toString(f.value)) value,
      if(s.family='sec_original_qualified',s.available_at,
        if(isNotNull(b.filing_day),toTimeZone(toDateTime64(concat(toString(addDays(b.filing_day,1)),' 00:00:00'),9,'America/New_York'),'UTC'),
          CAST(NULL AS Nullable(DateTime64(9,'UTC'))))) public_at,
      if(s.family='sec_original_qualified',1,2) authority_priority
      FROM ({fact_scope}) f
      INNER JOIN (SELECT * FROM {database}.reference_source_v1 FINAL
        WHERE entity_kind='issuer_cik' AND entity_id IN ({ids})
        AND family IN ('sec_bulk','sec_original_qualified'){known}) s USING(source_id)
      INNER JOIN (SELECT * FROM {database}.reference_context_v1 FINAL
        WHERE context_id IN (SELECT context_id FROM ({fact_scope}))) c USING(context_id)
      LEFT JOIN (SELECT cik,fact_id,source_file_id,any(filed_date) filing_day FROM sec_core.sec_bulk_mirror_xbrl_fact_v3 FINAL
        WHERE cik IN ({ids}) AND {predicate('sec_bulk')} GROUP BY cik,fact_id,source_file_id
        HAVING uniqExact(filed_date)=1) b ON s.family='sec_bulk' AND s.entity_id=b.cik AND s.event_key=b.fact_id
        AND s.source_hash=b.source_file_id
      WHERE c.dimensions IN ('{{}}','[]','') AND isNotNull(c.period_end) AND isNotNull(value)'''
    duration_names=','.join(literal(k) for k,(kind,_) in FINANCIAL.items() if kind=='duration')
    timing='coalesce(public_at,observed_at)' if include_recorded else 'public_at'
    eligible=f'''SELECT *,{timing} available_at,if(isNull(public_at),'recorded_only','public') availability_basis
      FROM ({base}) WHERE isNotNull({timing}) AND {timing}<={end}
      AND (feature NOT IN ({duration_names}) OR isNotNull(period_start))
      AND multiIf(feature IN ('shares_outstanding','shares_diluted_weighted'),unit='shares',
        feature='eps_diluted',match(unit,'^[A-Z]{{3}}/shares$'),match(unit,'^[A-Z]{{3}}$'))'''
    # Return alternative tags separately and expose disagreements. No dimension
    # aggregation, currency conversion or annual/quarter mixing occurs here.
    key='cik,feature,period_start,period_end,unit'
    eligible=f'''SELECT * EXCEPT(best_authority) FROM (SELECT *,min(authority_priority)
      OVER (PARTITION BY {key},accession) best_authority FROM ({eligible}))
      WHERE authority_priority=best_authority'''
    ranked=f'''SELECT *,dense_rank() OVER (PARTITION BY {key}
       ORDER BY available_at DESC,authority_priority ASC) vintage FROM ({eligible})'''
    if since is None:
        return f'''SELECT * EXCEPT(vintage),uniqExact(value) OVER (PARTITION BY {key})>1 conflict
          FROM ({ranked}) WHERE vintage=1 ORDER BY cik,feature,period_end,period_start'''
    start=_timestamp(since)
    if since>=cutoff:raise ValueError('History start must precede cutoff')
    return f'''WITH observations AS ({eligible}) SELECT * FROM observations WHERE available_at>={start}
      UNION ALL SELECT * EXCEPT(rn) FROM (SELECT *,rank() OVER(PARTITION BY {key}
      ORDER BY available_at DESC,authority_priority ASC) rn FROM observations WHERE available_at<{start}) WHERE rn=1'''


def reference_sql(securities,metrics,cutoff,*,since=None,database='arte'):
    """Broker/market snapshots are usable when observed, not at past event dates."""
    from .queries import observations_sql
    allowed={m[0].strip("'") for f in REFERENCE_FAMILIES for m in ADAPTERS[f].metrics
             if m[0].startswith("'")}
    if not set(metrics)<=allowed:raise ValueError('Unknown selected reference metric')
    return observations_sql(database,'security',securities,metrics,cutoff,since=since,clock='recorded')


def coverage_sql(ciks,cutoff,*,database='arte'):
    """Compact feature/period coverage; all numeric payloads remain on server."""
    sql=financial_sql(ciks,cutoff,database=database)
    return f'''SELECT cik,feature,count() observations,countIf(conflict) conflicts,
      min(period_end) first_period,max(period_end) last_period,max(available_at) latest_available
      FROM ({sql}) GROUP BY cik,feature ORDER BY cik,feature'''


def gap_sql(ciks,cutoff,*,database='arte'):
    """Actual filing-context holes, not ticker/day Cartesian products.

    Candidate gaps require review: absent tags can mean not reported or not
    applicable. An unavailable feature never blocks other published features.
    """
    sql=financial_sql(ciks,cutoff,database=database)
    return f'''WITH facts AS ({sql}), periods AS
      (SELECT cik,period_start,period_end,unit,groupUniqArray(accession) candidate_accessions,groupUniqArray(feature) present
       FROM facts WHERE match(unit,'^[A-Z]{{3}}$') GROUP BY cik,period_start,period_end,unit)
      SELECT cik,candidate_accessions,period_start,period_end,unit,feature,'missing_selected_feature_review' reason
      FROM periods ARRAY JOIN if(isNull(period_start),
        ['assets','liabilities','equity_parent','cash'],['revenue','operating_income','operating_cash_flow']) AS feature
      WHERE NOT has(present,feature)'''


def derived(rows):
    """Small serving response only. Never transport historical fact scans here.

    Same-period/currency inputs, all visible at the query cutoff, no subsidiary
    summation. Missing or conflicted inputs produce no fabricated output.
    """
    from decimal import Decimal,InvalidOperation
    groups={}
    for row in rows:
        if row.get('conflict'):continue
        key=(row['cik'],row['period_start'],row['period_end'],row['unit'])
        values=groups.setdefault(key,{})
        try:value=Decimal(row['value'])
        except (InvalidOperation,TypeError):continue
        if not value.is_finite():continue
        prior=values.get(row['feature'])
        values[row['feature']]=value if prior is None or prior==value else False
    out=[]
    for key,values in groups.items():
        for name,left,right,op in (
            ('operating_margin','operating_income','revenue','ratio'),
            ('net_margin_parent','net_income_parent','revenue','ratio'),
            ('free_cash_flow','operating_cash_flow','capital_expenditure','subtract'),
            ('cash_to_debt','cash','debt_total','ratio')):
            a,b=values.get(left),values.get(right)
            if a is None or b is None or a is False or b is False or (op=='ratio' and b==0):continue
            out.append(dict(cik=key[0],period_start=key[1],period_end=key[2],feature=name,
                unit='ratio' if op=='ratio' else key[3],value=str(a/b if op=='ratio' else a-b)))
    return out
