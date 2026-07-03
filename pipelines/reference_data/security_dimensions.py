from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


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
    description: str
    default_plot: bool = False


SEC_XBRL_DIMENSIONS: dict[str, tuple[SecurityDimension, str, str]] = {
    "sec_entity_common_stock_shares_outstanding": (
        SecurityDimension(
            code="sec_entity_common_stock_shares_outstanding",
            label="SEC entity common stock shares outstanding",
            group="share_supply",
            source_system="sec",
            source_table="sec_xbrl_company_fact_v1",
            value_unit="shares",
            description="SEC XBRL EntityCommonStockSharesOutstanding observations, timed by filed_at_utc.",
            default_plot=True,
        ),
        "EntityCommonStockSharesOutstanding",
        "shares",
    ),
    "sec_common_stock_shares_outstanding": (
        SecurityDimension(
            code="sec_common_stock_shares_outstanding",
            label="SEC common stock shares outstanding",
            group="share_supply",
            source_system="sec",
            source_table="sec_xbrl_company_fact_v1",
            value_unit="shares",
            description="SEC XBRL CommonStockSharesOutstanding observations, timed by filed_at_utc.",
            default_plot=True,
        ),
        "CommonStockSharesOutstanding",
        "shares",
    ),
    "sec_weighted_avg_basic_shares": (
        SecurityDimension(
            code="sec_weighted_avg_basic_shares",
            label="SEC weighted average basic shares",
            group="share_supply",
            source_system="sec",
            source_table="sec_xbrl_company_fact_v1",
            value_unit="shares",
            description="SEC XBRL WeightedAverageNumberOfSharesOutstandingBasic observations, timed by filed_at_utc.",
            default_plot=True,
        ),
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "shares",
    ),
    "sec_weighted_avg_diluted_shares": (
        SecurityDimension(
            code="sec_weighted_avg_diluted_shares",
            label="SEC weighted average diluted shares",
            group="share_supply",
            source_system="sec",
            source_table="sec_xbrl_company_fact_v1",
            value_unit="shares",
            description="SEC XBRL WeightedAverageNumberOfDilutedSharesOutstanding observations, timed by filed_at_utc.",
            default_plot=True,
        ),
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "shares",
    ),
    "sec_entity_public_float_usd": (
        SecurityDimension(
            code="sec_entity_public_float_usd",
            label="SEC entity public float USD",
            group="float",
            source_system="sec",
            source_table="sec_xbrl_company_fact_v1",
            value_unit="USD",
            description="SEC XBRL EntityPublicFloat observations in dollars, timed by filed_at_utc.",
            default_plot=True,
        ),
        "EntityPublicFloat",
        "USD",
    ),
}


MASSIVE_FLOAT_DIMENSION = SecurityDimension(
    code="massive_free_float",
    label="Massive free float",
    group="float",
    source_system="massive",
    source_table="market_security_float_v1",
    value_unit="shares",
    description="Massive free_float observations, timed by effective_date at midnight UTC.",
    default_plot=True,
)


MASSIVE_SHARES_OUTSTANDING_DIMENSION = SecurityDimension(
    code="massive_shares_outstanding",
    label="Massive shares outstanding",
    group="share_supply",
    source_system="massive",
    source_table="market_security_float_v1",
    value_unit="shares",
    description="Massive shares_outstanding observations when present in market_security_float_v1.",
    default_plot=True,
)


MASSIVE_SNAPSHOT_DIMENSIONS: dict[str, tuple[SecurityDimension, str]] = {
    "massive_share_class_shares_outstanding": (
        SecurityDimension(
            code="massive_share_class_shares_outstanding",
            label="Massive share-class shares outstanding",
            group="share_supply",
            source_system="massive",
            source_table="market_security_market_snapshot_v1",
            value_unit="shares",
            description="Massive ticker-detail share_class_shares_outstanding observations, timed by observed_at_utc.",
            default_plot=True,
        ),
        "share_class_shares_outstanding",
    ),
    "massive_weighted_shares_outstanding": (
        SecurityDimension(
            code="massive_weighted_shares_outstanding",
            label="Massive weighted shares outstanding",
            group="share_supply",
            source_system="massive",
            source_table="market_security_market_snapshot_v1",
            value_unit="shares",
            description="Massive ticker-detail weighted_shares_outstanding observations, timed by observed_at_utc.",
            default_plot=False,
        ),
        "weighted_shares_outstanding",
    ),
}


