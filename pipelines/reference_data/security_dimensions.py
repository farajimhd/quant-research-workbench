from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class SecurityDimensionContext:
    ticker: str
    cik: str
    symbol_id: str
    listing_id: str = ""
    security_id: str = ""
    issuer_id: str = ""


@dataclass(frozen=True, slots=True)
class SecurityDimension:
    code: str
    label: str
    group: str
    source_system: str
    source_table: str
    value_unit: str
    value_type: str = "numeric"
    description: str = ""
    default_plot: bool = False
    scanner_default: bool = False


SEC_XBRL_DIMENSIONS: tuple[tuple[str, str, str, str, str, bool, bool], ...] = (
    (
        "sec_entity_common_stock_shares_outstanding",
        "SEC common shares outstanding",
        "share_supply",
        "EntityCommonStockSharesOutstanding",
        "shares",
        True,
        False,
    ),
    (
        "sec_common_stock_shares_outstanding",
        "SEC common stock shares outstanding",
        "share_supply",
        "CommonStockSharesOutstanding",
        "shares",
        True,
        False,
    ),
    (
        "sec_common_stock_shares_issued",
        "SEC common shares issued",
        "share_supply",
        "CommonStockSharesIssued",
        "shares",
        False,
        False,
    ),
    (
        "sec_common_stock_shares_authorized",
        "SEC authorized common shares",
        "share_supply",
        "CommonStockSharesAuthorized",
        "shares",
        False,
        False,
    ),
    (
        "sec_weighted_avg_basic_shares",
        "SEC weighted avg basic shares",
        "share_supply",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "shares",
        True,
        False,
    ),
    (
        "sec_weighted_avg_diluted_shares",
        "SEC weighted avg diluted shares",
        "share_supply",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "shares",
        True,
        False,
    ),
    (
        "sec_share_based_incremental_shares",
        "SEC share-based incremental shares",
        "share_supply",
        "IncrementalCommonSharesAttributableToShareBasedPaymentArrangements",
        "shares",
        False,
        False,
    ),
    (
        "sec_antidilutive_excluded_shares",
        "SEC antidilutive excluded shares",
        "share_supply",
        "AntidilutiveSecuritiesExcludedFromComputationOfEarningsPerShareAmount",
        "shares",
        False,
        False,
    ),
    ("sec_entity_public_float", "SEC public float", "float", "EntityPublicFloat", "USD", True, False),
    ("sec_revenue", "SEC revenue", "fundamentals", "Revenues", "USD", False, False),
    (
        "sec_contract_revenue",
        "SEC contract revenue",
        "fundamentals",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "USD",
        False,
        False,
    ),
    ("sec_sales_revenue_net", "SEC sales revenue net", "fundamentals", "SalesRevenueNet", "USD", False, False),
    ("sec_gross_profit", "SEC gross profit", "fundamentals", "GrossProfit", "USD", False, False),
    ("sec_operating_income_loss", "SEC operating income", "fundamentals", "OperatingIncomeLoss", "USD", False, False),
    ("sec_net_income_loss", "SEC net income", "fundamentals", "NetIncomeLoss", "USD", False, False),
    ("sec_eps_basic", "SEC EPS basic", "fundamentals", "EarningsPerShareBasic", "USD/share", False, False),
    ("sec_eps_diluted", "SEC EPS diluted", "fundamentals", "EarningsPerShareDiluted", "USD/share", False, False),
    (
        "sec_cash_and_equivalents",
        "SEC cash and equivalents",
        "balance_sheet",
        "CashAndCashEquivalentsAtCarryingValue",
        "USD",
        False,
        False,
    ),
    ("sec_assets", "SEC assets", "balance_sheet", "Assets", "USD", False, False),
    ("sec_liabilities", "SEC liabilities", "balance_sheet", "Liabilities", "USD", False, False),
    ("sec_stockholders_equity", "SEC stockholders equity", "balance_sheet", "StockholdersEquity", "USD", False, False),
    ("sec_long_term_debt_current", "SEC current long-term debt", "balance_sheet", "LongTermDebtCurrent", "USD", False, False),
    (
        "sec_long_term_debt_noncurrent",
        "SEC noncurrent long-term debt",
        "balance_sheet",
        "LongTermDebtNoncurrent",
        "USD",
        False,
        False,
    ),
    (
        "sec_operating_cash_flow",
        "SEC operating cash flow",
        "cash_flow",
        "NetCashProvidedByUsedInOperatingActivities",
        "USD",
        False,
        False,
    ),
    (
        "sec_capex",
        "SEC capex",
        "cash_flow",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "USD",
        False,
        False,
    ),
    (
        "sec_dividends_per_share_declared",
        "SEC dividends/share declared",
        "capital_return",
        "CommonStockDividendsPerShareDeclared",
        "USD/share",
        False,
        False,
    ),
    (
        "sec_share_repurchases_value",
        "SEC share repurchases value",
        "capital_return",
        "PaymentsForRepurchaseOfCommonStock",
        "USD",
        False,
        False,
    ),
    (
        "sec_repurchased_retired_shares",
        "SEC repurchased retired shares",
        "capital_return",
        "StockRepurchasedAndRetiredDuringPeriodShares",
        "shares",
        False,
        False,
    ),
    (
        "sec_repurchased_retired_value",
        "SEC repurchased retired value",
        "capital_return",
        "StockRepurchasedAndRetiredDuringPeriodValue",
        "USD",
        False,
        False,
    ),
)


