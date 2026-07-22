from __future__ import annotations

import json
import logging
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from typing import Any, Callable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)


TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
DATABASE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNDAMENTAL_TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue", "RevenueFromContractsWithCustomers")),
    ("Gross profit", ("GrossProfit",)),
    ("Operating income", ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities")),
    ("Net income", ("NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent")),
    ("Diluted EPS", ("EarningsPerShareDiluted", "DilutedEarningsLossPerShare", "BasicAndDilutedEarningsLossPerShare")),
    ("Operating cash flow", ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperations")),
    ("Capital expenditure", ("PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities")),
    ("Cash", ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents")),
    ("Current assets", ("AssetsCurrent", "CurrentAssets")),
    ("Current liabilities", ("LiabilitiesCurrent", "CurrentLiabilities")),
    ("Accounts receivable", ("AccountsReceivableNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent", "TradeAndOtherCurrentReceivables", "CurrentTradeReceivables", "TradeReceivables", "CurrentReceivablesFromContractsWithCustomers")),
    ("Accounts payable", ("AccountsPayableCurrent", "AccountsPayableAndOtherAccruedLiabilitiesCurrent", "TradeAndOtherCurrentPayables", "TradeAndOtherCurrentPayablesToTradeSuppliers", "OtherCurrentPayables")),
    ("Inventory", ("InventoryNet", "Inventories", "InventoriesAtNetRealisableValue")),
    ("Assets", ("Assets",)),
    ("Liabilities", ("Liabilities", "LiabilitiesCurrent")),
    ("Stockholders' equity", ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity")),
    ("Long-term debt", ("LongTermDebtNoncurrent", "LongtermBorrowings", "NoncurrentPortionOfOtherNoncurrentBorrowings")),
    ("Current debt", ("LongTermDebtCurrent", "ShorttermBorrowings", "CurrentPortionOfLongTermDebt")),
    ("Research & development", ("ResearchAndDevelopmentExpense",)),
    ("SG&A expense", ("SellingGeneralAndAdministrativeExpense",)),
    ("Stock-based compensation", ("ShareBasedCompensation", "ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsGrantsInPeriodTotal", "AdjustmentsForSharebasedPayments", "IncreaseDecreaseThroughSharebasedPaymentTransactions")),
    ("Interest expense", ("InterestExpenseNonOperating", "InterestAndDebtExpense", "InterestExpense", "FinanceCosts", "InterestExpenseOnDebtInstrumentsIssued")),
    ("Income tax expense", ("IncomeTaxExpenseBenefit", "IncomeTaxExpenseContinuingOperations", "CurrentTaxExpenseIncome")),
    ("Effective tax rate", ("EffectiveIncomeTaxRateContinuingOperations", "AverageEffectiveTaxRate", "ApplicableTaxRate")),
    ("Goodwill", ("Goodwill", "GoodwillAndIntangibleAssetsNet")),
    ("Intangible assets", ("FiniteLivedIntangibleAssetsNet", "IndefiniteLivedIntangibleAssetsExcludingGoodwill", "IntangibleAssetsNetExcludingGoodwill", "IntangibleAssetsAndGoodwill")),
    ("Deferred revenue", ("ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent", "ContractWithCustomerLiabilityNoncurrent", "DeferredIncomeIncludingContractLiabilities", "CurrentDeferredIncomeOtherThanCurrentContractLiabilities")),
    ("Debt issued", ("ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromIssuanceOfSeniorLongTermDebt", "ProceedsFromBorrowingsClassifiedAsFinancingActivities", "ProceedsFromNoncurrentBorrowings")),
    ("Debt repaid", ("RepaymentsOfLongTermDebt", "RepaymentsOfDebt", "RepaymentsOfBondsNotesAndDebentures", "RepaymentsOfNoncurrentBorrowings")),
    ("Common-stock issuance", ("ProceedsFromIssuanceOfCommonStock", "ProceedsFromIssuanceOrSaleOfEquity", "ProceedsFromStockOptionsExercised", "ProceedsFromIssuingShares", "IssueOfEquity")),
    ("Common shares outstanding", ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")),
    ("Weighted average basic shares", ("WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageShares")),
    ("Weighted average diluted shares", ("WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares")),
    ("SEC public float value", ("EntityPublicFloat",)),
    ("Dividends per share", ("CommonStockDividendsPerShareDeclared",)),
    ("Share repurchases", ("PaymentsForRepurchaseOfCommonStock",)),
    ("Repurchased shares", ("StockRepurchasedAndRetiredDuringPeriodShares",)),
)
FUNDAMENTAL_DESCRIPTIONS: dict[str, str] = {
    "Revenue": "Top-line sales for the reported fiscal duration.",
    "Gross profit": "Revenue less direct cost of products or services.",
    "Operating income": "Income from core operations before financing and most non-operating items.",
    "Net income": "Profit or loss after operating costs, financing, taxes, and other reported items.",
    "Diluted EPS": "Earnings per diluted weighted-average share for the reported period.",
    "Operating cash flow": "Cash generated or consumed by operating activities.",
    "Capital expenditure": "Cash invested in property, plant, and equipment.",
    "Cash": "Cash and cash equivalents at the balance-sheet date.",
    "Current assets": "Assets expected to be realized or consumed within the operating cycle.",
    "Current liabilities": "Obligations expected to be settled within the operating cycle.",
    "Accounts receivable": "Customer and trade amounts owed to the issuer.",
    "Accounts payable": "Trade and supplier amounts owed by the issuer.",
    "Inventory": "Reported inventory carried at the balance-sheet date.",
    "Assets": "Total reported resources on the balance sheet.",
    "Liabilities": "Total reported obligations on the balance sheet.",
    "Stockholders' equity": "Residual shareholder interest after liabilities.",
    "Long-term debt": "Interest-bearing borrowings due beyond the current reporting horizon.",
    "Current debt": "Debt and long-term borrowings due within the current reporting horizon.",
    "Research & development": "Reported spending on research and product development.",
    "SG&A expense": "Selling, general, and administrative operating expense.",
    "Stock-based compensation": "Reported equity compensation or the closest standardized cash-flow adjustment.",
    "Interest expense": "Financing cost recognized for debt and other financial liabilities.",
    "Income tax expense": "Current and deferred income-tax expense for the period.",
    "Effective tax rate": "Reported effective or applicable income-tax rate.",
    "Goodwill": "Acquisition goodwill carried on the balance sheet.",
    "Intangible assets": "Reported non-physical assets; the exact tag shows whether goodwill is included.",
    "Deferred revenue": "Customer consideration received before the related revenue is recognized.",
    "Debt issued": "Cash proceeds from new borrowings during the period.",
    "Debt repaid": "Cash used to repay borrowings during the period.",
    "Common-stock issuance": "Cash or equity value associated with common-share issuance.",
    "Common shares outstanding": "Common shares legally outstanding at the reported instant.",
    "Weighted average basic shares": "Time-weighted basic shares used for basic EPS.",
    "Weighted average diluted shares": "Time-weighted shares after dilutive instruments when applicable.",
    "SEC public float value": "SEC-reported market value held by non-affiliates; this is currency, not shares.",
    "Dividends per share": "Cash dividends declared per common share for the reported period.",
    "Share repurchases": "Cash spent repurchasing common stock during the period.",
    "Repurchased shares": "Shares repurchased and retired during the period.",
}
FUNDAMENTAL_CLASS_BY_LABEL: dict[str, str] = {
    "Revenue": "Income statement", "Gross profit": "Income statement", "Operating income": "Income statement",
    "Net income": "Income statement", "Diluted EPS": "Income statement",
    "Operating cash flow": "Cash flow", "Capital expenditure": "Cash flow", "Debt issued": "Cash flow",
    "Debt repaid": "Cash flow", "Common-stock issuance": "Cash flow", "Share repurchases": "Cash flow",
    "Cash": "Balance sheet", "Current assets": "Balance sheet", "Current liabilities": "Balance sheet",
    "Accounts receivable": "Balance sheet", "Accounts payable": "Balance sheet", "Inventory": "Balance sheet",
    "Assets": "Balance sheet", "Liabilities": "Balance sheet", "Stockholders' equity": "Balance sheet",
    "Long-term debt": "Balance sheet", "Current debt": "Balance sheet", "Goodwill": "Balance sheet",
    "Intangible assets": "Balance sheet", "Deferred revenue": "Balance sheet",
    "Research & development": "Operating investment", "SG&A expense": "Operating investment",
    "Stock-based compensation": "Capital & dilution", "Common shares outstanding": "Capital & dilution",
    "Weighted average basic shares": "Capital & dilution", "Weighted average diluted shares": "Capital & dilution",
    "SEC public float value": "Capital & dilution", "Dividends per share": "Capital & dilution",
    "Repurchased shares": "Capital & dilution", "Interest expense": "Tax & financing",
    "Income tax expense": "Tax & financing", "Effective tax rate": "Tax & financing",
}
FUNDAMENTAL_CHANGE_DIRECTION: dict[str, str] = {
    "Revenue": "higher_is_stronger", "Gross profit": "higher_is_stronger", "Operating income": "higher_is_stronger",
    "Net income": "higher_is_stronger", "Diluted EPS": "higher_is_stronger", "Operating cash flow": "higher_is_stronger",
    "Cash": "higher_is_stronger", "Current assets": "higher_is_stronger", "Stockholders' equity": "higher_is_stronger",
    "Accounts receivable": "contextual", "Inventory": "contextual", "Assets": "contextual", "Deferred revenue": "contextual",
    "Capital expenditure": "contextual", "Research & development": "contextual", "Goodwill": "contextual",
    "Intangible assets": "contextual", "Effective tax rate": "contextual", "Dividends per share": "contextual",
    "Current liabilities": "lower_is_stronger", "Liabilities": "lower_is_stronger", "Long-term debt": "lower_is_stronger",
    "Current debt": "lower_is_stronger", "Interest expense": "lower_is_stronger", "SG&A expense": "lower_is_stronger",
    "Stock-based compensation": "lower_is_stronger", "Common-stock issuance": "lower_is_stronger",
    "Weighted average basic shares": "lower_is_stronger", "Weighted average diluted shares": "lower_is_stronger",
}
SEC_INCORPORATION_COUNTRY_CODES = {
    # SEC EDGAR jurisdiction code used by foreign private issuers. Keep this
    # separate from ISO country codes and from the exchange/listing country.
    "L2": "IE",
}
LOGGER = logging.getLogger(__name__)
HISTORY_LIMIT = 10_000
MAIN_HISTORY_DAYS = 520
US_INCORPORATION_CODES = frozenset({
    "AK", "AL", "AR", "AS", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "GU", "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MP", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VI", "VT", "WA", "WI", "WV", "WY",
})


def ticker_facts_payload(symbol: str, *, as_of: str | None = None, database: str = "q_live") -> dict[str, Any]:
    """Return the point-in-time snapshot plus prior-publication comparisons."""
    ticker = normalize_ticker(symbol)
    cutoff = parse_as_of(as_of)
    if not DATABASE_PATTERN.fullmatch(database):
        raise ValueError("Ticker facts database is not a valid identifier.")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    try:
        anchor_rows = clickhouse_rows(client, identity_anchor_sql(ticker, cutoff, database))
    except Exception as error:
        raise RuntimeError("Canonical stock-reference storage is unavailable.") from error
    if not anchor_rows:
        return {
            "as_of": cutoff.isoformat(),
            "errors": {},
            "facts": {},
            "fundamentals": [],
            "identifiers": [],
            "sources": [],
            "status": "not_found",
            "symbol": ticker,
            "warnings": ["No canonical US stock listing was available for this ticker at the selected clock."],
        }
    anchor = anchor_rows[0]
    context = {
        "cik": issuer_cik(str(anchor.get("issuer_id") or "")),
        "issuer_id": str(anchor.get("issuer_id") or ""),
        "listing_id": str(anchor.get("listing_id") or ""),
        "security_id": str(anchor.get("security_id") or ""),
        "symbol_id": str(anchor.get("symbol_id") or ""),
    }
    queries: dict[str, str] = {
        "borrow": borrow_sql(ticker, cutoff, database),
        "classifications": classifications_sql(context["security_id"], cutoff, database),
        "corporate": corporate_events_sql(context["symbol_id"], cutoff, database),
        "fails_to_deliver": fails_to_deliver_sql(ticker, cutoff, database),
        "float": float_sql(context["symbol_id"], cutoff, database),
        "identifiers": identifiers_sql(context["issuer_id"], context["security_id"], cutoff, database),
        "market": market_snapshot_sql(context["symbol_id"], cutoff, database),
        "reg_sho": reg_sho_sql(ticker, cutoff, database),
        "short_interest": short_interest_sql(context["symbol_id"], cutoff, database),
        "short_volume": short_volume_sql(context["symbol_id"], cutoff, database),
        "splits": splits_sql(context["symbol_id"], cutoff, database),
        "volume": volume_sql(ticker, cutoff, historical_database()),
    }
    if context["cik"]:
        queries["fundamentals"] = fundamentals_sql(context["cik"], cutoff, database)
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
        futures = {pool.submit(clickhouse_rows, client, query): name for name, query in queries.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:  # The response remains useful when one independent source table is unavailable.
                results[name] = []
                errors[name] = f"{name.replace('_', ' ').title()} source is unavailable."
                LOGGER.warning("Ticker facts source %s failed: %s", name, error)

    market_rows = results.get("market", [])
    market = first(market_rows)
    float_rows = results.get("float", [])
    float_row = first(float_rows)
    reported_float_row = first_with_number(float_rows, "free_float")
    short_rows = results.get("short_interest", [])
    short_interest = short_rows[0] if short_rows else {}
    previous_short_interest = short_rows[1] if len(short_rows) > 1 else {}
    short_volume_rows = results.get("short_volume", [])
    short_volume = aggregate_short_volume(short_volume_rows)
    borrow_rows = results.get("borrow", [])
    borrow = first(borrow_rows)
    volume_rows = results.get("volume", [])
    volume = aggregate_daily_volume(volume_rows)
    shares_outstanding = best_shares_outstanding(float_rows, market_rows, results.get("fundamentals", []))
    free_float = first_number(reported_float_row, "free_float")
    short_shares = first_number(short_interest, "short_interest")
    fundamentals = select_fundamentals(results.get("fundamentals", []), cutoff)
    fundamental_analysis = analyze_fundamentals(results.get("fundamentals", []))
    xbrl_analysis = build_xbrl_analysis(results.get("fundamentals", []), cutoff)
    identifiers = [
        {**row, "freshness": freshness_status(cutoff, row.get("last_seen_at_utc"))}
        for row in results.get("identifiers", [])
    ]
    synthesis = synthesize_stock_facts(
        as_of=cutoff,
        borrow=borrow,
        fails_to_deliver=first(results.get("fails_to_deliver")),
        float_rows=float_rows,
        fundamental_rows=results.get("fundamentals", []),
        market_rows=market_rows,
        reg_sho=first(results.get("reg_sho")),
        short_interest=short_interest,
        short_volume=short_volume,
        split_rows=results.get("splits", []),
        volume_rows=volume_rows,
    )
    warnings: list[str] = []
    if not free_float and synthesis.get("cards", [{}])[0].get("method") == "estimated":
        warnings.append("Reported free float is unavailable; tradable supply uses a clearly labeled SEC-derived estimate and uncertainty range.")
    elif not free_float:
        warnings.append("Free float is unavailable; shares outstanding is shown only as an upper bound and is not presented as tradable supply.")
    if borrow and not any(borrow.get(field) is not None for field in ("shortable_shares", "indicative_borrow_rate", "fee_rate")):
        warnings.append("IBKR returned a borrow snapshot but did not publish availability or rate fields.")
    if not fundamentals:
        warnings.append("No selected SEC-reported fundamental facts were available by the selected clock.")
    return {
        "as_of": cutoff.isoformat(),
        "errors": errors,
        "facts": {
            "borrow": borrow,
            "classifications": results.get("classifications", []),
            "corporate": first(results.get("corporate")),
            "fails_to_deliver": first(results.get("fails_to_deliver")),
            "float": reported_float_row or float_row,
            "identity": {
                **anchor,
                "cik": context["cik"] or None,
                "company_country_code": company_country_code(anchor),
                "company_country_source": company_country_source(anchor),
            },
            "market": market,
            "reg_sho": first(results.get("reg_sho")),
            "short_interest": {
                **short_interest,
                "change_from_previous": difference(short_interest, previous_short_interest, "short_interest"),
                "percent_of_float": ratio_percent(short_shares, free_float),
                "percent_of_outstanding": ratio_percent(short_shares, shares_outstanding),
                "previous_settlement_date": previous_short_interest.get("settlement_date"),
            },
            "short_volume": short_volume,
            "volume": volume,
        },
        "fundamentals": fundamentals,
        "fundamental_analysis": fundamental_analysis,
        "xbrl_analysis": xbrl_analysis,
        "freshness": fact_freshness(
            cutoff=cutoff,
            borrow=borrow,
            float_row=reported_float_row or float_row,
            fundamental_rows=fundamentals,
            market=market,
            short_interest=short_interest,
            short_volume=short_volume,
            volume=volume,
        ),
        "identifiers": identifiers,
        "metric_changes": metric_changes(
            market_rows=market_rows,
            float_rows=float_rows,
            short_interest_rows=short_rows,
            short_volume_rows=short_volume_rows,
            borrow_rows=borrow_rows,
            volume_rows=volume_rows,
            fundamental_rows=results.get("fundamentals", []),
        ),
        "synthesis": synthesis,
        "sources": source_inventory(results),
        "status": "partial" if errors else "ready",
        "symbol": ticker,
        "warnings": warnings,
    }


def ticker_fact_history_payload(symbol: str, metric: str, *, as_of: str | None = None, database: str = "q_live") -> dict[str, Any]:
    ticker = normalize_ticker(symbol)
    cutoff = parse_as_of(as_of)
    if not DATABASE_PATTERN.fullmatch(database):
        raise ValueError("Ticker facts database is not a valid identifier.")
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    anchors = clickhouse_rows(client, identity_anchor_sql(ticker, cutoff, database))
    if not anchors:
        return {"as_of": cutoff.isoformat(), "metric": metric, "points": [], "status": "not_found", "symbol": ticker}
    anchor = anchors[0]
    symbol_id = str(anchor.get("symbol_id") or "")
    cik = issuer_cik(str(anchor.get("issuer_id") or ""))
    normalized_metric = str(metric or "").strip().lower()
    if normalized_metric == "health_score":
        return ticker_health_history_payload(
            client=client,
            ticker=ticker,
            symbol_id=symbol_id,
            cik=cik,
            cutoff=cutoff,
            database=database,
        )
    rows: list[dict[str, Any]]
    label: str
    unit: str
    points: list[dict[str, Any]]
    if normalized_metric in {"market_cap", "shares_outstanding"}:
        rows = clickhouse_rows(client, latest_rows_sql(database, "market_security_market_snapshot_v1", "symbol_id", symbol_id, "observed_at_utc", cutoff, limit=HISTORY_LIMIT))
        field = "market_cap" if normalized_metric == "market_cap" else "share_class_shares_outstanding"
        label = "Market cap" if normalized_metric == "market_cap" else "Shares outstanding"
        unit = "USD" if normalized_metric == "market_cap" else "shares"
        points = history_points(rows, field, "observed_at_utc")
    elif normalized_metric == "free_float":
        rows = clickhouse_rows(client, latest_rows_sql(database, "market_security_float_v1", "symbol_id", symbol_id, "effective_date", cutoff, date_column=True, limit=HISTORY_LIMIT))
        label, unit = "Free float", "shares"
        points = history_points(rows, "free_float", "effective_date")
    elif normalized_metric in {"short_interest", "days_to_cover"}:
        rows = clickhouse_rows(client, short_interest_sql(symbol_id, cutoff, database, limit=HISTORY_LIMIT))
        field = "short_interest" if normalized_metric == "short_interest" else "days_to_cover"
        label = "Short interest" if normalized_metric == "short_interest" else "Days to cover"
        unit = "shares" if normalized_metric == "short_interest" else "days"
        points = history_points(rows, field, "settlement_date")
    elif normalized_metric in {"short_volume_ratio", "short_volume_ratio_20d"}:
        rows = clickhouse_rows(client, short_volume_history_sql(symbol_id, cutoff, database, limit=HISTORY_LIMIT))
        label = "FINRA short-volume ratio" if normalized_metric == "short_volume_ratio" else "20-session FINRA short-volume ratio"
        unit = "percent"
        points = short_volume_history_points(rows, rolling=normalized_metric.endswith("20d"))
    elif normalized_metric in {"daily_volume", "relative_volume_20d"}:
        rows = clickhouse_rows(client, daily_volume_history_sql(ticker, cutoff, historical_database(), limit=HISTORY_LIMIT))
        label = "Daily volume" if normalized_metric == "daily_volume" else "Relative volume versus prior 20 sessions"
        unit = "shares" if normalized_metric == "daily_volume" else "multiple"
        points = daily_volume_history_points(rows, relative=normalized_metric.endswith("20d"))
    elif normalized_metric in {"shortable_shares", "indicative_borrow_rate", "fee_rate"}:
        rows = clickhouse_rows(client, latest_rows_sql(database, "market_security_borrow_v1", "provider_ticker", ticker, "observed_at_utc", cutoff, limit=HISTORY_LIMIT))
        labels = {"shortable_shares": "IBKR shortable shares", "indicative_borrow_rate": "IBKR indicative borrow rate", "fee_rate": "IBKR fee rate"}
        label = labels[normalized_metric]
        unit = "shares" if normalized_metric == "shortable_shares" else "percent"
        points = history_points(rows, normalized_metric, "observed_at_utc")
    elif normalized_metric.startswith("fundamental:"):
        tag = normalized_metric.split(":", 1)[1]
        allowed = {candidate for _, alternatives in FUNDAMENTAL_TAGS for candidate in alternatives}
        if tag not in {candidate.lower() for candidate in allowed} or not cik:
            raise ValueError("Unknown or unavailable fundamental metric.")
        canonical_tag = next(candidate for candidate in allowed if candidate.lower() == tag)
        rows = clickhouse_rows(client, fundamental_history_sql(cik, canonical_tag, cutoff, database, limit=HISTORY_LIMIT))
        label = next((name for name, alternatives in FUNDAMENTAL_TAGS if canonical_tag in alternatives), canonical_tag)
        unit = str(rows[0].get("unit_code") or "reported") if rows else "reported"
        points = history_points(rows, "value", "period_end_date", extra_fields=("fiscal_period", "form_type", "filed_at_utc"))
    else:
        raise ValueError("Unknown ticker-fact metric.")
    return {
        "as_of": cutoff.isoformat(),
        "label": label,
        "metric": normalized_metric,
        "points": points,
        "row_count": len(points),
        "status": "ready" if points else "not_found",
        "symbol": ticker,
        "truncated": len(rows) >= HISTORY_LIMIT,
        "unit": unit,
    }


def ticker_health_history_payload(
    *,
    client: ClickHouseHttpClient,
    ticker: str,
    symbol_id: str,
    cik: str,
    cutoff: datetime,
    database: str,
) -> dict[str, Any]:
    queries = {
        "borrow": latest_rows_sql(database, "market_security_borrow_v1", "provider_ticker", ticker, "observed_at_utc", cutoff, limit=HISTORY_LIMIT),
        "fails": fails_to_deliver_sql(ticker, cutoff, database, limit=HISTORY_LIMIT),
        "float": latest_rows_sql(database, "market_security_float_v1", "symbol_id", symbol_id, "effective_date", cutoff, date_column=True, limit=HISTORY_LIMIT),
        "market": latest_rows_sql(database, "market_security_market_snapshot_v1", "symbol_id", symbol_id, "observed_at_utc", cutoff, limit=HISTORY_LIMIT),
        "reg_sho": reg_sho_sql(ticker, cutoff, database, limit=HISTORY_LIMIT),
        "short_interest": short_interest_sql(symbol_id, cutoff, database, limit=HISTORY_LIMIT),
        "short_volume": short_volume_history_sql(symbol_id, cutoff, database, limit=HISTORY_LIMIT),
        "splits": splits_sql(symbol_id, cutoff, database, limit=HISTORY_LIMIT),
        "volume": daily_volume_history_sql(ticker, cutoff, historical_database(), limit=HISTORY_LIMIT),
    }
    if cik:
        queries["fundamentals"] = fundamentals_history_sql(cik, cutoff, database)
    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
        futures = {pool.submit(clickhouse_rows, client, query): name for name, query in queries.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    points = build_health_timeline(results, cutoff)
    return {
        "as_of": cutoff.isoformat(),
        "comparisons": health_comparisons(points),
        "label": "Evidence-weighted stock health",
        "metric": "health_score",
        "points": points,
        "row_count": len(points),
        "status": "ready" if points else "not_found",
        "symbol": ticker,
        "truncated": any(len(rows) >= HISTORY_LIMIT for rows in results.values()),
        "unit": "score",
    }


def normalize_ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("Ticker must contain 1-16 letters, numbers, dots, or hyphens.")
    return ticker


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("as_of must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError("as_of must include an explicit timezone.")
    return parsed.astimezone(UTC)


def clickhouse_rows(client: ClickHouseHttpClient, query: str) -> list[dict[str, Any]]:
    payload = client.execute(query)
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def identity_anchor_sql(ticker: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    symbol = sql_string(ticker)
    instant = sql_string(clickhouse_timestamp(cutoff))
    day = sql_string(cutoff.date().isoformat())
    return f"""
        WITH (SELECT max(universe_date) FROM {db}.feature_tradable_universe_v1 FINAL
              WHERE universe_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS latest_date
        SELECT
            u.symbol_id AS symbol_id, u.listing_id AS listing_id, u.security_id AS security_id,
            u.issuer_id AS issuer_id, u.ticker AS ticker, u.exchange_code AS exchange_code,
            u.currency_code AS currency_code, u.ibkr_conid AS ibkr_conid, u.product_type AS product_type,
            u.asset_class AS asset_class, u.is_tradable AS is_tradable, u.exclusion_reason AS exclusion_reason,
            u.universe_date AS universe_date, s.display_name AS display_name,
            s.instrument_type AS instrument_type, s.security_type AS security_type,
            listing.list_date AS list_date, sec.security_name AS security_name, sec.has_options AS has_options,
            issuer.issuer_name AS issuer_name, issuer.legal_name AS legal_name,
            issuer.branding_name AS branding_name, issuer.entity_type AS entity_type,
            issuer.domicile_country_code AS domicile_country_code,
            issuer.state_of_incorporation AS state_of_incorporation, issuer.sic_code AS sic_code,
            issuer.sic_description AS sic_description, issuer.sector AS sector,
            issuer.industry AS industry, issuer.industry_group AS industry_group,
            issuer.website_url AS website_url, issuer.investor_website_url AS investor_website_url,
            issuer.last_verified_at_utc AS last_verified_at_utc
        FROM {db}.feature_tradable_universe_v1 AS u FINAL
        LEFT JOIN {db}.id_symbol_v1 AS s FINAL
            ON s.symbol_id = u.symbol_id AND s.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_listing_v1 AS listing FINAL
            ON listing.listing_id = u.listing_id AND listing.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_security_v1 AS sec FINAL
            ON sec.security_id = u.security_id AND sec.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL
            ON issuer.issuer_id = u.issuer_id AND issuer.first_seen_at_utc <= parseDateTime64BestEffort({instant})
        WHERE u.universe_date = latest_date AND upper(u.ticker) = {symbol}
        ORDER BY u.is_tradable DESC, u.currency_code = 'USD' DESC, u.product_type = 'STK' DESC, u.exchange_code ASC
        LIMIT 1
        FORMAT JSONEachRow
    """


def market_snapshot_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    return latest_rows_sql(database, "market_security_market_snapshot_v1", "symbol_id", symbol_id, "observed_at_utc", cutoff, limit=20)


def float_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    # A newer provider row can contain shares outstanding while omitting float.
    # Keep enough dated observations to select each field from its newest actual publication.
    return latest_rows_sql(database, "market_security_float_v1", "symbol_id", symbol_id, "effective_date", cutoff, date_column=True, limit=40)


def borrow_sql(ticker: str, cutoff: datetime, database: str) -> str:
    return latest_rows_sql(database, "market_security_borrow_v1", "provider_ticker", ticker, "observed_at_utc", cutoff, limit=20)


def short_interest_sql(symbol_id: str, cutoff: datetime, database: str, *, limit: int = 30) -> str:
    db = quote_ident(database)
    return f"""
        SELECT settlement_date, publication_date, published_at_utc, short_interest, avg_daily_volume,
               days_to_cover, source_system, source_venue, inserted_at
        FROM
        (
            SELECT settlement_date, publication_date, published_at_utc, short_interest, avg_daily_volume,
                   days_to_cover, source_system, source_venue, inserted_at
            FROM {db}.market_short_interest_v1 FINAL
            WHERE symbol_id = {sql_string(symbol_id)}
              AND settlement_date <= toDate({sql_string(cutoff.date().isoformat())})
              AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY settlement_date DESC, inserted_at DESC
            LIMIT 1 BY settlement_date
        )
        ORDER BY settlement_date DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def short_volume_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    return short_volume_history_sql(symbol_id, cutoff, database, limit=21)


def short_volume_history_sql(symbol_id: str, cutoff: datetime, database: str, *, limit: int = HISTORY_LIMIT) -> str:
    db = quote_ident(database)
    return f"""
        SELECT trade_date, short_volume, total_volume, exempt_volume, short_volume_ratio,
               source_system, source_venue, inserted_at
        FROM
        (
            SELECT * FROM {db}.market_short_volume_v1 FINAL
            WHERE symbol_id = {sql_string(symbol_id)}
              AND trade_date <= toDate({sql_string(cutoff.date().isoformat())})
              AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY trade_date DESC, inserted_at DESC
            LIMIT 1 BY trade_date
        )
        ORDER BY trade_date DESC, inserted_at DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def volume_sql(ticker: str, cutoff: datetime, database: str) -> str:
    return daily_volume_history_sql(ticker, cutoff, database, limit=MAIN_HISTORY_DAYS)


def daily_volume_history_sql(ticker: str, cutoff: datetime, database: str, *, limit: int = HISTORY_LIMIT) -> str:
    db = quote_ident(database)
    return f"""
        SELECT session_date, bar_end, close, size_sum
        FROM {db}.macro_bars_by_time_symbol FINAL
        WHERE sym = {sql_string(ticker)} AND timeframe = '1d' AND bar_family = 'trade'
          AND bar_end <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY bar_end DESC
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def historical_database() -> str:
    value = os.environ.get("QMD_HISTORY_DATABASE") or os.environ.get("QMD_HISTORICAL_CLICKHOUSE_DATABASE") or "market_sip_compact"
    if not DATABASE_PATTERN.fullmatch(value):
        raise ValueError("QMD history database is not a valid identifier.")
    return value


def fundamentals_sql(cik: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    tags = sorted({tag for _, alternatives in FUNDAMENTAL_TAGS for tag in alternatives})
    tag_clause = ", ".join(sql_string(tag) for tag in tags)
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag IN ({tag_clause})
          AND filed_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
          AND recorded_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY tag ASC, period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
        LIMIT 16 BY tag
        FORMAT JSONEachRow
    """


def fundamental_history_sql(cik: str, tag: str, cutoff: datetime, database: str, *, limit: int = HISTORY_LIMIT) -> str:
    db = quote_ident(database)
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag = {sql_string(tag)}
          AND filed_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
          AND recorded_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY period_end_date DESC, filed_at_utc DESC, recorded_at_utc DESC
        LIMIT 1 BY period_end_date, fiscal_period, unit_code
        LIMIT {max(1, min(HISTORY_LIMIT, limit))}
        FORMAT JSONEachRow
    """


def fundamentals_history_sql(cik: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    tags = sorted({tag for _, alternatives in FUNDAMENTAL_TAGS for tag in alternatives})
    return f"""
        SELECT tag, taxonomy, unit_code, value, fiscal_year, fiscal_period, period_end_date,
               filed_at_utc, form_type, accession_number, recorded_at_utc, inserted_at
        FROM {db}.sec_xbrl_company_fact_v3 FINAL
        WHERE cik = {sql_string(cik)} AND tag IN ({", ".join(sql_string(tag) for tag in tags)})
          AND filed_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
          AND recorded_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY filed_at_utc DESC, period_end_date DESC, recorded_at_utc DESC
        LIMIT 64 BY tag
        FORMAT JSONEachRow
    """


def identifiers_sql(issuer_id: str, security_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    return f"""
        SELECT entity, identifier_kind, identifier_value, source_system, is_primary, last_seen_at_utc
        FROM
        (
            SELECT 'issuer' AS entity, identifier_kind, identifier_value, source_system, is_primary, last_seen_at_utc
            FROM {db}.id_issuer_identifier_v1 FINAL WHERE issuer_id = {sql_string(issuer_id)}
            UNION ALL
            SELECT 'security' AS entity, identifier_kind, identifier_value, source_system, is_primary, last_seen_at_utc
            FROM {db}.id_security_identifier_v1 FINAL WHERE security_id = {sql_string(security_id)}
        )
        WHERE last_seen_at_utc <= parseDateTime64BestEffort({instant})
        ORDER BY is_primary DESC, entity ASC, identifier_kind ASC
        FORMAT JSONEachRow
    """


def classifications_sql(security_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    return f"""
        SELECT classification_source, classification_scheme, classification_level, classification_value, last_seen_at_utc
        FROM {db}.market_security_classification_v1 FINAL
        WHERE security_id = {sql_string(security_id)}
          AND last_seen_at_utc <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
        ORDER BY classification_source ASC, classification_scheme ASC, classification_level ASC
        LIMIT 30
        FORMAT JSONEachRow
    """


def corporate_events_sql(symbol_id: str, cutoff: datetime, database: str) -> str:
    db = quote_ident(database)
    instant = sql_string(clickhouse_timestamp(cutoff))
    day = sql_string(cutoff.date().isoformat())
    return f"""
        SELECT
            (SELECT max(execution_date) FROM {db}.market_stock_split_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND execution_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_split_date,
            (SELECT argMax(split_from, tuple(execution_date, inserted_at)) FROM {db}.market_stock_split_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND execution_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_split_from,
            (SELECT argMax(split_to, tuple(execution_date, inserted_at)) FROM {db}.market_stock_split_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND execution_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_split_to,
            (SELECT max(ex_dividend_date) FROM {db}.market_cash_dividend_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND ex_dividend_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_ex_dividend_date,
            (SELECT argMax(cash_amount, tuple(ex_dividend_date, inserted_at)) FROM {db}.market_cash_dividend_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND ex_dividend_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS last_dividend_amount,
            (SELECT argMax(currency_code, tuple(ex_dividend_date, inserted_at)) FROM {db}.market_cash_dividend_v1 FINAL
             WHERE symbol_id = {sql_string(symbol_id)} AND ex_dividend_date <= toDate({day}) AND inserted_at <= parseDateTime64BestEffort({instant})) AS dividend_currency
        FORMAT JSONEachRow
    """


def fails_to_deliver_sql(ticker: str, cutoff: datetime, database: str, *, limit: int = 30) -> str:
    return latest_rows_sql(
        database,
        "market_fails_to_deliver_v1",
        "provider_ticker",
        ticker,
        "settlement_date",
        cutoff,
        date_column=True,
        limit=limit,
    )


def reg_sho_sql(ticker: str, cutoff: datetime, database: str, *, limit: int = 30) -> str:
    return latest_rows_sql(
        database,
        "market_reg_sho_threshold_v1",
        "provider_ticker",
        ticker,
        "threshold_date",
        cutoff,
        date_column=True,
        limit=limit,
    )


def splits_sql(symbol_id: str, cutoff: datetime, database: str, *, limit: int = 100) -> str:
    return latest_rows_sql(
        database,
        "market_stock_split_v1",
        "symbol_id",
        symbol_id,
        "execution_date",
        cutoff,
        date_column=True,
        limit=limit,
    )


def latest_sql(database: str, table: str, key: str, value: str, order: str, cutoff: datetime, *, date_column: bool = False) -> str:
    return latest_rows_sql(database, table, key, value, order, cutoff, date_column=date_column, limit=1)


def latest_rows_sql(
    database: str,
    table: str,
    key: str,
    value: str,
    order: str,
    cutoff: datetime,
    *,
    date_column: bool = False,
    limit: int = 2,
) -> str:
    db = quote_ident(database)
    relation = f"{db}.{quote_ident(table)}"
    cutoff_clause = f"toDate({sql_string(cutoff.date().isoformat())})" if date_column else f"parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})"
    return f"""
        SELECT * FROM
        (
            SELECT * FROM {relation} FINAL
            WHERE {quote_ident(key)} = {sql_string(value)} AND {quote_ident(order)} <= {cutoff_clause}
              AND inserted_at <= parseDateTime64BestEffort({sql_string(clickhouse_timestamp(cutoff))})
            ORDER BY {quote_ident(order)} DESC, inserted_at DESC
            LIMIT 1 BY {quote_ident(order)}
        )
        ORDER BY {quote_ident(order)} DESC, inserted_at DESC
        LIMIT {max(1, min(10_000, limit))}
        FORMAT JSONEachRow
    """


def select_fundamentals(rows: list[dict[str, Any]], as_of: datetime | None = None) -> list[dict[str, Any]]:
    by_tag: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_tag.setdefault(str(row.get("tag") or ""), row)
    selected: list[dict[str, Any]] = []
    for label, alternatives in FUNDAMENTAL_TAGS:
        row = next((by_tag[tag] for tag in alternatives if tag in by_tag), None)
        if row:
            selected.append({
                "label": label,
                "description": FUNDAMENTAL_DESCRIPTIONS.get(label, "SEC-reported XBRL observation; inspect the exact tag and period before comparison."),
                **row,
                **({"freshness": freshness_status(as_of, row.get("filed_at_utc") or row.get("recorded_at_utc"))} if as_of else {}),
            })
    return selected


def select_fundamental_histories(rows: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    """Select one canonical XBRL metric per label while retaining comparable causal history."""
    selected: list[dict[str, Any]] = []
    for label, alternatives in FUNDAMENTAL_TAGS:
        observations = comparable_facts(rows, alternatives)
        if not observations:
            continue
        observations = sorted(observations, key=lambda row: (str(row.get("period_end_date") or ""), str(row.get("filed_at_utc") or row.get("recorded_at_utc") or "")))
        history = [
            {
                "accession_number": row.get("accession_number"),
                "filed_at_utc": row.get("filed_at_utc") or row.get("recorded_at_utc"),
                "fiscal_period": row.get("fiscal_period"),
                "period_end_date": row.get("period_end_date"),
                "tag": row.get("tag"),
                "taxonomy": row.get("taxonomy"),
                "unit_code": row.get("unit_code"),
                "value": row_value(row),
            }
            for row in observations[-12:]
        ]
        latest = observations[-1]
        latest_value = row_value(latest)
        previous_value = row_value(observations[-2]) if len(observations) > 1 else None
        change_percent = ((latest_value - previous_value) / abs(previous_value) * 100.0) if latest_value is not None and previous_value not in (None, 0) else None
        direction = FUNDAMENTAL_CHANGE_DIRECTION.get(label, "contextual")
        if change_percent is None or direction == "contextual" or abs(change_percent) < 0.05:
            change_tone = "neutral"
        else:
            favorable = change_percent > 0 if direction == "higher_is_stronger" else change_percent < 0
            change_tone = "positive" if favorable else "negative"
        selected.append({
            "change_percent": change_percent,
            "change_tone": change_tone,
            "description": FUNDAMENTAL_DESCRIPTIONS.get(label, "SEC-reported XBRL observation; inspect the exact tag and period before comparison."),
            "direction": direction,
            "freshness": freshness_status(as_of, latest.get("filed_at_utc") or latest.get("recorded_at_utc")),
            "history": history,
            "label": label,
            **latest,
        })
    return selected


def analyze_fundamentals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build auditable financial-strength facets from aligned SEC observations."""
    revenue = comparable_facts(rows, FUNDAMENTAL_TAGS[0][1])
    gross_profit = comparable_facts(rows, FUNDAMENTAL_TAGS[1][1])
    operating_income = comparable_facts(rows, FUNDAMENTAL_TAGS[2][1])
    net_income = comparable_facts(rows, FUNDAMENTAL_TAGS[3][1])
    operating_cash = comparable_facts(rows, FUNDAMENTAL_TAGS[5][1])
    capex = comparable_facts(rows, FUNDAMENTAL_TAGS[6][1])
    current_assets = fact_observations(rows, ("AssetsCurrent", "CurrentAssets"))
    current_liabilities = fact_observations(rows, ("LiabilitiesCurrent", "CurrentLiabilities"))
    assets = fact_observations(rows, ("Assets",))
    equity = fact_observations(rows, ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity"))
    cash = fact_observations(rows, ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"))
    interest = comparable_facts(rows, ("InterestExpenseNonOperating", "InterestAndDebtExpense", "InterestExpense", "FinanceCosts", "InterestExpenseOnDebtInstrumentsIssued"))
    research = comparable_facts(rows, ("ResearchAndDevelopmentExpense",))
    sga = comparable_facts(rows, ("SellingGeneralAndAdministrativeExpense",))
    basic_shares = comparable_facts(rows, ("WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageShares"))
    diluted_shares = comparable_facts(rows, ("WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares"))

    gross_value, gross_revenue = aligned_values(gross_profit, revenue)
    operating_value, operating_revenue = aligned_values(operating_income, revenue)
    income_value, income_revenue = aligned_values(net_income, revenue)
    ocf_value, ocf_revenue = aligned_values(operating_cash, revenue)
    capex_value, _ = aligned_values(capex, revenue)
    free_cash_flow = ocf_value - abs(capex_value) if ocf_value is not None and capex_value is not None else None
    assets_value = row_value(first(assets))
    equity_value = row_value(first(equity))
    current_assets_value, current_liabilities_value = aligned_values(current_assets, current_liabilities)
    cash_value = row_value(first(cash))
    debt_value = latest_total_debt(rows)
    interest_value = row_value(first(interest))
    basic_value = aligned_numerator_value(basic_shares, diluted_shares)
    diluted_value = row_value(first(diluted_shares))

    metrics = [
        derived_fundamental("free_cash_flow", "Free cash flow", free_cash_flow, "USD", first(operating_cash), "Operating cash flow minus capital expenditure."),
        derived_fundamental("gross_margin", "Gross margin", percent_ratio(gross_value, gross_revenue), "percent", first(gross_profit), "Gross profit divided by aligned revenue."),
        derived_fundamental("operating_margin", "Operating margin", percent_ratio(operating_value, operating_revenue), "percent", first(operating_income), "Operating income divided by aligned revenue."),
        derived_fundamental("net_margin", "Net margin", percent_ratio(income_value, income_revenue), "percent", first(net_income), "Net income divided by aligned revenue."),
        derived_fundamental("free_cash_flow_margin", "Free-cash-flow margin", percent_ratio(free_cash_flow, ocf_revenue), "percent", first(operating_cash), "Free cash flow divided by aligned revenue."),
        derived_fundamental("return_on_assets", "Return on assets", percent_ratio(income_value, assets_value), "percent", first(assets), "Latest comparable net income divided by latest assets; a point-in-time approximation."),
        derived_fundamental("return_on_equity", "Return on equity", positive_denominator_percent(income_value, equity_value), "percent", first(equity), "Latest comparable net income divided by latest positive equity; a point-in-time approximation."),
        derived_fundamental("working_capital", "Working capital", current_assets_value - current_liabilities_value if current_assets_value is not None and current_liabilities_value is not None else None, "USD", first(current_assets), "Current assets minus aligned current liabilities."),
        derived_fundamental("current_ratio", "Current ratio", safe_ratio(current_assets_value, current_liabilities_value), "multiple", first(current_assets), "Current assets divided by aligned current liabilities."),
        derived_fundamental("debt_to_equity", "Debt to equity", positive_denominator_ratio(debt_value, equity_value), "multiple", first(equity), "Current plus noncurrent borrowings divided by positive equity; withheld when equity is nonpositive."),
        derived_fundamental("net_debt", "Net debt", debt_value - cash_value if debt_value is not None and cash_value is not None else None, "USD", first(cash), "Interest-bearing debt minus cash and equivalents."),
        derived_fundamental("interest_coverage", "Interest coverage", safe_ratio(operating_value, abs(interest_value) if interest_value is not None else None), "multiple", first(interest), "Operating income divided by interest expense."),
        derived_fundamental("revenue_growth", "Revenue growth", comparable_growth(revenue), "percent", first(revenue), "Change between the latest two comparable fiscal periods."),
        derived_fundamental("earnings_growth", "Earnings growth", comparable_growth(net_income), "percent", first(net_income), "Change in net income between comparable fiscal periods."),
        derived_fundamental("share_growth", "Basic share growth", comparable_growth(basic_shares), "percent", first(basic_shares), "Change in weighted-average basic shares between comparable periods."),
        derived_fundamental("dilution", "Dilution spread", percent_ratio(diluted_value - basic_value if diluted_value is not None and basic_value is not None else None, basic_value), "percent", first(diluted_shares), "Diluted shares above basic shares for the aligned period."),
        derived_fundamental("cash_conversion", "Cash conversion", safe_ratio(ocf_value, income_value), "multiple", first(operating_cash), "Operating cash flow divided by net income."),
        derived_fundamental("research_intensity", "R&D intensity", percent_ratio(*aligned_values(research, revenue)), "percent", first(research), "Research and development expense divided by aligned revenue."),
        derived_fundamental("sga_intensity", "SG&A intensity", percent_ratio(*aligned_values(sga, revenue)), "percent", first(sga), "Selling, general, and administrative expense divided by aligned revenue."),
    ]
    metrics = [metric for metric in metrics if metric["value"] is not None]

    profitability_components = [
        score_component("gross_margin", "Gross margin", percent_ratio(gross_value, gross_revenue), "percent", 20, 10, 60),
        score_component("operating_margin", "Operating margin", percent_ratio(operating_value, operating_revenue), "percent", 30, -5, 25),
        score_component("net_margin", "Net margin", percent_ratio(income_value, income_revenue), "percent", 30, -5, 20),
        score_component("return_on_equity", "Return on equity", positive_denominator_percent(income_value, equity_value), "percent", 20, -10, 30),
    ]
    growth_components = [
        score_component("revenue_growth", "Revenue growth", comparable_growth(revenue), "percent", 55, -10, 25),
        score_component("earnings_growth", "Earnings growth", comparable_growth(net_income), "percent", 45, -25, 40),
    ]
    cash_components = [
        score_component("free_cash_flow_margin", "Free-cash-flow margin", percent_ratio(free_cash_flow, ocf_revenue), "percent", 60, -5, 20),
        score_component("cash_conversion", "Cash conversion", safe_ratio(ocf_value, income_value), "multiple", 40, 0.5, 1.5),
    ]
    balance_components = [
        score_component("current_ratio", "Current ratio", safe_ratio(current_assets_value, current_liabilities_value), "multiple", 40, 0.5, 2.0),
        score_component("debt_to_equity", "Debt / equity", positive_denominator_ratio(debt_value, equity_value), "multiple", 35, 0.0, 2.0, inverse=True),
        score_component("interest_coverage", "Interest coverage", safe_ratio(operating_value, abs(interest_value) if interest_value is not None else None), "multiple", 25, 1.0, 8.0),
    ]
    capital_components = [
        score_component("share_growth", "Share growth", comparable_growth(basic_shares), "percent", 60, -2.0, 8.0, inverse=True),
        score_component("dilution", "Dilution spread", percent_ratio(diluted_value - basic_value if diluted_value is not None and basic_value is not None else None, basic_value), "percent", 40, 0.0, 10.0, inverse=True),
    ]
    profitability, profitability_coverage = score_components(profitability_components)
    growth, growth_coverage = score_components(growth_components)
    cash_quality, cash_coverage = score_components(cash_components)
    balance, balance_coverage = score_components(balance_components)
    capital_discipline, capital_coverage = score_components(capital_components)
    facets = [
        fundamental_facet("profitability", "Profitability", profitability, profitability_coverage, 30, profitability_components),
        fundamental_facet("growth", "Growth", growth, growth_coverage, 20, growth_components),
        fundamental_facet("cash_quality", "Cash quality", cash_quality, cash_coverage, 20, cash_components),
        fundamental_facet("balance_sheet", "Balance sheet", balance, balance_coverage, 20, balance_components),
        fundamental_facet("capital_discipline", "Capital discipline", capital_discipline, capital_coverage, 10, capital_components),
    ]
    overall, coverage = weighted_facets([
        (facet["label"], facet.get("score"), facet["coverage_percent"], facet["overall_weight"])
        for facet in facets
    ])
    effective_total = sum(facet["overall_weight"] * facet["coverage_percent"] / 100.0 for facet in facets if facet.get("score") is not None)
    for facet in facets:
        effective_weight = facet["overall_weight"] * facet["coverage_percent"] / 100.0 if facet.get("score") is not None else 0.0
        facet["effective_weight"] = effective_weight
        facet["contribution_points"] = (facet["score"] * effective_weight / effective_total) if effective_total and facet.get("score") is not None else None
    label, tone = strength_label(overall, coverage)
    return {
        "coverage_percent": coverage,
        "facets": facets,
        "label": label,
        "metrics": metrics,
        "score": overall if coverage >= 50 else None,
        "tone": tone,
        "formula": "sum(category_score * category_weight * category_coverage) / sum(category_weight * category_coverage)",
        "version": "sec_fundamental_strength_v2",
    }


def build_xbrl_analysis(rows: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    """Create a causal filing-by-filing financial evidence record for deep XBRL analysis."""
    available = [row for row in rows if _fact_available_at(row) is not None and _fact_available_at(row) <= as_of]
    current = analyze_fundamentals(available)
    selected = select_fundamental_histories(available, as_of)
    classes: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        class_name = FUNDAMENTAL_CLASS_BY_LABEL.get(str(row.get("label") or ""), "Other reported facts")
        classes.setdefault(class_name, []).append(row)

    filing_clocks = sorted({_fact_available_at(row) for row in available if _fact_available_at(row) is not None})
    timeline: list[dict[str, Any]] = []
    previous_signature: tuple[Any, ...] | None = None
    for clock in filing_clocks:
        prefix = [row for row in available if (_fact_available_at(row) or as_of) <= clock]
        analysis = analyze_fundamentals(prefix)
        signature = (
            analysis.get("score"),
            analysis.get("coverage_percent"),
            tuple((facet.get("id"), facet.get("score")) for facet in analysis.get("facets", [])),
        )
        if signature == previous_signature:
            continue
        accessions = sorted({str(row.get("accession_number") or "") for row in prefix if _fact_available_at(row) == clock and row.get("accession_number")})
        timeline.append({
            "available_at": clock.isoformat(),
            "accession_numbers": accessions,
            "coverage_percent": analysis.get("coverage_percent"),
            "facets": analysis.get("facets", []),
            "label": analysis.get("label"),
            "score": analysis.get("score"),
            "tone": analysis.get("tone"),
        })
        previous_signature = signature
    timeline = timeline[-32:]
    scored = [point for point in timeline if point.get("score") is not None]
    current_score = current.get("score")
    previous_score = scored[-2].get("score") if len(scored) > 1 else None
    delta = current_score - previous_score if current_score is not None and previous_score is not None else None
    if current_score is None:
        decision_label, decision_tone = "Insufficient filing evidence", "muted"
    elif delta is not None and delta >= 5:
        decision_label, decision_tone = "Financial evidence strengthening", "positive"
    elif delta is not None and delta <= -5:
        decision_label, decision_tone = "Financial evidence weakening", "negative"
    else:
        decision_label, decision_tone = "Financial evidence stable", "neutral"
    return {
        "classes": [{"id": name.lower().replace(" & ", "_").replace(" ", "_"), "label": name, "facts": facts} for name, facts in classes.items()],
        "current": current,
        "decision": {
            "delta_from_previous": delta,
            "label": decision_label,
            "tone": decision_tone,
            "scope": "Slow-moving SEC filing evidence; not a short-term price forecast.",
        },
        "latest_filing_at": max(filing_clocks).isoformat() if filing_clocks else None,
        "timeline": timeline,
        "formula": current.get("formula"),
        "version": "sec_xbrl_decision_evidence_v2",
    }


def _fact_available_at(row: dict[str, Any]) -> datetime | None:
    raw = row.get("filed_at_utc") or row.get("recorded_at_utc")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def score_component(
    component_id: str,
    label: str,
    value: float | None,
    unit: str,
    weight: float,
    lower_bound: float,
    upper_bound: float,
    *,
    inverse: bool = False,
) -> dict[str, Any]:
    normalized = inverse_scaled(value, lower_bound, upper_bound) if inverse else scaled(value, lower_bound, upper_bound)
    direction = "lower_is_stronger" if inverse else "higher_is_stronger"
    operator = "100 - clamp" if inverse else "clamp"
    return {
        "direction": direction,
        "formula": f"{operator}((value - {lower_bound:g}) / ({upper_bound:g} - {lower_bound:g}) * 100)",
        "id": component_id,
        "label": label,
        "lower_bound": lower_bound,
        "normalized_score": normalized,
        "unit": unit,
        "upper_bound": upper_bound,
        "value": value,
        "weight": weight,
        "weighted_points": normalized * weight / 100.0 if normalized is not None else None,
    }


def score_components(components: list[dict[str, Any]]) -> tuple[float | None, float]:
    return weighted_available([
        (str(component["label"]), component.get("normalized_score"), float(component["weight"]))
        for component in components
    ])


def fundamental_facet(
    facet_id: str,
    label: str,
    score: float | None,
    coverage: float,
    overall_weight: float,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    strength, tone = strength_label(score, coverage)
    available_weight = sum(float(component["weight"]) for component in components if component.get("normalized_score") is not None)
    return {
        "available_component_weight": available_weight,
        "components": components,
        "coverage_percent": coverage,
        "formula": "sum(component_score * component_weight) / sum(available component weights)",
        "id": facet_id,
        "label": label,
        "overall_weight": overall_weight,
        "score": score if coverage >= 40 else None,
        "strength": strength,
        "tone": tone,
    }


def strength_label(score: float | None, coverage: float) -> tuple[str, str]:
    if score is None or coverage < 40:
        return "Insufficient", "muted"
    if score >= 80:
        return "Robust", "positive"
    if score >= 65:
        return "Strong", "positive"
    if score >= 45:
        return "Mixed", "neutral"
    if score >= 25:
        return "Fragile", "warning"
    return "Weak", "negative"


def derived_fundamental(metric_id: str, label: str, value: float | None, unit: str, source_row: dict[str, Any], formula: str) -> dict[str, Any]:
    return {
        "id": metric_id,
        "label": label,
        "value": value,
        "unit": unit,
        "formula": formula,
        "period_end_date": source_row.get("period_end_date"),
        "available_at": source_row.get("filed_at_utc") or source_row.get("recorded_at_utc"),
    }


def aligned_numerator_value(numerator_rows: list[dict[str, Any]], denominator_rows: list[dict[str, Any]]) -> float | None:
    return aligned_values(numerator_rows, denominator_rows)[0]


def aligned_values(numerator_rows: list[dict[str, Any]], denominator_rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    if not numerator_rows or not denominator_rows:
        return None, None
    denominator_by_period = {(str(row.get("period_end_date") or ""), str(row.get("fiscal_period") or "")): row for row in denominator_rows}
    numerator = next((row for row in numerator_rows if (str(row.get("period_end_date") or ""), str(row.get("fiscal_period") or "")) in denominator_by_period), None)
    if not numerator:
        return None, None
    denominator = denominator_by_period[(str(numerator.get("period_end_date") or ""), str(numerator.get("fiscal_period") or ""))]
    return row_value(numerator), row_value(denominator)


def latest_total_debt(rows: list[dict[str, Any]]) -> float | None:
    """Reconcile total debt without collapsing current and noncurrent components."""
    total_tags = ("Borrowings",)
    current_tags = ("LongTermDebtCurrent", "CurrentPortionOfLongTermDebt", "ShorttermBorrowings")
    noncurrent_tags = ("LongTermDebtNoncurrent", "LongtermBorrowings", "NoncurrentPortionOfOtherNoncurrentBorrowings")
    debt_tags = set(total_tags + current_tags + noncurrent_tags)
    candidates = [row for row in rows if str(row.get("tag") or "") in debt_tags and row_value(row) is not None]
    if not candidates:
        return None
    latest_period = max(str(row.get("period_end_date") or "") for row in candidates)
    period_rows = [row for row in candidates if str(row.get("period_end_date") or "") == latest_period]

    def priority_value(tags: tuple[str, ...]) -> float | None:
        for tag in tags:
            matching = [row for row in period_rows if str(row.get("tag") or "") == tag]
            if matching:
                matching.sort(key=lambda row: str(row.get("filed_at_utc") or row.get("recorded_at_utc") or ""), reverse=True)
                return row_value(matching[0])
        return None

    total = priority_value(total_tags)
    if total is not None:
        return total
    components = [value for value in (priority_value(current_tags), priority_value(noncurrent_tags)) if value is not None]
    return sum(components) if components else None


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator not in {None, 0} else None


def positive_denominator_ratio(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def positive_denominator_percent(numerator: float | None, denominator: float | None) -> float | None:
    ratio = positive_denominator_ratio(numerator, denominator)
    return ratio * 100.0 if ratio is not None else None


def percent_ratio(numerator: float | None, denominator: float | None) -> float | None:
    ratio = safe_ratio(numerator, denominator)
    return ratio * 100.0 if ratio is not None else None


def inverse_scaled(value: float | None, low: float, high: float) -> float | None:
    scaled_value = scaled(value, low, high)
    return None if scaled_value is None else 100.0 - scaled_value


def aggregate_daily_volume(rows: list[dict[str, Any]], offset: int = 0) -> dict[str, Any]:
    window = rows[offset: offset + 20]
    if not window:
        return {}
    latest = window[0]
    volumes = [value for row in window if (value := numeric_value(row.get("size_sum"))) is not None]
    average = sum(volumes) / len(volumes) if volumes else None
    latest_volume = numeric_value(latest.get("size_sum"))
    latest_close = numeric_value(latest.get("close"))
    return {
        "average_volume_20d": average,
        "latest_close": latest_close,
        "latest_dollar_volume": latest_volume * latest_close if latest_volume is not None and latest_close is not None else None,
        "latest_volume": latest_volume,
        "relative_volume_20d": latest_volume / average if latest_volume is not None and average else None,
        "session_date": latest.get("session_date"),
        "sessions": len(volumes),
    }


def aggregate_short_volume(rows: list[dict[str, Any]], offset: int = 0) -> dict[str, Any]:
    window = rows[offset: offset + 20]
    if not window:
        return {}
    latest = window[0]
    short_values = [numeric_value(row.get("short_volume")) or 0.0 for row in window]
    total_values = [numeric_value(row.get("total_volume")) or 0.0 for row in window]
    short_total = sum(short_values)
    volume_total = sum(total_values)
    return {
        "latest_exempt_volume": numeric_value(latest.get("exempt_volume")),
        "latest_short_volume": numeric_value(latest.get("short_volume")),
        "latest_short_volume_ratio": numeric_value(latest.get("short_volume_ratio")),
        "latest_total_volume": numeric_value(latest.get("total_volume")),
        "latest_trade_date": latest.get("trade_date"),
        "ratio_20d": short_total / volume_total if volume_total else None,
        "sessions": len(window),
        "short_volume_20d": short_total,
        "source_system": latest.get("source_system"),
        "source_venue": latest.get("source_venue"),
        "total_volume_20d": volume_total,
    }


def synthesize_stock_facts(
    *,
    as_of: datetime,
    borrow: dict[str, Any],
    fails_to_deliver: dict[str, Any],
    float_rows: list[dict[str, Any]],
    fundamental_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
    reg_sho: dict[str, Any],
    short_interest: dict[str, Any],
    short_volume: dict[str, Any],
    split_rows: list[dict[str, Any]],
    volume_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create one auditable point-in-time stock profile for UI and strategy consumers."""
    shares = best_shares_outstanding(float_rows, market_rows, fundamental_rows)
    latest_price = first_number(first(volume_rows), "close")
    reported_float_row = first_with_number(float_rows, "free_float")
    reported_float = first_number(reported_float_row, "free_float")
    float_estimate = estimate_tradable_shares(fundamental_rows, volume_rows, split_rows)
    estimated_float = numeric_value(float_estimate.get("value"))
    tradable_shares = reported_float or estimated_float
    float_comparison = ratio_percent(abs(reported_float - estimated_float), reported_float) if reported_float and estimated_float else None
    reconciliation = (
        "aligned" if float_comparison is not None and float_comparison <= 10
        else "review" if float_comparison is not None and float_comparison <= 25
        else "divergent" if float_comparison is not None
        else "single_source"
    )
    supply_evidence = [
        evidence("Reported free float", reported_float, "shares", reported_float_row.get("effective_date"), "reported", "Provider-published tradable share count."),
        evidence("SEC-implied float", estimated_float, "shares", float_estimate.get("period_end_date"), "estimated", "SEC public-float value divided by the price on its measurement date, then split-adjusted."),
        evidence("Shares outstanding", shares, "shares", latest_observation_date(float_rows, market_rows, fundamental_rows), "reported", "Upper bound for tradable shares, not a float estimate."),
        evidence("Market-cap-implied shares", market_cap_implied_shares(market_rows, volume_rows), "shares", first(market_rows).get("observed_at_utc"), "derived", "Market capitalization divided by an aligned daily close; used only as a shares-outstanding cross-check."),
    ]
    supply_method = "reported" if reported_float else "estimated" if estimated_float else "upper_bound"
    supply_card = metric_card(
        "tradable_supply",
        "Tradable supply",
        reported_float or estimated_float or shares,
        "shares",
        "Reported float" if reported_float else "Estimated range" if estimated_float else "Not established",
        "positive" if reconciliation == "aligned" else "warning" if reconciliation in {"review", "single_source"} else "negative",
        "high" if reported_float and reconciliation != "divergent" else "medium" if reported_float else "low" if estimated_float else "insufficient",
        supply_method,
        supply_evidence,
        {
            "comparison_gap_percent": float_comparison,
            "float_percent": ratio_percent(tradable_shares, shares),
            "lower_bound": float_estimate.get("lower_bound") if not reported_float else None,
            "reconciliation": reconciliation,
            "reported_value": reported_float,
            "estimated_value": estimated_float,
            "shares_outstanding": shares,
            "upper_bound": float_estimate.get("upper_bound") if not reported_float else shares,
        },
    )

    short_card, short_health = short_crowding_card(
        borrow=borrow,
        fails_to_deliver=fails_to_deliver,
        reg_sho=reg_sho,
        short_interest=short_interest,
        short_volume=short_volume,
        tradable_shares=tradable_shares,
        shares_outstanding=shares,
    )
    liquidity_card, liquidity_health = liquidity_card_and_score(volume_rows, tradable_shares or shares)
    share_card, share_health = share_base_card(fundamental_rows, float_rows, market_rows)
    financial_card, financial_scores = financial_card_and_scores(fundamental_rows)
    valuation_card = valuation_card_from_facts(fundamental_rows, latest_price, first_number(first(market_rows), "market_cap"))
    health = stock_health(
        as_of=as_of,
        financial_scores=financial_scores,
        liquidity_score=liquidity_health,
        share_score=share_health,
        short_score=short_health,
    )
    return {
        "cards": [supply_card, short_card, liquidity_card, share_card, financial_card, valuation_card],
        "health": health,
        "profile_summary": profile_summary(liquidity_card, short_card, share_card, financial_card, valuation_card),
        "version": "ticker_fact_synthesis_v1",
    }


def short_crowding_card(
    *,
    borrow: dict[str, Any],
    fails_to_deliver: dict[str, Any],
    reg_sho: dict[str, Any],
    short_interest: dict[str, Any],
    short_volume: dict[str, Any],
    tradable_shares: float | None,
    shares_outstanding: float | None,
) -> tuple[dict[str, Any], float | None]:
    short_shares = first_number(short_interest, "short_interest")
    denominator = tradable_shares or shares_outstanding
    short_percent = ratio_percent(short_shares, denominator)
    days_to_cover = numeric_value(short_interest.get("days_to_cover"))
    borrow_rate = first_number(borrow, "fee_rate", "indicative_borrow_rate")
    ftd = first_number(fails_to_deliver, "fails_quantity")
    ftd_percent = ratio_percent(ftd, denominator)
    threshold_active = str(reg_sho.get("threshold_status") or "").lower() in {"1", "active", "true", "threshold"}
    borrow_status = str(borrow.get("borrow_status") or "").lower()
    score_inputs: list[tuple[str, float | None, float]] = [
        ("Short interest / tradable supply", scaled(short_percent, 2, 20), 40),
        ("Days to cover", scaled(days_to_cover, 1, 10), 20),
        ("Borrow cost", scaled(borrow_rate, 1, 25), 15),
        ("Borrow availability", 90.0 if any(token in borrow_status for token in ("hard", "unavailable")) else 10.0 if any(token in borrow_status for token in ("available", "easy", "shortable")) else None, 5),
        ("Fails to deliver / supply", scaled(ftd_percent, 0.01, 0.25), 10),
        ("Reg SHO threshold", 100.0 if threshold_active else 0.0 if reg_sho else None, 10),
    ]
    risk, coverage = weighted_available(score_inputs)
    label = "Unavailable" if risk is None else "Low" if risk < 20 else "Normal" if risk < 40 else "Elevated" if risk < 60 else "High" if risk < 80 else "Extreme"
    tone = "muted" if risk is None else "positive" if risk < 20 else "neutral" if risk < 40 else "warning" if risk < 60 else "negative"
    evidence_rows = [
        evidence("Short interest", short_shares, "shares", short_interest.get("settlement_date"), "reported", "Open short positions at the exchange settlement date."),
        evidence("Short interest / supply", short_percent, "percent", short_interest.get("settlement_date"), "derived", "Short interest divided by reported or estimated tradable supply; shares outstanding is the fallback denominator."),
        evidence("Days to cover", days_to_cover, "days", short_interest.get("settlement_date"), "reported", "Reported short interest divided by average daily volume."),
        evidence("Borrow fee", borrow_rate, "percent", borrow.get("observed_at_utc"), "reported", "Latest persisted IBKR borrow cost."),
        evidence("Fails to deliver", ftd, "shares", fails_to_deliver.get("settlement_date"), "reported", "Settlement failures are stress evidence, not automatically short positions."),
        evidence("FINRA short-sale flow", ratio_to_percent(numeric_value(short_volume.get("ratio_20d"))), "percent", short_volume.get("latest_trade_date"), "reported", "Twenty-session short-sale volume flow; displayed separately and not added to short interest."),
    ]
    card = metric_card(
        "short_crowding", "Short crowding", short_percent, "percent", label, tone,
        confidence_from_coverage(coverage), "derived", evidence_rows,
        {
            "coverage_percent": coverage,
            "days_to_cover": days_to_cover,
            "denominator": denominator,
            "denominator_kind": "tradable_supply" if tradable_shares else "shares_outstanding" if shares_outstanding else "unavailable",
            "decision_inputs": [{"label": name, "score": value, "weight": weight} for name, value, weight in score_inputs],
            "risk_score": risk,
            "short_shares": short_shares,
        },
    )
    return card, None if risk is None else 100.0 - risk


def liquidity_card_and_score(volume_rows: list[dict[str, Any]], supply: float | None) -> tuple[dict[str, Any], float | None]:
    summary = aggregate_daily_volume(volume_rows)
    average = numeric_value(summary.get("average_volume_20d"))
    close = numeric_value(summary.get("latest_close"))
    turnover = ratio_percent(average, supply)
    dollar_volume = average * close if average is not None and close is not None else None
    dollar_score = None if not dollar_volume or dollar_volume <= 0 else clamp((math.log10(dollar_volume) - 5.0) / 4.0 * 100.0)
    turnover_score = scaled(turnover, 0.05, 2.0)
    score, coverage = weighted_available([("Dollar volume", dollar_score, 60), ("Share turnover", turnover_score, 40)])
    label = "Unavailable" if score is None else "Deep" if score >= 75 else "Good" if score >= 55 else "Workable" if score >= 35 else "Thin"
    tone = "muted" if score is None else "positive" if score >= 55 else "warning" if score >= 35 else "negative"
    return metric_card(
        "trading_liquidity", "Trading liquidity", turnover, "percent", label, tone,
        confidence_from_coverage(coverage), "derived",
        [
            evidence("20-session average volume", average, "shares/day", summary.get("session_date"), "derived", "Mean completed daily share volume over the latest 20 sessions."),
            evidence("Average dollar volume", dollar_volume, "USD/day", summary.get("session_date"), "derived", "Average share volume multiplied by the latest completed close."),
            evidence("Latest relative volume", numeric_value(summary.get("relative_volume_20d")), "multiple", summary.get("session_date"), "derived", "Latest completed volume divided by the 20-session average."),
        ],
        {"coverage_percent": coverage, "dollar_volume": dollar_volume, "relative_volume": summary.get("relative_volume_20d"), "score": score, "supply": supply},
    ), score


def share_base_card(
    fundamental_rows: list[dict[str, Any]],
    float_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], float | None]:
    observations = fact_observations(fundamental_rows, ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", "NumberOfSharesIssued"))
    if len(observations) < 2:
        fallback = [
            {"value": first_number(row, "shares_outstanding", "share_class_shares_outstanding"), "period_end_date": row.get("effective_date") or row.get("observed_at_utc")}
            for row in [*float_rows, *market_rows]
        ]
        observations = [row for row in fallback if row.get("value")]
    current = numeric_value(observations[0].get("value")) if observations else None
    prior = observation_at_least_days_earlier(observations, 300)
    change = ratio_percent(current - numeric_value(prior.get("value")), numeric_value(prior.get("value"))) if current is not None and prior and numeric_value(prior.get("value")) else None
    label = "Unavailable" if change is None else "Rapid expansion" if change > 5 else "Expanding" if change > 1 else "Stable" if change >= -1 else "Contracting"
    tone = "muted" if change is None else "negative" if change > 5 else "warning" if change > 1 else "neutral" if change >= -1 else "positive"
    score = None if change is None else clamp(65.0 - change * 7.0)
    return metric_card(
        "share_base", "Share-base pressure", change, "percent", label, tone,
        "high" if len(observations) >= 2 else "insufficient", "derived",
        [
            evidence("Current shares", current, "shares", observations[0].get("period_end_date") if observations else None, "reported", "Latest available SEC or provider shares outstanding."),
            evidence("Comparison shares", numeric_value(prior.get("value")) if prior else None, "shares", prior.get("period_end_date") if prior else None, "reported", "Nearest observation at least 300 days earlier."),
        ],
        {"change_percent": change, "current_shares": current, "prior_shares": numeric_value(prior.get("value")) if prior else None, "score": score},
    ), score


def financial_card_and_scores(fundamental_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, float | None]]:
    revenue = comparable_facts(fundamental_rows, ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue", "RevenueFromContractsWithCustomers"))
    net_income = comparable_facts(fundamental_rows, ("NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"))
    operating_income = comparable_facts(fundamental_rows, ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"))
    operating_cash = comparable_facts(fundamental_rows, ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperations"))
    capex = comparable_facts(fundamental_rows, ("PaymentsToAcquirePropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"))
    cash = latest_fact_value(fundamental_rows, ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"))
    liabilities = latest_fact_value(fundamental_rows, ("Liabilities",))
    debt = latest_total_debt(fact_observations(fundamental_rows, ("LongTermDebtCurrent", "LongTermDebtNoncurrent", "CurrentPortionOfLongTermDebt", "ShorttermBorrowings", "LongtermBorrowings", "Borrowings")))
    revenue_growth = comparable_growth(revenue)
    income_growth = comparable_growth(net_income)
    latest_income = row_value(first(net_income))
    latest_operating = row_value(first(operating_income))
    latest_ocf = row_value(first(operating_cash))
    latest_capex = row_value(first(capex))
    free_cash_flow = latest_ocf - abs(latest_capex) if latest_ocf is not None and latest_capex is not None else None
    profitability, profitability_coverage = weighted_available([
        ("Positive net income", 100.0 if latest_income is not None and latest_income > 0 else 0.0 if latest_income is not None else None, 45),
        ("Positive operating income", 100.0 if latest_operating is not None and latest_operating > 0 else 0.0 if latest_operating is not None else None, 25),
        ("Revenue growth", clamp(50.0 + revenue_growth * 2.5) if revenue_growth is not None else None, 15),
        ("Income growth", clamp(50.0 + income_growth * 1.5) if income_growth is not None else None, 15),
    ])
    cash_generation, cash_coverage = weighted_available([
        ("Operating cash flow", 100.0 if latest_ocf is not None and latest_ocf > 0 else 0.0 if latest_ocf is not None else None, 50),
        ("Free cash flow", 100.0 if free_cash_flow is not None and free_cash_flow > 0 else 0.0 if free_cash_flow is not None else None, 50),
    ])
    balance, balance_coverage = weighted_available([
        ("Cash / debt", scaled(cash / debt if cash is not None and debt else None, 0.25, 2.0), 60),
        ("Cash / liabilities", scaled(cash / liabilities if cash is not None and liabilities else None, 0.05, 0.5), 40),
    ])
    overall, coverage = weighted_available([
        ("Profitability", profitability, 45),
        ("Cash generation", cash_generation, 30),
        ("Balance sheet", balance, 25),
    ])
    label = "Unavailable" if overall is None else "Strong" if overall >= 80 else "Improving" if overall >= 65 and (revenue_growth or 0) > 0 else "Stable" if overall >= 50 else "Weak" if overall >= 30 else "Deteriorating"
    tone = "muted" if overall is None else "positive" if overall >= 65 else "neutral" if overall >= 50 else "warning" if overall >= 30 else "negative"
    card = metric_card(
        "financial_trajectory", "Financial trajectory", overall, "score", label, tone,
        confidence_from_coverage(coverage), "derived",
        [
            evidence("Revenue growth", revenue_growth, "percent", first(revenue).get("period_end_date"), "derived", "Change between the latest two comparable annual or fiscal-period observations."),
            evidence("Net-income growth", income_growth, "percent", first(net_income).get("period_end_date"), "derived", "Change between comparable reported periods; negative-base changes require caution."),
            evidence("Operating cash flow", latest_ocf, "USD", first(operating_cash).get("period_end_date"), "reported", "Latest comparable SEC-reported operating cash flow."),
            evidence("Free cash flow", free_cash_flow, "USD", first(operating_cash).get("period_end_date"), "derived", "Operating cash flow minus capital expenditure."),
            evidence("Cash", cash, "USD", None, "reported", "Latest SEC-reported cash and equivalents."),
            evidence("Debt", debt, "USD", None, "derived", "Current plus noncurrent long-term debt when available."),
        ],
        {"coverage_percent": coverage, "revenue_growth_percent": revenue_growth, "income_growth_percent": income_growth, "score": overall},
    )
    return card, {
        "profitability": profitability if profitability_coverage else None,
        "cash_generation": cash_generation if cash_coverage else None,
        "balance_sheet": balance if balance_coverage else None,
    }


def valuation_card_from_facts(fundamental_rows: list[dict[str, Any]], latest_price: float | None, market_cap: float | None) -> dict[str, Any]:
    eps_rows = comparable_facts(fundamental_rows, ("EarningsPerShareDiluted", "DilutedEarningsLossPerShare", "BasicAndDilutedEarningsLossPerShare"))
    net_rows = comparable_facts(fundamental_rows, ("NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"))
    eps = row_value(first(eps_rows))
    net_income = row_value(first(net_rows))
    pe = latest_price / eps if latest_price is not None and eps is not None and eps > 0 else market_cap / net_income if market_cap is not None and net_income is not None and net_income > 0 else None
    label = "Not meaningful" if (eps is not None and eps <= 0) or (net_income is not None and net_income <= 0) else "Unavailable" if pe is None else "Discount" if pe < 12 else "Moderate" if pe < 22 else "Premium" if pe < 40 else "Very premium"
    # A historical earnings multiple is descriptive, not intrinsically good or bad.
    # Keep every valid valuation regime direction-neutral; the label conveys the regime.
    tone = "muted" if pe is None else "neutral"
    return metric_card(
        "valuation", "Valuation regime", pe, "multiple", label, tone,
        "medium" if pe is not None else "insufficient", "derived",
        [
            evidence("Latest completed price", latest_price, "USD/share", None, "reported", "Latest completed daily close available at the selected clock."),
            evidence("Comparable diluted EPS", eps, "USD/share", first(eps_rows).get("period_end_date"), "reported", "Latest comparable annual SEC diluted EPS; this is not an analyst forward estimate."),
            evidence("Market capitalization", market_cap, "USD", None, "reported", "Provider market snapshot used only when an EPS-derived ratio is unavailable."),
        ],
        {"basis": "latest_price / latest_comparable_annual_eps" if latest_price is not None and eps and eps > 0 else "market_cap / latest_comparable_annual_net_income" if pe is not None else "unavailable", "pe": pe},
    )


def stock_health(
    *,
    as_of: datetime,
    financial_scores: dict[str, float | None],
    liquidity_score: float | None,
    share_score: float | None,
    short_score: float | None,
) -> dict[str, Any]:
    components = [
        ("Profitability", financial_scores.get("profitability"), 25),
        ("Cash generation", financial_scores.get("cash_generation"), 20),
        ("Balance-sheet resilience", financial_scores.get("balance_sheet"), 20),
        ("Share-base discipline", share_score, 15),
        ("Trading liquidity", liquidity_score, 10),
        ("Short / settlement resilience", short_score, 10),
    ]
    score, coverage = weighted_available(components)
    label = health_label(score, coverage)
    tone = health_tone(label)
    return {
        "as_of": as_of.isoformat(),
        "components": [{"label": label_name, "score": value, "weight": weight} for label_name, value, weight in components],
        "confidence": confidence_from_coverage(coverage),
        "coverage_percent": coverage,
        "label": label,
        "score": score if coverage >= 70 else None,
        "tone": tone,
    }


def health_label(score: float | None, coverage: float) -> str:
    if score is None or coverage < 70:
        return "Insufficient evidence"
    return "Robust" if score >= 80 else "Healthy" if score >= 65 else "Mixed" if score >= 45 else "Fragile" if score >= 25 else "Stressed"


def health_tone(label: str) -> str:
    return "positive" if label in {"Robust", "Healthy"} else "neutral" if label == "Mixed" else "warning" if label == "Fragile" else "negative" if label == "Stressed" else "muted"


def estimate_tradable_shares(fundamental_rows: list[dict[str, Any]], price_rows: list[dict[str, Any]], split_rows: list[dict[str, Any]]) -> dict[str, Any]:
    observations = fact_observations(fundamental_rows, ("EntityPublicFloat",))
    for row in observations:
        public_float_value = row_value(row)
        period = parse_date(row.get("period_end_date"))
        price = price_at_or_before(price_rows, period)
        if public_float_value is None or not period or not price:
            continue
        split_factor = split_factor_since(split_rows, period)
        estimate = public_float_value / price * split_factor
        return {
            "method": "sec_public_float_value_divided_by_period_price_split_adjusted",
            "period_end_date": period.isoformat(),
            "price": price,
            "public_float_value": public_float_value,
            "split_factor": split_factor,
            "value": estimate,
            "lower_bound": estimate * 0.65,
            "upper_bound": estimate * 1.35,
        }
    return {}


def market_cap_implied_shares(market_rows: list[dict[str, Any]], price_rows: list[dict[str, Any]]) -> float | None:
    row = first_with_number(market_rows, "market_cap")
    market_cap = first_number(row, "market_cap")
    price = price_at_or_before(price_rows, parse_date(row.get("as_of_date") or row.get("observed_at_utc")))
    return market_cap / price if market_cap and price else None


def best_shares_outstanding(float_rows: list[dict[str, Any]], market_rows: list[dict[str, Any]], fundamental_rows: list[dict[str, Any]]) -> float | None:
    provider = first_number(first_with_number(float_rows, "shares_outstanding"), "shares_outstanding") or first_number(first_with_number(market_rows, "share_class_shares_outstanding", "weighted_shares_outstanding"), "share_class_shares_outstanding", "weighted_shares_outstanding")
    sec = latest_fact_value(fundamental_rows, ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", "NumberOfSharesIssued"))
    return provider or sec


def latest_observation_date(float_rows: list[dict[str, Any]], market_rows: list[dict[str, Any]], fundamental_rows: list[dict[str, Any]]) -> Any:
    row = first_with_number(float_rows, "shares_outstanding")
    if row:
        return row.get("effective_date")
    row = first_with_number(market_rows, "share_class_shares_outstanding", "weighted_shares_outstanding")
    if row:
        return row.get("observed_at_utc")
    rows = fact_observations(fundamental_rows, ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", "NumberOfSharesIssued"))
    return first(rows).get("period_end_date")


def metric_card(card_id: str, title: str, value: float | None, unit: str, label: str, tone: str, confidence: str, method: str, evidence_rows: list[dict[str, Any]], extra: dict[str, Any]) -> dict[str, Any]:
    return {"id": card_id, "title": title, "value": value, "unit": unit, "label": label, "tone": tone, "confidence": confidence, "method": method, "evidence": [row for row in evidence_rows if row.get("value") is not None], **extra}


def evidence(label: str, value: Any, unit: str, observed_at: Any, evidence_type: str, explanation: str) -> dict[str, Any]:
    return {"label": label, "value": value, "unit": unit, "observed_at": observed_at, "type": evidence_type, "explanation": explanation}


def profile_summary(*cards: dict[str, Any]) -> str:
    return " · ".join(f"{card.get('label')} {card.get('title', '').lower()}" for card in cards if card.get("label") and card.get("label") != "Unavailable")


def weighted_available(items: list[tuple[str, float | None, float]]) -> tuple[float | None, float]:
    available = [(clamp(value), weight) for _, value, weight in items if value is not None]
    available_weight = sum(weight for _, weight in available)
    total_weight = sum(weight for _, _, weight in items)
    if not available_weight or not total_weight:
        return None, 0.0
    return sum(value * weight for value, weight in available) / available_weight, available_weight / total_weight * 100.0


def weighted_facets(items: list[tuple[str, float | None, float, float]]) -> tuple[float | None, float]:
    """Weight a facet by both its decision importance and its internal evidence coverage."""
    total_weight = sum(weight for _, _, _, weight in items)
    available = [
        (clamp(score), weight * clamp(coverage) / 100.0)
        for _, score, coverage, weight in items
        if score is not None and coverage > 0
    ]
    evidence_weight = sum(weight for _, weight in available)
    if not evidence_weight or not total_weight:
        return None, 0.0
    return sum(score * weight for score, weight in available) / evidence_weight, evidence_weight / total_weight * 100.0


def scaled(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    return clamp((value - low) / (high - low) * 100.0)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def confidence_from_coverage(coverage: float) -> str:
    return "high" if coverage >= 85 else "medium" if coverage >= 60 else "low" if coverage > 0 else "insufficient"


def ratio_to_percent(value: float | None) -> float | None:
    return value * 100.0 if value is not None else None


def fact_observations(rows: list[dict[str, Any]], tags: tuple[str, ...]) -> list[dict[str, Any]]:
    priorities = {tag: index for index, tag in enumerate(tags)}
    matching = [row for row in rows if str(row.get("tag") or "") in priorities and row_value(row) is not None]
    matching.sort(key=lambda row: (str(row.get("period_end_date") or ""), str(row.get("filed_at_utc") or ""), -priorities[str(row.get("tag") or "")]), reverse=True)
    by_period: dict[tuple[str, str], dict[str, Any]] = {}
    for row in matching:
        key = (str(row.get("period_end_date") or ""), str(row.get("fiscal_period") or ""))
        by_period.setdefault(key, row)
    return list(by_period.values())


def comparable_facts(rows: list[dict[str, Any]], tags: tuple[str, ...]) -> list[dict[str, Any]]:
    observations = fact_observations(rows, tags)
    annual = [row for row in observations if str(row.get("fiscal_period") or "").upper() == "FY" or str(row.get("form_type") or "").upper() in {"10-K", "20-F", "40-F"}]
    return annual if annual else observations


def comparable_growth(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    current, previous = row_value(rows[0]), row_value(rows[1])
    return ratio_percent(current - previous, abs(previous)) if current is not None and previous not in {None, 0} else None


def latest_fact_value(rows: list[dict[str, Any]], tags: tuple[str, ...]) -> float | None:
    return row_value(first(fact_observations(rows, tags)))


def row_value(row: dict[str, Any]) -> float | None:
    return numeric_value(row.get("value"))


def observation_at_least_days_earlier(rows: list[dict[str, Any]], days: int) -> dict[str, Any]:
    if not rows:
        return {}
    current_date = parse_date(rows[0].get("period_end_date"))
    if not current_date:
        return rows[1] if len(rows) > 1 else {}
    return next((row for row in rows[1:] if (other := parse_date(row.get("period_end_date"))) and (current_date - other).days >= days), rows[1] if len(rows) > 1 else {})


def price_at_or_before(rows: list[dict[str, Any]], target: date | None) -> float | None:
    if not target:
        return None
    candidates = []
    for row in rows:
        observed = parse_date(row.get("session_date") or row.get("bar_end"))
        price = numeric_value(row.get("close"))
        if observed and observed <= target and price and price > 0:
            candidates.append((observed, price))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def split_factor_since(rows: list[dict[str, Any]], target: date) -> float:
    factor = 1.0
    for row in rows:
        execution = parse_date(row.get("execution_date"))
        split_from = numeric_value(row.get("split_from"))
        split_to = numeric_value(row.get("split_to"))
        if execution and execution > target and split_from and split_to:
            factor *= split_to / split_from
    return factor


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def build_health_timeline(results: dict[str, list[dict[str, Any]]], cutoff: datetime) -> list[dict[str, Any]]:
    volume_rows = results.get("volume", [])
    session_dates = sorted({observed for row in volume_rows if (observed := parse_date(row.get("session_date") or row.get("bar_end"))) and observed <= cutoff.date()})
    monthly: dict[tuple[int, int], date] = {}
    for observed in session_dates:
        monthly[(observed.year, observed.month)] = observed
    anchors = sorted(set(monthly.values()) | ({session_dates[-1]} if session_dates else set()))
    points: list[dict[str, Any]] = []
    for anchor in anchors:
        available = {name: rows_available_by(rows, anchor, name) for name, rows in results.items()}
        synthesis = synthesize_stock_facts(
            as_of=datetime(anchor.year, anchor.month, anchor.day, 23, 59, 59, tzinfo=UTC),
            borrow=first(available.get("borrow")),
            fails_to_deliver=first(available.get("fails")),
            float_rows=available.get("float", []),
            fundamental_rows=available.get("fundamentals", []),
            market_rows=available.get("market", []),
            reg_sho=first(available.get("reg_sho")),
            short_interest=first(available.get("short_interest")),
            short_volume=aggregate_short_volume(available.get("short_volume", [])),
            split_rows=available.get("splits", []),
            volume_rows=available.get("volume", [])[:MAIN_HISTORY_DAYS],
        )
        health = synthesis["health"]
        if health.get("score") is None:
            continue
        points.append({
            "at": anchor.isoformat(),
            "coverage": health.get("coverage_percent"),
            "label": health.get("label"),
            "tone": health.get("tone"),
            "value": health.get("score"),
        })
    return points


def rows_available_by(rows: list[dict[str, Any]], anchor: date, kind: str) -> list[dict[str, Any]]:
    # Use the first date on which the observation was knowable, not the date on
    # which a historical backfill happened to be inserted into ClickHouse. This
    # keeps the monthly health series causal and prevents later loads from
    # erasing otherwise valid historical evidence.
    fields = {
        "borrow": ("observed_at_utc",),
        # The FTD table does not expose a publication timestamp. inserted_at is
        # therefore the only safe availability boundary; settlement_date alone
        # would introduce look-ahead during a historical replay.
        "fails": ("inserted_at",),
        "float": ("effective_date",),
        "fundamentals": ("filed_at_utc", "recorded_at_utc"),
        "market": ("observed_at_utc",),
        "reg_sho": ("threshold_date",),
        "short_interest": ("published_at_utc", "publication_date", "inserted_at"),
        "short_volume": ("trade_date",),
        "splits": ("execution_date",),
        "volume": ("bar_end", "session_date"),
    }.get(kind, ("inserted_at",))
    kept: list[tuple[date, dict[str, Any]]] = []
    for row in rows:
        observed = next((parsed for field in fields if (parsed := parse_date(row.get(field)))), None)
        if observed and observed <= anchor:
            kept.append((observed, row))
    return [row for _, row in sorted(kept, key=lambda item: item[0], reverse=True)]


def health_comparisons(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points:
        return []
    latest_date = parse_date(points[-1].get("at"))
    if not latest_date:
        return []
    comparisons = []
    for label, days in (("1 month", 30), ("3 months", 90), ("1 year", 365)):
        target_ordinal = latest_date.toordinal() - days
        point = min(points, key=lambda item: abs((parse_date(item.get("at")) or latest_date).toordinal() - target_ordinal))
        comparisons.append({"period": label, "at": point.get("at"), "score": point.get("value"), "label": point.get("label"), "tone": point.get("tone")})
    return comparisons


def metric_changes(
    *,
    market_rows: list[dict[str, Any]],
    float_rows: list[dict[str, Any]],
    short_interest_rows: list[dict[str, Any]],
    short_volume_rows: list[dict[str, Any]],
    borrow_rows: list[dict[str, Any]],
    volume_rows: list[dict[str, Any]],
    fundamental_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    add_change(changes, "market_cap", market_rows, lambda row: numeric_value(row.get("market_cap")), "observed_at_utc")
    add_change(changes, "free_float", float_rows, lambda row: numeric_value(row.get("free_float")), "effective_date")
    shares_rows = float_rows if first_number(first(float_rows), "shares_outstanding") is not None else market_rows
    shares_field = "shares_outstanding" if shares_rows is float_rows else "share_class_shares_outstanding"
    add_change(changes, "shares_outstanding", shares_rows, lambda row: numeric_value(row.get(shares_field)), "effective_date" if shares_rows is float_rows else "observed_at_utc")
    add_change(changes, "short_interest", short_interest_rows, lambda row: numeric_value(row.get("short_interest")), "settlement_date")
    add_change(changes, "days_to_cover", short_interest_rows, lambda row: numeric_value(row.get("days_to_cover")), "settlement_date")
    add_change(changes, "shortable_shares", borrow_rows, lambda row: numeric_value(row.get("shortable_shares")), "observed_at_utc")
    add_change(changes, "indicative_borrow_rate", borrow_rows, lambda row: numeric_value(row.get("indicative_borrow_rate")), "observed_at_utc")
    add_change(changes, "fee_rate", borrow_rows, lambda row: numeric_value(row.get("fee_rate")), "observed_at_utc")
    daily_summaries = [aggregate_daily_volume(volume_rows, offset) for offset in range(min(2, len(volume_rows)))]
    add_change(changes, "daily_volume", daily_summaries, lambda row: numeric_value(row.get("latest_volume")), "session_date")
    add_change(changes, "relative_volume_20d", daily_summaries, lambda row: numeric_value(row.get("relative_volume_20d")), "session_date")
    short_summaries = [aggregate_short_volume(short_volume_rows, offset) for offset in range(min(2, len(short_volume_rows)))]
    add_change(changes, "short_volume_ratio", short_summaries, lambda row: numeric_value(row.get("latest_short_volume_ratio")), "latest_trade_date")
    add_change(changes, "short_volume_ratio_20d", short_summaries, lambda row: numeric_value(row.get("ratio_20d")), "latest_trade_date")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in fundamental_rows:
        grouped.setdefault(str(row.get("tag") or ""), []).append(row)
    for _, alternatives in FUNDAMENTAL_TAGS:
        tag = next((candidate for candidate in alternatives if grouped.get(candidate)), "")
        if tag:
            add_change(changes, f"fundamental:{tag.lower()}", grouped[tag], lambda row: numeric_value(row.get("value")), "period_end_date")
    return changes


def add_change(
    target: dict[str, dict[str, Any]],
    key: str,
    rows: list[dict[str, Any]],
    getter: Callable[[dict[str, Any]], float | None],
    date_field: str,
) -> None:
    observations = [(getter(row), row.get(date_field)) for row in rows]
    observations = [(value, observed_at) for value, observed_at in observations if value is not None]
    if not observations:
        return
    current, current_at = observations[0]
    previous, previous_at = observations[1] if len(observations) > 1 else (None, None)
    delta = current - previous if previous is not None else None
    target[key] = {
        "current": current,
        "current_at": current_at,
        "delta": delta,
        "direction": "up" if delta is not None and delta > 0 else "down" if delta is not None and delta < 0 else "flat" if delta == 0 else "unavailable",
        "previous": previous,
        "previous_at": previous_at,
    }


def history_points(
    rows: list[dict[str, Any]],
    value_field: str,
    date_field: str,
    *,
    extra_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for row in reversed(rows):
        value = numeric_value(row.get(value_field))
        observed_at = str(row.get(date_field) or "").strip()
        if value is None or not observed_at:
            continue
        by_date[observed_at] = {"at": observed_at, "value": value, **{field: row.get(field) for field in extra_fields if row.get(field) is not None}}
    return sorted(by_date.values(), key=lambda point: str(point["at"]))


def short_volume_history_points(rows: list[dict[str, Any]], *, rolling: bool) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
    if not rolling:
        return [
            {"at": row.get("trade_date"), "value": value * 100.0}
            for row in ordered
            if (value := numeric_value(row.get("short_volume_ratio"))) is not None
        ]
    points: list[dict[str, Any]] = []
    for index, row in enumerate(ordered):
        window = ordered[max(0, index - 19): index + 1]
        short_total = sum(numeric_value(item.get("short_volume")) or 0.0 for item in window)
        total = sum(numeric_value(item.get("total_volume")) or 0.0 for item in window)
        if total:
            points.append({"at": row.get("trade_date"), "value": short_total / total * 100.0})
    return points


def daily_volume_history_points(rows: list[dict[str, Any]], *, relative: bool) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("bar_end") or row.get("session_date") or ""))
    points: list[dict[str, Any]] = []
    for index, row in enumerate(ordered):
        value = numeric_value(row.get("size_sum"))
        if value is None:
            continue
        if relative:
            window = [numeric_value(item.get("size_sum")) for item in ordered[max(0, index - 19):index + 1]]
            window = [item for item in window if item is not None]
            if not window:
                continue
            value = value / (sum(window) / len(window))
        points.append({"at": row.get("session_date") or row.get("bar_end"), "value": value})
    return points


def numeric_value(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else None


def company_country_code(identity: dict[str, Any]) -> str | None:
    domicile = str(identity.get("domicile_country_code") or "").strip().upper()
    if domicile:
        return domicile
    incorporation = str(identity.get("state_of_incorporation") or "").strip().upper()
    if incorporation in US_INCORPORATION_CODES:
        return "US"
    return SEC_INCORPORATION_COUNTRY_CODES.get(incorporation)


def company_country_source(identity: dict[str, Any]) -> str | None:
    if str(identity.get("domicile_country_code") or "").strip():
        return "issuer domicile"
    incorporation = str(identity.get("state_of_incorporation") or "").strip().upper()
    if incorporation in US_INCORPORATION_CODES:
        return "SEC incorporation state"
    if incorporation in SEC_INCORPORATION_COUNTRY_CODES:
        return "SEC incorporation jurisdiction"
    return None


def freshness_status(as_of: datetime, available_at: Any) -> dict[str, Any] | None:
    observed = parse_datetime(available_at)
    if not observed or observed > as_of:
        return None
    age_seconds = (as_of - observed).total_seconds()
    if age_seconds <= 86_400:
        return {"available_at": observed.isoformat(), "status": "new"}
    if age_seconds <= 7 * 86_400:
        return {"available_at": observed.isoformat(), "status": "recent"}
    return None


def fact_freshness(
    *,
    cutoff: datetime,
    borrow: dict[str, Any],
    float_row: dict[str, Any],
    fundamental_rows: list[dict[str, Any]],
    market: dict[str, Any],
    short_interest: dict[str, Any],
    short_volume: dict[str, Any],
    volume: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, Any] = {
        "market_cap": market.get("observed_at_utc"),
        "free_float": float_row.get("effective_date"),
        "shares_outstanding": float_row.get("effective_date") or market.get("observed_at_utc"),
        "daily_volume": volume.get("session_date"),
        "relative_volume_20d": volume.get("session_date"),
        "short_interest": short_interest.get("published_at_utc") or short_interest.get("publication_date") or short_interest.get("inserted_at"),
        "days_to_cover": short_interest.get("published_at_utc") or short_interest.get("publication_date") or short_interest.get("inserted_at"),
        "short_volume_ratio": short_volume.get("latest_trade_date"),
        "short_volume_ratio_20d": short_volume.get("latest_trade_date"),
        "borrow": borrow.get("observed_at_utc"),
        "shortable_shares": borrow.get("observed_at_utc"),
        "indicative_borrow_rate": borrow.get("observed_at_utc"),
        "fee_rate": borrow.get("observed_at_utc"),
    }
    for row in fundamental_rows:
        tag = str(row.get("tag") or "").lower()
        if tag:
            candidates[f"fundamental:{tag}"] = row.get("filed_at_utc") or row.get("recorded_at_utc")
    return {key: status for key, available_at in candidates.items() if (status := freshness_status(cutoff, available_at))}


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text[:10]}T00:00:00+00:00")
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def source_inventory(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    authorities = {
        "borrow": ("IBKR borrow", "q_live.market_security_borrow_v1"),
        "classifications": ("Reference classification", "q_live.market_security_classification_v1"),
        "corporate": ("Corporate actions", "q_live.market_stock_split_v1 / market_cash_dividend_v1"),
        "fails_to_deliver": ("SEC fails to deliver", "q_live.market_fails_to_deliver_v1"),
        "float": ("Massive float", "q_live.market_security_float_v1"),
        "fundamentals": ("SEC XBRL", "q_live.sec_xbrl_company_fact_v3"),
        "identifiers": ("Canonical identifiers", "q_live.id_*_identifier_v1"),
        "market": ("Massive market snapshot", "q_live.market_security_market_snapshot_v1"),
        "reg_sho": ("Reg SHO threshold", "q_live.market_reg_sho_threshold_v1"),
        "short_interest": ("Massive short interest", "q_live.market_short_interest_v1"),
        "short_volume": ("FINRA short volume", "q_live.market_short_volume_v1"),
        "volume": ("QMD daily bars", f"{historical_database()}.macro_bars_by_time_symbol"),
    }
    return [
        {"available": bool(results.get(key)), "label": label, "table": table}
        for key, (label, table) in authorities.items()
    ]


def issuer_cik(issuer_id: str) -> str:
    match = re.fullmatch(r"issuer:cik:(\d{1,10})", issuer_id)
    return match.group(1).zfill(10) if match else ""


def clickhouse_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def first(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    return rows[0] if rows else {}


def first_with_number(rows: list[dict[str, Any]], *keys: str) -> dict[str, Any]:
    return next((row for row in rows if first_number(row, *keys) is not None), {})


def first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def ratio_percent(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator * 100.0 if numerator is not None and denominator else None


def difference(current: dict[str, Any], previous: dict[str, Any], key: str) -> float | None:
    try:
        return float(current[key]) - float(previous[key])
    except (KeyError, TypeError, ValueError):
        return None