def dimension_registry() -> dict[str, SecurityDimension]:
    registry = {code: spec for code, (spec, _tag, _unit) in SEC_XBRL_DIMENSIONS.items()}
    registry[MASSIVE_FLOAT_DIMENSION.code] = MASSIVE_FLOAT_DIMENSION
    registry[MASSIVE_SHARES_OUTSTANDING_DIMENSION.code] = MASSIVE_SHARES_OUTSTANDING_DIMENSION
    registry.update({code: spec for code, (spec, _column) in MASSIVE_SNAPSHOT_DIMENSIONS.items()})
    return registry


def default_dimension_codes() -> tuple[str, ...]:
    return tuple(code for code, spec in dimension_registry().items() if spec.default_plot)


def security_dimension_observations_sql(
    *,
    database: str,
    ticker: str = "",
    symbol_id: str = "",
    dimension_codes: Iterable[str] | None = None,
    start_date: str = "2019-01-01",
    end_date: str = "2100-01-01",
) -> str:
    db = quote_ident(database)
    where = symbol_filter_sql(ticker=ticker, symbol_id=symbol_id)
    codes = tuple(dimension_codes or default_dimension_codes())
    unknown = sorted(set(codes) - set(dimension_registry()))
    if unknown:
        raise ValueError(f"Unknown security dimension code(s): {', '.join(unknown)}")
    selects: list[str] = []
    for code in codes:
        if code in SEC_XBRL_DIMENSIONS:
            selects.append(sec_xbrl_dimension_sql(db, code, where, start_date, end_date))
        elif code == MASSIVE_FLOAT_DIMENSION.code:
            selects.append(massive_float_dimension_sql(db, where, start_date, end_date))
        elif code == MASSIVE_SHARES_OUTSTANDING_DIMENSION.code:
            selects.append(massive_shares_outstanding_dimension_sql(db, where, start_date, end_date))
        elif code in MASSIVE_SNAPSHOT_DIMENSIONS:
            selects.append(massive_snapshot_dimension_sql(db, code, where, start_date, end_date))
    if not selects:
        raise ValueError("At least one dimension code is required.")
    return "\nUNION ALL\n".join(selects) + "\nORDER BY observed_at_utc, dimension_code, source_priority FORMAT JSONEachRow"


def security_dimension_observations_sql_for_context(
    *,
    database: str,
    context: SecurityDimensionContext,
    dimension_codes: Iterable[str] | None = None,
    start_date: str = "2019-01-01",
    end_date: str = "2100-01-01",
) -> str:
    db = quote_ident(database)
    codes = tuple(dimension_codes or default_dimension_codes())
    unknown = sorted(set(codes) - set(dimension_registry()))
    if unknown:
        raise ValueError(f"Unknown security dimension code(s): {', '.join(unknown)}")
    selects: list[str] = []
    for code in codes:
        if code in SEC_XBRL_DIMENSIONS:
            selects.append(sec_xbrl_dimension_sql_for_context(db, context, code, start_date, end_date))
        elif code == MASSIVE_FLOAT_DIMENSION.code:
            selects.append(massive_float_dimension_sql_for_context(db, context, start_date, end_date))
        elif code == MASSIVE_SHARES_OUTSTANDING_DIMENSION.code:
            selects.append(massive_shares_outstanding_dimension_sql_for_context(db, context, start_date, end_date))
        elif code in MASSIVE_SNAPSHOT_DIMENSIONS:
            selects.append(massive_snapshot_dimension_sql_for_context(db, context, code, start_date, end_date))
    if not selects:
        raise ValueError("At least one dimension code is required.")
    return "\nUNION ALL\n".join(selects) + "\nORDER BY observed_at_utc, dimension_code, source_priority FORMAT JSONEachRow"