DIRECT_DIMENSIONS: tuple[SecurityDimension, ...] = (
    SecurityDimension("massive_free_float", "Massive free float", "float", "massive", "market_security_float_v1", "shares", default_plot=True, scanner_default=True),
    SecurityDimension("massive_free_float_percent", "Massive free-float percent", "float", "massive", "market_security_float_v1", "percent", scanner_default=True),
    SecurityDimension("massive_float_shares_outstanding", "Massive float shares outstanding", "share_supply", "massive", "market_security_float_v1", "shares", default_plot=True, scanner_default=True),
    SecurityDimension("massive_snapshot_market_cap", "Massive market cap", "market_value", "massive", "market_security_market_snapshot_v1", "USD", scanner_default=True),
    SecurityDimension("massive_share_class_shares_outstanding", "Massive share class shares", "share_supply", "massive", "market_security_market_snapshot_v1", "shares", default_plot=True, scanner_default=True),
    SecurityDimension("massive_weighted_shares_outstanding", "Massive weighted shares", "share_supply", "massive", "market_security_market_snapshot_v1", "shares", default_plot=True),
    SecurityDimension("short_interest", "Short interest", "short_pressure", "massive", "market_short_interest_v1", "shares", scanner_default=True),
    SecurityDimension("short_interest_avg_daily_volume", "Short-interest ADV", "short_pressure", "massive", "market_short_interest_v1", "shares/day"),
    SecurityDimension("short_interest_days_to_cover", "Days to cover", "short_pressure", "massive", "market_short_interest_v1", "days", scanner_default=True),
    SecurityDimension("short_volume", "Short volume", "short_pressure", "finra", "market_short_volume_v1", "shares", scanner_default=True),
    SecurityDimension("short_volume_ratio", "Short-volume ratio", "short_pressure", "finra", "market_short_volume_v1", "ratio", scanner_default=True),
    SecurityDimension("short_exempt_volume", "Short exempt volume", "short_pressure", "finra", "market_short_volume_v1", "shares"),
    SecurityDimension("fails_to_deliver_quantity", "Fails to deliver", "fails_to_deliver", "sec", "market_fails_to_deliver_v1", "shares", scanner_default=True),
    SecurityDimension("fails_to_deliver_prev_close", "FTD previous close", "fails_to_deliver", "sec", "market_fails_to_deliver_v1", "USD"),
    SecurityDimension("reg_sho_threshold_status", "Reg SHO threshold status", "short_pressure", "sec", "market_reg_sho_threshold_v1", "status", value_type="text", scanner_default=True),
    SecurityDimension("borrow_status", "Borrow status", "borrow", "ibkr", "market_security_borrow_v1", "status", value_type="text", scanner_default=True),
    SecurityDimension("borrow_shortable_shares", "Shortable shares", "borrow", "ibkr", "market_security_borrow_v1", "shares", scanner_default=True),
    SecurityDimension("borrow_lender_count", "Borrow lender count", "borrow", "ibkr", "market_security_borrow_v1", "count", scanner_default=True),
    SecurityDimension("borrow_indicative_rate", "Indicative borrow rate", "borrow", "ibkr", "market_security_borrow_v1", "rate", scanner_default=True),
    SecurityDimension("borrow_fee_rate", "Borrow fee rate", "borrow", "ibkr", "market_security_borrow_v1", "rate", scanner_default=True),
    SecurityDimension("split_ratio", "Split ratio", "corporate_action", "massive", "market_stock_split_v1", "ratio", scanner_default=True),
    SecurityDimension("dividend_cash_amount", "Dividend cash amount", "corporate_action", "massive", "market_cash_dividend_v1", "USD/share", scanner_default=True),
    SecurityDimension("ipo_final_issue_price", "IPO final issue price", "corporate_action", "massive", "market_ipo_v1", "USD"),
    SecurityDimension("ipo_total_offer_size", "IPO total offer size", "corporate_action", "massive", "market_ipo_v1", "USD"),
    SecurityDimension("ipo_shares_outstanding", "IPO shares outstanding", "corporate_action", "massive", "market_ipo_v1", "shares"),
    SecurityDimension("sec_filing_size", "SEC filing size", "sec_filing", "sec", "sec_filing_v2", "bytes"),
    SecurityDimension("sec_filing_text_chars", "SEC filing text chars", "sec_filing", "sec", "sec_filing_v2/sec_filing_text_v2", "chars"),
    SecurityDimension("sec_filing_document_count", "SEC filing document count", "sec_filing", "sec", "sec_filing_v2/sec_filing_document_v2", "count"),
    SecurityDimension("listing_country", "Listing country", "classification", "sec/massive", "market_security_country_v1", "country", value_type="text", scanner_default=True),
    SecurityDimension("issuer_legal_country", "Issuer legal country", "classification", "sec/massive", "market_security_country_v1", "country", value_type="text"),
    SecurityDimension("issuer_hq_country", "Issuer HQ country", "classification", "sec/massive", "market_security_country_v1", "country", value_type="text"),
    SecurityDimension("effective_country", "Effective country", "classification", "sec/massive", "market_security_country_v1", "country", value_type="text", scanner_default=True),
    SecurityDimension("country_confidence", "Country confidence", "classification", "sec/massive", "market_security_country_v1", "score"),
    SecurityDimension("classification_value", "Security classification", "classification", "sec/massive", "market_security_classification_v1", "class", value_type="text", scanner_default=True),
    SecurityDimension("news_count", "Benzinga news count", "news", "benzinga", "benzinga_news_ticker_v1", "count", scanner_default=True),
    SecurityDimension("news_provider_delay_ns", "Benzinga provider delay", "news", "benzinga", "benzinga_news_normalized_v1", "ns"),
    SecurityDimension("news_has_external_text", "News has external text", "news", "benzinga", "benzinga_news_normalized_v1", "bool", value_type="bool"),
    SecurityDimension("news_has_pdf", "News has PDF", "news", "benzinga", "benzinga_news_normalized_v1", "bool", value_type="bool"),
    SecurityDimension("news_is_title_only", "News title-only", "news", "benzinga", "benzinga_news_normalized_v1", "bool", value_type="bool"),
    SecurityDimension("tradable_is_tradable", "Reference tradable", "tradability", "reference_gateway", "feature_tradable_universe_v1", "bool", value_type="bool", scanner_default=True),
    SecurityDimension("routing_ibkr_conid", "IBKR conid", "routing", "reference_gateway", "feature_tradable_universe_v1", "conid", value_type="text", scanner_default=True),
)


