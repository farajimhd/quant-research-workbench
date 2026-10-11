"""Producer-owned persisted liquidity resolution upgrade, from pinned ARTE.

The base100ms market/eligible-price certificates are required input authorities.
This module only builds reviewable SQL; it performs no I/O or installation.
Backtest reads the completed product and never calls these materializers.
"""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass
import re
from uuid import UUID

from pipelines.market_sip.events.market_day_sql import literal
from src.trading_runtime.arte_journal_schema import TableContract

PREFIX = (("source_build_id", "String"), ("session_date", "Date"),
          ("ticker", "LowCardinality(String)"), ("source_attempt_id", "UUID"),
          ("derivation_attempt_id", "UUID"), ("resolution_ms", "UInt32"))
COUNTERS = ("event_count", "source_trade_count", "quote_event_count",
    "reporting_delayed_trades", "volume_ineligible_trades", "price_ineligible_trades",
    "execution_ineligible_trades", "pre_0405_trades", "pre_0405_volume_eligible_trades",
    "invalid_trade_values", "trade_count")
SUMS = ("volume", "notional", "execution_volume", "execution_notional")
CARRY = (("quote_timestamp_us", "UInt64"), ("bid_int", "UInt64"), ("ask_int", "UInt64"),
    ("bid_size", "Float64"), ("ask_size", "Float64"),
    ("cumulative_volume", "Float64"), ("cumulative_notional", "Float64"),
    ("cumulative_execution_volume", "Float64"), ("cumulative_execution_notional", "Float64"),
    ("execution_vwap", "Float64"), ("spread", "Float64"), ("quote_valid", "UInt8"))
ORDER = "source_build_id,session_date,ticker,source_attempt_id,derivation_attempt_id,resolution_ms"
LIQUIDITY = TableContract("liquidity_bars_v2", PREFIX + (("bucket_index", "UInt32"),
    ("first_event_us", "UInt64"), ("last_event_us", "UInt64")) +
    tuple((name, "UInt64") for name in COUNTERS) +
    tuple((name, "UInt64") for name in ("open_int", "high_int", "low_int", "close_int")) +
    tuple((name, "Float64") for name in SUMS) +
    tuple((name, "UInt8") for name in ("price_valid", "extremes_valid", "volume_valid")) + CARRY,
    "toYYYYMM(session_date)", ORDER + ",bucket_index")
PRICE = TableContract("liquidity_execution_price_bars_v2", PREFIX +
    (("bucket_index", "UInt32"), ("price_int", "UInt64"), ("execution_volume", "Float64")),
    "toYYYYMM(session_date)", ORDER + ",bucket_index,price_int")
COVERAGE = TableContract("liquidity_bars_coverage_v2", PREFIX +
    (("source_price_attempt_id", "UUID"), ("source_market_unit_hash", "FixedString(64)"),
     ("source_price_unit_hash", "FixedString(64)"),
     ("producer_code_hash", "FixedString(64)"),
     ("liquidity_row_count", "UInt32"), ("price_row_count", "UInt64"),
     ("total_execution_volume", "Float64"),
     ("liquidity_content_hash", "FixedString(64)"), ("price_content_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)"), ("certified_at", "DateTime64(6, 'UTC')")),
    "toYYYYMM(session_date)", ORDER)
TABLES = (LIQUIDITY, PRICE, COVERAGE)