def resolve_security_dimension_context_sql(*, database: str, ticker: str = "", symbol_id: str = "") -> str:
    db = quote_ident(database)
    if not ticker and not symbol_id:
        raise ValueError("ticker or symbol_id is required.")
    if symbol_id:
        condition = f"b.symbol_id = {sql_string(symbol_id)}"
    else:
        condition = f"upper(b.ticker) = {sql_string(ticker.upper())} AND position(b.symbol_id, ':usd:') > 0 AND endsWith(b.symbol_id, ':stk')"
    return f"""
WITH latest_universe AS
(
    SELECT
        symbol_id,
        argMax(ticker, universe_date) AS universe_ticker,
        argMax(listing_id, universe_date) AS universe_listing_id,
        argMax(security_id, universe_date) AS universe_security_id,
        argMax(issuer_id, universe_date) AS universe_issuer_id,
        argMax(is_tradable, universe_date) AS is_tradable,
        max(universe_date) AS max_universe_date
    FROM {db}.feature_tradable_universe_v1 FINAL
    WHERE {'symbol_id = ' + sql_string(symbol_id) if symbol_id else 'upper(ticker) = ' + sql_string(ticker.upper())}
    GROUP BY symbol_id
),
bridge AS
(
    SELECT
        b.ticker,
        b.cik,
        b.symbol_id,
        b.listing_id,
        b.security_id,
        b.issuer_id,
        ifNull(u.is_tradable, 0) AS is_tradable,
        if(u.symbol_id != '', 1, 0) AS in_universe
    FROM {db}.id_sec_market_bridge_v1 AS b FINAL
    LEFT JOIN latest_universe AS u ON u.symbol_id = b.symbol_id
    WHERE {condition}
      AND b.mapping_status = 'active'
)
SELECT ticker, cik, symbol_id, listing_id, security_id, issuer_id
FROM bridge
ORDER BY is_tradable DESC, in_universe DESC, symbol_id ASC
LIMIT 1
FORMAT JSONEachRow
""".strip()


def symbol_filter_sql(*, ticker: str, symbol_id: str) -> str:
    if symbol_id:
        return f"b.symbol_id = {sql_string(symbol_id)}"
    if ticker:
        return f"upper(b.ticker) = {sql_string(ticker.upper())} AND position(b.symbol_id, ':usd:') > 0 AND endsWith(b.symbol_id, ':stk')"
    raise ValueError("ticker or symbol_id is required.")


def sec_xbrl_dimension_sql(db: str, code: str, bridge_where: str, start_date: str, end_date: str) -> str:
    spec, tag, unit = SEC_XBRL_DIMENSIONS[code]
    return f"""
SELECT
    b.symbol_id AS symbol_id,
    upper(b.ticker) AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(f.filed_at_utc, 3, 'UTC') AS observed_at_utc,
    f.period_end_date AS period_end_date,
    toFloat64(f.value) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    f.accession_number AS source_event_id,
    f.form_type AS source_form,
    10 AS source_priority
FROM {db}.sec_xbrl_company_fact_v1 AS f FINAL
INNER JOIN {db}.id_sec_market_bridge_v1 AS b FINAL
    ON b.cik = f.cik
WHERE {bridge_where}
  AND b.mapping_status = 'active'
  AND (b.ambiguity_status = 'unique' OR ({bridge_where}))
  AND f.tag = {sql_string(tag)}
  AND f.unit_code = {sql_string(unit)}
  AND f.value > 0
  AND f.filed_at_utc >= toDateTime64({sql_string(start_date + " 00:00:00")}, 3, 'UTC')
  AND f.filed_at_utc < toDateTime64({sql_string(end_date + " 00:00:00")}, 3, 'UTC')
""".strip()