def dimension_registry() -> dict[str, SecurityDimension]:
    registry = {dimension.code: dimension for dimension in DIRECT_DIMENSIONS}
    for code, label, group, tag, unit, default_plot, scanner_default in SEC_XBRL_DIMENSIONS:
        registry[code] = SecurityDimension(
            code=code,
            label=label,
            group=group,
            source_system="sec",
            source_table="sec_xbrl_company_fact_v1",
            value_unit=unit,
            description=f"SEC XBRL tag {tag}.",
            default_plot=default_plot,
            scanner_default=scanner_default,
        )
    return registry


def default_dimension_codes() -> tuple[str, ...]:
    return tuple(code for code, dimension in dimension_registry().items() if dimension.default_plot)


def all_dimension_codes() -> tuple[str, ...]:
    return tuple(dimension_registry())


def scanner_default_dimension_codes() -> tuple[str, ...]:
    return tuple(code for code, dimension in dimension_registry().items() if dimension.scanner_default)


def dimension_codes_for_groups(groups: Iterable[str]) -> tuple[str, ...]:
    wanted = {group.strip() for group in groups if group.strip()}
    return tuple(code for code, dimension in dimension_registry().items() if dimension.group in wanted)


def resolve_security_dimension_context_sql(*, database: str, ticker: str = "", symbol_id: str = "") -> str:
    filter_sql = _context_filter_sql(ticker=ticker, symbol_id=symbol_id)
    return f"""
SELECT
    upper(u.ticker) AS ticker,
    toString(u.symbol_id) AS symbol_id,
    toString(u.listing_id) AS listing_id,
    toString(u.security_id) AS security_id,
    toString(u.issuer_id) AS issuer_id,
    toString(ifNull(b.bridge_cik, '')) AS cik
FROM
(
    SELECT
        *,
        row_number() OVER (PARTITION BY symbol_id ORDER BY is_tradable DESC, inserted_at DESC) AS rn
    FROM {q(database)}.feature_tradable_universe_v1 FINAL
    WHERE {filter_sql}
) AS u
LEFT JOIN {_sec_bridge_by_symbol_sql(database)} AS b ON b.symbol_id = u.symbol_id
WHERE u.rn = 1
ORDER BY u.is_tradable DESC, u.inserted_at DESC, upper(u.ticker) ASC
LIMIT 1
FORMAT JSONEachRow
""".strip()


def resolve_security_dimension_contexts_sql(
    *,
    database: str,
    tickers: Sequence[str] = (),
    symbol_ids: Sequence[str] = (),
    limit: int = 10000,
) -> str:
    filter_sql = _contexts_filter_sql(tickers=tickers, symbol_ids=symbol_ids)
    partition_key = "upper(ticker)" if tickers and not symbol_ids else "symbol_id"
    return f"""
SELECT
    upper(ticker) AS ticker,
    toString(symbol_id) AS symbol_id,
    toString(listing_id) AS listing_id,
    toString(security_id) AS security_id,
    toString(issuer_id) AS issuer_id,
    toString(cik) AS cik
FROM
(
    SELECT
        *,
        row_number() OVER (
            PARTITION BY {partition_key}
            ORDER BY
                is_tradable DESC,
                (currency_code = 'USD') DESC,
                (upper(product_type) IN ('STK', 'STOCK', 'STOCKS')) DESC,
                inserted_at DESC
        ) AS rn
    FROM {q(database)}.feature_tradable_universe_v1 FINAL
    WHERE {filter_sql}
    LIMIT {int(limit)}
) AS u
LEFT JOIN {_sec_bridge_by_symbol_sql(database)} AS b ON b.symbol_id = u.symbol_id
WHERE u.rn = 1
ORDER BY upper(ticker) ASC
FORMAT JSONEachRow
""".strip()


def security_dimension_observations_sql(
    *,
    database: str,
    ticker: str = "",
    symbol_id: str = "",
    dimension_codes: Sequence[str] = (),
    start_date: str = "2019-01-01",
    end_date: str = "2100-01-01",
) -> str:
    context_cte = _target_context_from_database_cte(database=database, ticker=ticker, symbol_id=symbol_id)
    return _observation_query(
        database=database,
        context_cte=context_cte,
        dimension_codes=dimension_codes,
        start_date=start_date,
        end_date=end_date,
        latest=False,
    )


def security_dimension_observations_sql_for_context(
    *,
    database: str,
    context: SecurityDimensionContext,
    dimension_codes: Sequence[str] = (),
    start_date: str = "2019-01-01",
    end_date: str = "2100-01-01",
) -> str:
    return security_dimension_observations_sql_for_contexts(
        database=database,
        contexts=(context,),
        dimension_codes=dimension_codes,
        start_date=start_date,
        end_date=end_date,
    )


def security_dimension_observations_sql_for_contexts(
    *,
    database: str,
    contexts: Sequence[SecurityDimensionContext],
    dimension_codes: Sequence[str] = (),
    start_date: str = "2019-01-01",
    end_date: str = "2100-01-01",
) -> str:
    context_cte = _literal_context_cte(contexts)
    return _observation_query(
        database=database,
        context_cte=context_cte,
        dimension_codes=dimension_codes,
        start_date=start_date,
        end_date=end_date,
        latest=False,
    )


def security_dimension_observations_sql_for_tickers(
    *,
    database: str,
    tickers: Sequence[str],
    dimension_codes: Sequence[str] = (),
    start_date: str = "2019-01-01",
    end_date: str = "2100-01-01",
    limit: int = 10000,
) -> str:
    context_cte = _target_context_from_database_cte(database=database, tickers=tickers, limit=limit)
    return _observation_query(
        database=database,
        context_cte=context_cte,
        dimension_codes=dimension_codes,
        start_date=start_date,
        end_date=end_date,
        latest=False,
    )