@dataclass(frozen=True)
class LiquiditySourceUnit:
    build_id: str
    session_date: date
    ticker: str
    source_attempt_id: str
    source_price_attempt_id: str
    source_market_unit_hash: str
    source_price_unit_hash: str

    def __post_init__(self):
        if (not isinstance(self.build_id, str) or not 1 <= len(self.build_id) <= 128
                or type(self.session_date) is not date
                or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", self.ticker or "") is None):
            raise ValueError("Liquidity upgrade requires a pinned market source unit")
        for value in (self.source_attempt_id, self.source_price_attempt_id):
            if str(UUID(value)) != value:
                raise ValueError("Liquidity upgrade UUID must be canonical")
        for value in (self.source_market_unit_hash, self.source_price_unit_hash):
            if re.fullmatch(r"[0-9a-f]{64}", value or "") is None:
                raise ValueError("Liquidity upgrade needs both verified parent unit hashes")


def ddl():
    return tuple(table.ddl() for table in TABLES)


def source_scope(unit, *, price=False, qualifier=""):
    if type(unit) is not LiquiditySourceUnit:
        raise TypeError("Liquidity SQL requires its typed source unit")
    if qualifier not in ("", "s."):
        raise ValueError("Unknown producer SQL qualifier")
    scope = (f"{qualifier}{'source_build_id' if price else 'build_id'}={literal(unit.build_id)} "
        f"AND {qualifier}session_date=toDate({literal(unit.session_date.isoformat())}) "
        f"AND {qualifier}ticker={literal(unit.ticker)} "
        f"AND {qualifier}{'source_attempt_id' if price else 'attempt_id'}=toUUID({literal(unit.source_attempt_id)})")
    if price:
        scope += f" AND {qualifier}derivation_attempt_id=toUUID({literal(unit.source_price_attempt_id)})"
    else:
        scope += f" AND {qualifier}resolution_ms=100"
    return scope


def target_scope(unit, attempt):
    UUID(attempt)
    return (f"source_build_id={literal(unit.build_id)} "
        f"AND session_date=toDate({literal(unit.session_date.isoformat())}) "
        f"AND ticker={literal(unit.ticker)} "
        f"AND source_attempt_id=toUUID({literal(unit.source_attempt_id)}) "
        f"AND derivation_attempt_id=toUUID({literal(attempt)}) AND resolution_ms=1000")


def _prefix_values(unit, attempt):
    UUID(attempt)
    return [literal(unit.build_id), f"toDate({literal(unit.session_date.isoformat())})",
        literal(unit.ticker), f"toUUID({literal(unit.source_attempt_id)})",
        f"toUUID({literal(attempt)})", "toUInt32(1000)"]


def liquidity_select_sql(unit: LiquiditySourceUnit, attempt: str):
    """Aggregate disjoint completed100ms slots, preserving latest quote state.

    Sparse absent source slots are covered-empty only because the caller has
    verified the full parent certificate. They never create synthetic candles.
    Cumulative fields are copied from the latest sub-bucket, never summed.
    """
    expressions = _prefix_values(unit, attempt) + [
        "toUInt32(intDiv(s.bucket_index,10))", "min(s.first_event_us)", "max(s.last_event_us)"]
    expressions += [f"toUInt64(sum(s.{name}))" for name in COUNTERS]
    expressions += ["argMinIf(s.open_int,s.bucket_index,s.price_valid=1)",
        "maxIf(s.high_int,s.extremes_valid=1)", "minIf(s.low_int,s.extremes_valid=1)",
        "argMaxIf(s.close_int,s.bucket_index,s.price_valid=1)"]
    expressions += [f"sum(s.{name})" for name in SUMS]
    expressions += [f"max(s.{name})" for name in ("price_valid", "extremes_valid", "volume_valid")]
    expressions += [f"argMax(s.{name},s.bucket_index)" for name, _ in CARRY]
    columns = [name for name, _ in LIQUIDITY.columns]
    return ("SELECT " + ",".join(f"{expression} AS {name}" for name, expression in zip(columns, expressions))
        + " FROM arte.liquidity_100ms_v1 s WHERE " + source_scope(unit, qualifier="s.")
        + " GROUP BY intDiv(s.bucket_index,10) ORDER BY bucket_index")


def price_select_sql(unit: LiquiditySourceUnit, attempt: str):
    expressions = _prefix_values(unit, attempt) + ["toUInt32(intDiv(s.bucket_index,10))",
                                                  "s.price_int", "sum(s.execution_volume)"]
    return ("SELECT " + ",".join(f"{expression} AS {name}" for (name, _), expression in zip(PRICE.columns, expressions))
        + " FROM arte.liquidity_execution_price_100ms_v1 s WHERE " + source_scope(unit, price=True, qualifier="s.")
        + " GROUP BY intDiv(s.bucket_index,10),s.price_int ORDER BY bucket_index,price_int")


def insert_sql(table, unit, attempt):
    if table not in (LIQUIDITY, PRICE):
        raise ValueError("Liquidity producer cannot INSERT an undeclared table")
    columns = ",".join(name for name, _ in table.columns)
    select_sql = liquidity_select_sql(unit, attempt) if table == LIQUIDITY else price_select_sql(unit, attempt)
    return f"INSERT INTO arte.{table.name} ({columns}) {select_sql}"


def bucket_parity_sql(unit, attempt):
    scope = target_scope(unit, attempt)
    # FULL JOIN exposes missing or extra eligible buckets. Zero-volume quote
    # buckets are valid and have no price child; no positive volume can vanish.
    return f"""SELECT if(ifNull(l.liquid_present,0)=1,l.bucket_index,p.bucket_index) AS bucket_index
      FROM (SELECT bucket_index,execution_volume,count() AS rows,toUInt8(1) AS liquid_present
            FROM arte.{LIQUIDITY.name} WHERE {scope}
            GROUP BY bucket_index,execution_volume) l
      FULL JOIN (SELECT bucket_index,sum(execution_volume) AS execution_volume,
                 count() AS rows,toUInt8(1) AS price_present FROM arte.{PRICE.name} WHERE {scope}
                 GROUP BY bucket_index) p USING(bucket_index)
      WHERE ifNull(l.liquid_present,0)=0 OR l.rows!=1 OR
        (l.execution_volume=0 AND ifNull(p.price_present,0)=1) OR
        (l.execution_volume>0 AND ifNull(p.price_present,0)=0) OR
        abs(ifNull(l.execution_volume,0)-ifNull(p.execution_volume,0))>
          greatest(1e-6,1e-9*abs(ifNull(l.execution_volume,0)))
      LIMIT 1"""