def sec_xbrl_dimension_sql_for_context(db: str, context: SecurityDimensionContext, code: str, start_date: str, end_date: str) -> str:
    spec, tag, unit = SEC_XBRL_DIMENSIONS[code]
    return f"""
SELECT
    {sql_string(context.symbol_id)} AS symbol_id,
    {sql_string(context.ticker.upper())} AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(f.filed_at_utc, 3, 'UTC') AS observed_at_utc,
    f.period_end_date AS period_end_date,
    toFloat64(f.value) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    f.accession_number AS source_event_id,
    f.form_type AS source_form,
    10 AS source_priority
FROM {db}.sec_xbrl_company_fact_v1 AS f FINAL
WHERE f.cik = {sql_string(context.cik)}
  AND f.tag = {sql_string(tag)}
  AND f.unit_code = {sql_string(unit)}
  AND f.value > 0
  AND f.filed_at_utc >= toDateTime64({sql_string(start_date + " 00:00:00")}, 3, 'UTC')
  AND f.filed_at_utc < toDateTime64({sql_string(end_date + " 00:00:00")}, 3, 'UTC')
""".strip()


def massive_float_dimension_sql(db: str, bridge_where: str, start_date: str, end_date: str) -> str:
    spec = MASSIVE_FLOAT_DIMENSION
    return f"""
SELECT
    b.symbol_id AS symbol_id,
    upper(b.ticker) AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(toDateTime(m.effective_date), 3, 'UTC') AS observed_at_utc,
    m.effective_date AS period_end_date,
    toFloat64(m.free_float) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    m.security_float_id AS source_event_id,
    '' AS source_form,
    20 AS source_priority
FROM {db}.market_security_float_v1 AS m FINAL
INNER JOIN {db}.id_sec_market_bridge_v1 AS b FINAL
    ON b.symbol_id = m.symbol_id
WHERE {bridge_where}
  AND b.mapping_status = 'active'
  AND (b.ambiguity_status = 'unique' OR ({bridge_where}))
  AND m.free_float IS NOT NULL
  AND m.free_float > 0
  AND m.effective_date >= toDate({sql_string(start_date)})
  AND m.effective_date < toDate({sql_string(end_date)})
""".strip()


def massive_float_dimension_sql_for_context(db: str, context: SecurityDimensionContext, start_date: str, end_date: str) -> str:
    spec = MASSIVE_FLOAT_DIMENSION
    return f"""
SELECT
    {sql_string(context.symbol_id)} AS symbol_id,
    {sql_string(context.ticker.upper())} AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(toDateTime(m.effective_date), 3, 'UTC') AS observed_at_utc,
    m.effective_date AS period_end_date,
    toFloat64(m.free_float) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    m.security_float_id AS source_event_id,
    '' AS source_form,
    20 AS source_priority
FROM {db}.market_security_float_v1 AS m FINAL
WHERE m.symbol_id = {sql_string(context.symbol_id)}
  AND m.free_float IS NOT NULL
  AND m.free_float > 0
  AND m.effective_date >= toDate({sql_string(start_date)})
  AND m.effective_date < toDate({sql_string(end_date)})
""".strip()


def massive_shares_outstanding_dimension_sql(db: str, bridge_where: str, start_date: str, end_date: str) -> str:
    spec = MASSIVE_SHARES_OUTSTANDING_DIMENSION
    return f"""
SELECT
    b.symbol_id AS symbol_id,
    upper(b.ticker) AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(toDateTime(m.effective_date), 3, 'UTC') AS observed_at_utc,
    m.effective_date AS period_end_date,
    toFloat64(m.shares_outstanding) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    m.security_float_id AS source_event_id,
    '' AS source_form,
    21 AS source_priority
FROM {db}.market_security_float_v1 AS m FINAL
INNER JOIN {db}.id_sec_market_bridge_v1 AS b FINAL
    ON b.symbol_id = m.symbol_id
WHERE {bridge_where}
  AND b.mapping_status = 'active'
  AND (b.ambiguity_status = 'unique' OR ({bridge_where}))
  AND m.shares_outstanding IS NOT NULL
  AND m.shares_outstanding > 0
  AND m.effective_date >= toDate({sql_string(start_date)})
  AND m.effective_date < toDate({sql_string(end_date)})
""".strip()