def security_dimension_latest_sql_for_tickers(
    *,
    database: str,
    tickers: Sequence[str],
    dimension_codes: Sequence[str] = (),
    as_of: str = "2100-01-01 00:00:00",
    lookback_start: str = "2019-01-01",
    limit: int = 10000,
) -> str:
    context_cte = _target_context_from_database_cte(database=database, tickers=tickers, limit=limit)
    return _observation_query(
        database=database,
        context_cte=context_cte,
        dimension_codes=dimension_codes,
        start_date=lookback_start,
        end_date=as_of,
        latest=True,
    )


def security_dimension_latest_sql_for_contexts(
    *,
    database: str,
    contexts: Sequence[SecurityDimensionContext],
    dimension_codes: Sequence[str] = (),
    as_of: str = "2100-01-01 00:00:00",
    lookback_start: str = "2019-01-01",
) -> str:
    context_cte = _literal_context_cte(contexts)
    return _observation_query(
        database=database,
        context_cte=context_cte,
        dimension_codes=dimension_codes,
        start_date=lookback_start,
        end_date=as_of,
        latest=True,
    )


def _observation_query(
    *,
    database: str,
    context_cte: str,
    dimension_codes: Sequence[str],
    start_date: str,
    end_date: str,
    latest: bool,
) -> str:
    codes = tuple(dimension_codes) if dimension_codes else default_dimension_codes()
    union_sql = _dimension_union_sql(database=database, dimension_codes=codes)
    if latest:
        select_sql = f"""
WITH target_context AS ({context_cte})
SELECT
    symbol_id,
    ticker,
    dimension_code,
    dimension_label,
    dimension_group,
    latest_observed_at_utc AS observed_at_utc,
    period_end_date,
    value,
    value_text,
    value_bool,
    value_unit,
    source_system,
    source_table,
    source_event_id,
    source_form,
    source_priority
FROM
(
    SELECT
        symbol_id,
        ticker,
        dimension_code,
    argMax(dimension_label, observed_at_utc) AS dimension_label,
    argMax(dimension_group, observed_at_utc) AS dimension_group,
    max(observed_at_utc) AS latest_observed_at_utc,
    argMax(period_end_date, observed_at_utc) AS period_end_date,
    argMax(value, observed_at_utc) AS value,
    argMax(value_text, observed_at_utc) AS value_text,
    argMax(value_bool, observed_at_utc) AS value_bool,
    argMax(value_unit, observed_at_utc) AS value_unit,
    argMax(source_system, observed_at_utc) AS source_system,
    argMax(source_table, observed_at_utc) AS source_table,
    argMax(source_event_id, observed_at_utc) AS source_event_id,
    argMax(source_form, observed_at_utc) AS source_form,
    argMax(source_priority, observed_at_utc) AS source_priority
FROM
(
    SELECT
        dimension_rows.symbol_id AS symbol_id,
        dimension_rows.ticker AS ticker,
        dimension_rows.dimension_code AS dimension_code,
        dimension_rows.dimension_label AS dimension_label,
        dimension_rows.dimension_group AS dimension_group,
        dimension_rows.observed_at_utc AS observed_at_utc,
        dimension_rows.period_end_date AS period_end_date,
        dimension_rows.value AS value,
        dimension_rows.value_text AS value_text,
        dimension_rows.value_bool AS value_bool,
        dimension_rows.value_unit AS value_unit,
        dimension_rows.source_system AS source_system,
        dimension_rows.source_table AS source_table,
        dimension_rows.source_event_id AS source_event_id,
        dimension_rows.source_form AS source_form,
        dimension_rows.source_priority AS source_priority
    FROM ({union_sql}) AS dimension_rows
    WHERE dimension_rows.observed_at_utc >= toDateTime64('{escape(start_date)}', 6)
      AND dimension_rows.observed_at_utc <= toDateTime64('{escape(end_date)}', 6)
) AS dimension_observations
GROUP BY symbol_id, ticker, dimension_code
)
ORDER BY ticker ASC, dimension_code ASC
FORMAT JSONEachRow
""".strip()
    else:
        select_sql = f"""
WITH target_context AS ({context_cte})
SELECT *
FROM ({union_sql})
WHERE observed_at_utc >= toDateTime64('{escape(start_date)}', 6)
  AND observed_at_utc <= toDateTime64('{escape(end_date)}', 6)
ORDER BY ticker ASC, dimension_code ASC, observed_at_utc ASC, source_priority ASC
FORMAT JSONEachRow
""".strip()
    return select_sql