def massive_shares_outstanding_dimension_sql_for_context(db: str, context: SecurityDimensionContext, start_date: str, end_date: str) -> str:
    spec = MASSIVE_SHARES_OUTSTANDING_DIMENSION
    return f"""
SELECT
    {sql_string(context.symbol_id)} AS symbol_id,
    {sql_string(context.ticker.upper())} AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(toDateTime(m.effective_date), 3, 'UTC') AS observed_at_utc,
    m.effective_date AS period_end_date,
    toFloat64(m.shares_outstanding) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    m.security_float_id AS source_event_id,
    '' AS source_form,
    21 AS source_priority
FROM {db}.market_security_float_v1 AS m FINAL
WHERE m.symbol_id = {sql_string(context.symbol_id)}
  AND m.shares_outstanding IS NOT NULL
  AND m.shares_outstanding > 0
  AND m.effective_date >= toDate({sql_string(start_date)})
  AND m.effective_date < toDate({sql_string(end_date)})
""".strip()


def massive_snapshot_dimension_sql(db: str, code: str, bridge_where: str, start_date: str, end_date: str) -> str:
    spec, column = MASSIVE_SNAPSHOT_DIMENSIONS[code]
    return f"""
SELECT
    b.symbol_id AS symbol_id,
    upper(b.ticker) AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(s.observed_at_utc, 3, 'UTC') AS observed_at_utc,
    s.as_of_date AS period_end_date,
    toFloat64(s.{column}) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    s.security_market_snapshot_id AS source_event_id,
    '' AS source_form,
    22 AS source_priority
FROM {db}.market_security_market_snapshot_v1 AS s FINAL
INNER JOIN {db}.id_sec_market_bridge_v1 AS b FINAL
    ON b.symbol_id = s.symbol_id
WHERE {bridge_where}
  AND b.mapping_status = 'active'
  AND (b.ambiguity_status = 'unique' OR ({bridge_where}))
  AND s.{column} IS NOT NULL
  AND s.{column} > 0
  AND s.observed_at_utc >= toDateTime64({sql_string(start_date + " 00:00:00")}, 3, 'UTC')
  AND s.observed_at_utc < toDateTime64({sql_string(end_date + " 00:00:00")}, 3, 'UTC')
""".strip()


def massive_snapshot_dimension_sql_for_context(db: str, context: SecurityDimensionContext, code: str, start_date: str, end_date: str) -> str:
    spec, column = MASSIVE_SNAPSHOT_DIMENSIONS[code]
    return f"""
SELECT
    {sql_string(context.symbol_id)} AS symbol_id,
    {sql_string(context.ticker.upper())} AS ticker,
    {sql_string(spec.code)} AS dimension_code,
    {sql_string(spec.label)} AS dimension_label,
    {sql_string(spec.group)} AS dimension_group,
    toDateTime64(s.observed_at_utc, 3, 'UTC') AS observed_at_utc,
    s.as_of_date AS period_end_date,
    toFloat64(s.{column}) AS value,
    {sql_string(spec.value_unit)} AS value_unit,
    {sql_string(spec.source_system)} AS source_system,
    {sql_string(spec.source_table)} AS source_table,
    s.security_market_snapshot_id AS source_event_id,
    '' AS source_form,
    22 AS source_priority
FROM {db}.market_security_market_snapshot_v1 AS s FINAL
WHERE s.symbol_id = {sql_string(context.symbol_id)}
  AND s.{column} IS NOT NULL
  AND s.{column} > 0
  AND s.observed_at_utc >= toDateTime64({sql_string(start_date + " 00:00:00")}, 3, 'UTC')
  AND s.observed_at_utc < toDateTime64({sql_string(end_date + " 00:00:00")}, 3, 'UTC')
""".strip()


def quote_ident(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"Unsafe ClickHouse identifier: {value!r}")
    return value


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