def _dimension_union_sql(*, database: str, dimension_codes: Sequence[str]) -> str:
    wanted = set(dimension_codes)
    builders: list[str] = []
    for code, _label, _group, tag, unit, _default_plot, _scanner_default in SEC_XBRL_DIMENSIONS:
        if code in wanted:
            builders.append(_sec_xbrl_dimension_sql(database, code=code, tag=tag, unit=unit))
    direct_builders = {
        "massive_free_float": lambda: _float_sql(database, "massive_free_float", "free_float", "effective_date", "shares"),
        "massive_free_float_percent": lambda: _float_sql(database, "massive_free_float_percent", "free_float_percent", "effective_date", "percent"),
        "massive_float_shares_outstanding": lambda: _float_sql(database, "massive_float_shares_outstanding", "shares_outstanding", "effective_date", "shares"),
        "massive_snapshot_market_cap": lambda: _snapshot_sql(database, "massive_snapshot_market_cap", "market_cap", "USD"),
        "massive_share_class_shares_outstanding": lambda: _snapshot_sql(database, "massive_share_class_shares_outstanding", "share_class_shares_outstanding", "shares"),
        "massive_weighted_shares_outstanding": lambda: _snapshot_sql(database, "massive_weighted_shares_outstanding", "weighted_shares_outstanding", "shares"),
        "short_interest": lambda: _short_interest_sql(database, "short_interest", "short_interest", "settlement_date", "shares"),
        "short_interest_avg_daily_volume": lambda: _short_interest_sql(database, "short_interest_avg_daily_volume", "avg_daily_volume", "settlement_date", "shares/day"),
        "short_interest_days_to_cover": lambda: _short_interest_sql(database, "short_interest_days_to_cover", "days_to_cover", "settlement_date", "days"),
        "short_volume": lambda: _short_volume_sql(database, "short_volume", "short_volume", "trade_date", "shares"),
        "short_volume_ratio": lambda: _short_volume_sql(database, "short_volume_ratio", "short_volume_ratio", "trade_date", "ratio"),
        "short_exempt_volume": lambda: _short_volume_sql(database, "short_exempt_volume", "exempt_volume", "trade_date", "shares"),
        "fails_to_deliver_quantity": lambda: _fails_sql(database, "fails_to_deliver_quantity", "fails_quantity", "settlement_date", "shares"),
        "fails_to_deliver_prev_close": lambda: _fails_sql(database, "fails_to_deliver_prev_close", "previous_close_price", "settlement_date", "USD"),
        "reg_sho_threshold_status": lambda: _reg_sho_sql(database),
        "borrow_status": lambda: _borrow_text_sql(database, "borrow_status", "borrow_status"),
        "borrow_shortable_shares": lambda: _borrow_numeric_sql(database, "borrow_shortable_shares", "shortable_shares", "shares"),
        "borrow_lender_count": lambda: _borrow_numeric_sql(database, "borrow_lender_count", "lender_count", "count"),
        "borrow_indicative_rate": lambda: _borrow_numeric_sql(database, "borrow_indicative_rate", "indicative_borrow_rate", "rate"),
        "borrow_fee_rate": lambda: _borrow_numeric_sql(database, "borrow_fee_rate", "fee_rate", "rate"),
        "split_ratio": lambda: _split_sql(database),
        "dividend_cash_amount": lambda: _dividend_sql(database),
        "ipo_final_issue_price": lambda: _ipo_sql(database, "ipo_final_issue_price", "final_issue_price", "USD"),
        "ipo_total_offer_size": lambda: _ipo_sql(database, "ipo_total_offer_size", "total_offer_size", "USD"),
        "ipo_shares_outstanding": lambda: _ipo_sql(database, "ipo_shares_outstanding", "shares_outstanding", "shares"),
        "sec_filing_size": lambda: _sec_filing_numeric_sql(database, "sec_filing_size", "filing_size", "bytes"),
        "sec_filing_text_chars": lambda: _sec_filing_text_chars_sql(database),
        "sec_filing_document_count": lambda: _sec_filing_document_count_sql(database),
        "listing_country": lambda: _country_text_sql(database, "listing_country", "listing_country_code"),
        "issuer_legal_country": lambda: _country_text_sql(database, "issuer_legal_country", "issuer_legal_country_code"),
        "issuer_hq_country": lambda: _country_text_sql(database, "issuer_hq_country", "issuer_hq_country_code"),
        "effective_country": lambda: _country_text_sql(database, "effective_country", "effective_country_code"),
        "country_confidence": lambda: _country_numeric_sql(database),
        "classification_value": lambda: _classification_sql(database),
        "news_count": lambda: _news_count_sql(database),
        "news_provider_delay_ns": lambda: _news_normalized_numeric_sql(database, "news_provider_delay_ns", "provider_delay_ns", "ns"),
        "news_has_external_text": lambda: _news_normalized_bool_sql(database, "news_has_external_text", "has_external_text"),
        "news_has_pdf": lambda: _news_normalized_bool_sql(database, "news_has_pdf", "has_pdf"),
        "news_is_title_only": lambda: _news_normalized_bool_sql(database, "news_is_title_only", "is_title_only"),
        "tradable_is_tradable": lambda: _tradability_bool_sql(database),
        "routing_ibkr_conid": lambda: _routing_text_sql(database),
    }
    for code, builder in direct_builders.items():
        if code in wanted:
            builders.append(builder())
    if not builders:
        known = ", ".join(sorted(dimension_registry()))
        raise ValueError(f"No known dimension codes requested. Known codes: {known}")
    return "\nUNION ALL\n".join(builders)


def _base_numeric_select(
    *,
    code: str,
    value_expr: str,
    unit: str,
    observed_expr: str,
    period_expr: str = "toDate(NULL)",
    source_event_expr: str = "''",
    source_form_expr: str = "''",
    source_priority: int = 100,
) -> str:
    dimension = dimension_registry()[code]
    return f"""
SELECT
    ctx.symbol_id AS symbol_id,
    ctx.ticker AS ticker,
    '{code}' AS dimension_code,
    '{escape(dimension.label)}' AS dimension_label,
    '{dimension.group}' AS dimension_group,
    toDateTime64({observed_expr}, 6) AS observed_at_utc,
    {period_expr} AS period_end_date,
    toNullable(toFloat64({value_expr})) AS value,
    '' AS value_text,
    CAST(NULL, 'Nullable(UInt8)') AS value_bool,
    '{unit}' AS value_unit,
    '{dimension.source_system}' AS source_system,
    '{dimension.source_table}' AS source_table,
    toString({source_event_expr}) AS source_event_id,
    toString({source_form_expr}) AS source_form,
    toUInt16({source_priority}) AS source_priority
"""


def _base_text_select(
    *,
    code: str,
    value_expr: str,
    observed_expr: str,
    period_expr: str = "toDate(NULL)",
    source_event_expr: str = "''",
    source_form_expr: str = "''",
    source_priority: int = 100,
) -> str:
    dimension = dimension_registry()[code]
    return f"""
SELECT
    ctx.symbol_id AS symbol_id,
    ctx.ticker AS ticker,
    '{code}' AS dimension_code,
    '{escape(dimension.label)}' AS dimension_label,
    '{dimension.group}' AS dimension_group,
    toDateTime64({observed_expr}, 6) AS observed_at_utc,
    {period_expr} AS period_end_date,
    CAST(NULL, 'Nullable(Float64)') AS value,
    toString({value_expr}) AS value_text,
    CAST(NULL, 'Nullable(UInt8)') AS value_bool,
    '{dimension.value_unit}' AS value_unit,
    '{dimension.source_system}' AS source_system,
    '{dimension.source_table}' AS source_table,
    toString({source_event_expr}) AS source_event_id,
    toString({source_form_expr}) AS source_form,
    toUInt16({source_priority}) AS source_priority
"""


def _base_bool_select(
    *,
    code: str,
    value_expr: str,
    observed_expr: str,
    period_expr: str = "toDate(NULL)",
    source_event_expr: str = "''",
    source_form_expr: str = "''",
    source_priority: int = 100,
) -> str:
    dimension = dimension_registry()[code]
    return f"""
SELECT
    ctx.symbol_id AS symbol_id,
    ctx.ticker AS ticker,
    '{code}' AS dimension_code,
    '{escape(dimension.label)}' AS dimension_label,
    '{dimension.group}' AS dimension_group,
    toDateTime64({observed_expr}, 6) AS observed_at_utc,
    {period_expr} AS period_end_date,
    CAST(NULL, 'Nullable(Float64)') AS value,
    '' AS value_text,
    toNullable(toUInt8({value_expr})) AS value_bool,
    '{dimension.value_unit}' AS value_unit,
    '{dimension.source_system}' AS source_system,
    '{dimension.source_table}' AS source_table,
    toString({source_event_expr}) AS source_event_id,
    toString({source_form_expr}) AS source_form,
    toUInt16({source_priority}) AS source_priority
"""


def _sec_xbrl_dimension_sql(database: str, *, code: str, tag: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr="f.value",
        unit=unit,
        observed_expr="coalesce(f.filed_at_utc, f.recorded_at_utc)",
        period_expr="f.period_end_date",
        source_event_expr="f.accession_number",
        source_form_expr="f.form_type",
        source_priority=10,
    )
    return f"""
{select}
FROM {q(database)}.sec_xbrl_company_fact_v1 AS f FINAL
INNER JOIN target_context AS ctx ON ctx.cik = f.cik
WHERE f.tag = '{escape(tag)}'
  AND lower(f.unit_code) = lower('{escape(unit)}')
  AND isFinite(f.value)
""".strip()


def _float_sql(database: str, code: str, column: str, date_column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr=f"toDateTime64(m.{date_column}, 6)",
        period_expr=f"m.{date_column}",
        source_event_expr="m.source_event_key",
        source_priority=20,
    )
    return f"""
{select}
FROM {q(database)}.market_security_float_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _snapshot_sql(database: str, code: str, column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr="m.observed_at_utc",
        period_expr="toDate(m.observed_at_utc)",
        source_event_expr="m.security_market_snapshot_id",
        source_priority=21,
    )
    return f"""
{select}
FROM {q(database)}.market_security_market_snapshot_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _short_interest_sql(database: str, code: str, column: str, date_column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr=f"coalesce(m.published_at_utc, toDateTime64(m.{date_column}, 6))",
        period_expr=f"m.{date_column}",
        source_event_expr="m.source_event_key",
        source_priority=30,
    )
    return f"""
{select}
FROM {q(database)}.market_short_interest_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _short_volume_sql(database: str, code: str, column: str, date_column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr=f"coalesce(m.published_at_utc, toDateTime64(m.{date_column}, 6))",
        period_expr=f"m.{date_column}",
        source_event_expr="m.source_event_key",
        source_priority=31,
    )
    return f"""
{select}
FROM {q(database)}.market_short_volume_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _fails_sql(database: str, code: str, column: str, date_column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr=f"toDateTime64(m.{date_column}, 6)",
        period_expr=f"m.{date_column}",
        source_event_expr="m.source_event_key",
        source_priority=32,
    )
    return f"""
{select}
FROM {q(database)}.market_fails_to_deliver_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _reg_sho_sql(database: str) -> str:
    select = _base_text_select(
        code="reg_sho_threshold_status",
        value_expr="m.threshold_status",
        observed_expr="toDateTime64(m.threshold_date, 6)",
        period_expr="m.threshold_date",
        source_event_expr="m.source_event_key",
        source_priority=33,
    )
    return f"""
{select}
FROM {q(database)}.market_reg_sho_threshold_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE m.threshold_status != ''
""".strip()


def _borrow_numeric_sql(database: str, code: str, column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr="m.observed_at_utc",
        period_expr="toDate(m.observed_at_utc)",
        source_event_expr="m.source_event_key",
        source_priority=40,
    )
    return f"""
{select}
FROM {q(database)}.market_security_borrow_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _borrow_text_sql(database: str, code: str, column: str) -> str:
    select = _base_text_select(
        code=code,
        value_expr=f"m.{column}",
        observed_expr="m.observed_at_utc",
        period_expr="toDate(m.observed_at_utc)",
        source_event_expr="m.source_event_key",
        source_priority=40,
    )
    return f"""
{select}
FROM {q(database)}.market_security_borrow_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE m.{column} != ''
""".strip()


def _split_sql(database: str) -> str:
    select = _base_numeric_select(
        code="split_ratio",
        value_expr="toFloat64(m.split_to) / nullIf(toFloat64(m.split_from), 0)",
        unit="ratio",
        observed_expr="toDateTime64(m.execution_date, 6)",
        period_expr="m.execution_date",
        source_event_expr="m.source_event_key",
        source_priority=50,
    )
    return f"""
{select}
FROM {q(database)}.market_stock_split_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.split_from) AND isNotNull(m.split_to) AND m.split_from != 0
""".strip()


def _dividend_sql(database: str) -> str:
    select = _base_numeric_select(
        code="dividend_cash_amount",
        value_expr="m.cash_amount",
        unit="USD/share",
        observed_expr="toDateTime64(m.ex_dividend_date, 6)",
        period_expr="m.ex_dividend_date",
        source_event_expr="m.source_event_key",
        source_form_expr="m.dividend_type",
        source_priority=51,
    )
    return f"""
{select}
FROM {q(database)}.market_cash_dividend_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.cash_amount)
""".strip()


def _ipo_sql(database: str, code: str, column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"m.{column}",
        unit=unit,
        observed_expr="toDateTime64(m.listing_date, 6)",
        period_expr="m.listing_date",
        source_event_expr="m.source_event_key",
        source_form_expr="m.ipo_status",
        source_priority=52,
    )
    return f"""
{select}
FROM {q(database)}.market_ipo_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.{column})
""".strip()


def _sec_filing_numeric_sql(database: str, code: str, column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"f.{column}",
        unit=unit,
        observed_expr="coalesce(f.accepted_at_utc, toDateTime64(f.filing_date, 6))",
        period_expr="coalesce(f.report_date, f.filing_date)",
        source_event_expr="f.accession_number",
        source_form_expr="f.form_type",
        source_priority=60,
    )
    return f"""
{select}
FROM {q(database)}.sec_filing_v2 AS f FINAL
INNER JOIN target_context AS ctx ON ctx.cik = f.cik
WHERE isNotNull(f.{column})
""".strip()


def _sec_filing_text_chars_sql(database: str) -> str:
    select = _base_numeric_select(
        code="sec_filing_text_chars",
        value_expr="t.text_char_count",
        unit="chars",
        observed_expr="coalesce(f.accepted_at_utc, toDateTime64(f.filing_date, 6))",
        period_expr="coalesce(f.report_date, f.filing_date)",
        source_event_expr="f.accession_number",
        source_form_expr="f.form_type",
        source_priority=61,
    )
    return f"""
{select}
FROM {q(database)}.sec_filing_v2 AS f FINAL
INNER JOIN target_context AS ctx ON ctx.cik = f.cik
INNER JOIN
(
    SELECT accession_number, sum(text_char_count) AS text_char_count
    FROM {q(database)}.sec_filing_text_v2 FINAL
    GROUP BY accession_number
) AS t ON t.accession_number = f.accession_number
WHERE isNotNull(t.text_char_count)
""".strip()


def _sec_filing_document_count_sql(database: str) -> str:
    select = _base_numeric_select(
        code="sec_filing_document_count",
        value_expr="d.document_count",
        unit="count",
        observed_expr="coalesce(f.accepted_at_utc, toDateTime64(f.filing_date, 6))",
        period_expr="coalesce(f.report_date, f.filing_date)",
        source_event_expr="f.accession_number",
        source_form_expr="f.form_type",
        source_priority=62,
    )
    return f"""
{select}
FROM {q(database)}.sec_filing_v2 AS f FINAL
INNER JOIN target_context AS ctx ON ctx.cik = f.cik
INNER JOIN
(
    SELECT accession_number, count() AS document_count
    FROM {q(database)}.sec_filing_document_v2 FINAL
    GROUP BY accession_number
) AS d ON d.accession_number = f.accession_number
WHERE isNotNull(d.document_count)
""".strip()


def _country_text_sql(database: str, code: str, column: str) -> str:
    select = _base_text_select(
        code=code,
        value_expr=f"m.{column}",
        observed_expr="toDateTime64(m.assertion_date, 6)",
        period_expr="m.assertion_date",
        source_event_expr="m.source_event_key",
        source_priority=70,
    )
    return f"""
{select}
FROM {q(database)}.market_security_country_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE m.{column} != ''
""".strip()


def _country_numeric_sql(database: str) -> str:
    select = _base_numeric_select(
        code="country_confidence",
        value_expr="m.confidence_score",
        unit="score",
        observed_expr="toDateTime64(m.assertion_date, 6)",
        period_expr="m.assertion_date",
        source_event_expr="m.source_event_key",
        source_priority=70,
    )
    return f"""
{select}
FROM {q(database)}.market_security_country_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE isNotNull(m.confidence_score)
""".strip()


def _classification_sql(database: str) -> str:
    select = _base_text_select(
        code="classification_value",
        value_expr="concat(m.classification_scheme, ':', m.classification_level, ':', m.classification_value)",
        observed_expr="m.last_seen_at_utc",
        period_expr="toDate(m.last_seen_at_utc)",
        source_event_expr="m.source_entity_key",
        source_priority=71,
    )
    return f"""
{select}
FROM {q(database)}.market_security_classification_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.security_id = m.security_id
WHERE m.classification_value != ''
""".strip()


def _news_count_sql(database: str) -> str:
    select = _base_numeric_select(
        code="news_count",
        value_expr="n.news_count",
        unit="count",
        observed_expr="n.news_date",
        period_expr="toDate(n.news_date)",
        source_event_expr="n.news_date",
        source_priority=80,
    )
    return f"""
{select}
FROM
(
    SELECT
        ticker,
        toStartOfDay(published_at_utc) AS news_date,
        count() AS news_count
    FROM {q(database)}.benzinga_news_ticker_v1 FINAL
    GROUP BY ticker, news_date
) AS n
INNER JOIN target_context AS ctx ON ctx.ticker = upper(n.ticker)
""".strip()


def _news_normalized_numeric_sql(database: str, code: str, column: str, unit: str) -> str:
    select = _base_numeric_select(
        code=code,
        value_expr=f"n.{column}",
        unit=unit,
        observed_expr="n.published_at_utc",
        period_expr="toDate(n.published_at_utc)",
        source_event_expr="n.canonical_news_id",
        source_priority=81,
    )
    return f"""
{select}
FROM {q(database)}.benzinga_news_normalized_v1 AS n FINAL
ARRAY JOIN n.tickers AS news_ticker
INNER JOIN target_context AS ctx ON ctx.ticker = upper(news_ticker)
WHERE isNotNull(n.{column})
""".strip()


def _news_normalized_bool_sql(database: str, code: str, column: str) -> str:
    select = _base_bool_select(
        code=code,
        value_expr=f"n.{column}",
        observed_expr="n.published_at_utc",
        period_expr="toDate(n.published_at_utc)",
        source_event_expr="n.canonical_news_id",
        source_priority=82,
    )
    return f"""
{select}
FROM {q(database)}.benzinga_news_normalized_v1 AS n FINAL
ARRAY JOIN n.tickers AS news_ticker
INNER JOIN target_context AS ctx ON ctx.ticker = upper(news_ticker)
""".strip()


def _tradability_bool_sql(database: str) -> str:
    select = _base_bool_select(
        code="tradable_is_tradable",
        value_expr="m.is_tradable",
        observed_expr="m.inserted_at",
        period_expr="m.universe_date",
        source_event_expr="ifNull(m.exclusion_reason, '')",
        source_priority=5,
    )
    return f"""
{select}
FROM {q(database)}.feature_tradable_universe_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.symbol_id = m.symbol_id
""".strip()


def _routing_text_sql(database: str) -> str:
    select = _base_text_select(
        code="routing_ibkr_conid",
        value_expr="ifNull(m.ibkr_conid, '')",
        observed_expr="m.inserted_at",
        period_expr="m.universe_date",
        source_event_expr="m.source_run_id",
        source_priority=6,
    )
    return f"""
{select}
FROM {q(database)}.feature_tradable_universe_v1 AS m FINAL
INNER JOIN target_context AS ctx ON ctx.symbol_id = m.symbol_id
WHERE isNotNull(m.ibkr_conid) AND m.ibkr_conid != ''
""".strip()


def _literal_context_cte(contexts: Sequence[SecurityDimensionContext]) -> str:
    if not contexts:
        raise ValueError("At least one security dimension context is required.")
    rows = []
    for context in contexts:
        rows.append(
            "SELECT "
            f"upper('{escape(context.ticker)}') AS ticker, "
            f"toString('{escape(context.symbol_id)}') AS symbol_id, "
            f"toString('{escape(context.listing_id)}') AS listing_id, "
            f"toString('{escape(context.security_id)}') AS security_id, "
            f"toString('{escape(context.issuer_id)}') AS issuer_id, "
            f"toString('{escape(context.cik)}') AS cik"
        )
    return "\nUNION ALL\n".join(rows)


def _target_context_from_database_cte(
    *,
    database: str,
    ticker: str = "",
    symbol_id: str = "",
    tickers: Sequence[str] = (),
    limit: int = 10000,
) -> str:
    if tickers:
        filter_sql = _contexts_filter_sql(tickers=tickers, symbol_ids=())
        partition_key = "upper(ticker)"
    else:
        filter_sql = _context_filter_sql(ticker=ticker, symbol_id=symbol_id)
        partition_key = "symbol_id"
    return f"""
SELECT
    upper(u.ticker) AS ticker,
    toString(u.symbol_id) AS symbol_id,
    toString(u.listing_id) AS listing_id,
    toString(u.security_id) AS security_id,
    toString(u.issuer_id) AS issuer_id,
    toString(ifNull(b.bridge_cik, '')) AS cik
FROM
(
    SELECT
        *,
        row_number() OVER (
            PARTITION BY {partition_key}
            ORDER BY
                is_tradable DESC,
                (currency_code = 'USD') DESC,
                (upper(product_type) IN ('STK', 'STOCK', 'STOCKS')) DESC,
                inserted_at DESC
        ) AS rn
    FROM {q(database)}.feature_tradable_universe_v1 FINAL
    WHERE {filter_sql}
) AS u
LEFT JOIN {_sec_bridge_by_symbol_sql(database)} AS b ON b.symbol_id = u.symbol_id
WHERE u.rn = 1
LIMIT {int(limit)}
""".strip()


def _context_filter_sql(*, ticker: str, symbol_id: str) -> str:
    if symbol_id:
        return f"symbol_id = '{escape(symbol_id)}'"
    if ticker:
        return f"upper(ticker) = upper('{escape(ticker)}')"
    raise ValueError("ticker or symbol_id is required.")


def _sec_bridge_by_symbol_sql(database: str) -> str:
    return f"""
(
    SELECT
        toString(symbol_id) AS symbol_id,
        argMax(cik, last_seen_at_utc) AS bridge_cik
    FROM {q(database)}.id_sec_market_bridge_v1 FINAL
    WHERE isNotNull(symbol_id)
      AND symbol_id != ''
      AND cik != ''
      AND mapping_status IN ('active', 'current', 'mapped', 'ok', 'resolved')
    GROUP BY symbol_id
)
""".strip()


def _contexts_filter_sql(*, tickers: Sequence[str], symbol_ids: Sequence[str]) -> str:
    filters = []
    if tickers:
        filters.append(f"upper(ticker) IN ({literal_list([ticker.upper() for ticker in tickers])})")
    if symbol_ids:
        filters.append(f"symbol_id IN ({literal_list(symbol_ids)})")
    if not filters:
        raise ValueError("At least one ticker or symbol_id is required.")
    return " OR ".join(filters)


def literal_list(values: Iterable[str]) -> str:
    items = [f"'{escape(value)}'" for value in values]
    if not items:
        raise ValueError("literal_list requires at least one value.")
    return ", ".join(items)


def q(identifier: str) -> str:
    if not identifier or not all(part.replace("_", "").isalnum() for part in identifier.split(".")):
        raise ValueError(f"Unsafe ClickHouse identifier: {identifier!r}")
    return ".".join(f"`{part}`" for part in identifier.split("."))


def escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")
