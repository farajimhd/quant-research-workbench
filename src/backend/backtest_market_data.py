"""Read-only persisted market-day authority for fixed-interval Backtest runs.

This module deliberately has no builder imports.  A Backtest process may read a
certified market-day build and its immutable rows; it cannot create, repair, or
publish market products.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import date, datetime, time, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo


ARTE_DATABASE = "arte"
MARKET_DAY_VERSION = "market-day-core-v5"
MARKET_DAY_TABLES = ("bars_v1", "indicators_v1", "liquidity_100ms_v1")
MARKET_DAY_STAGES = ("bars", "technical", "broker_100ms")
FIXED_EXECUTION_BLOCKER = (
    "Fixed-interval Backtest is not yet causally executable: aggregate 100ms rows "
    "cannot be converted to synthetic quote/trade events for broker fills or V7 strategy state. "
    "A native persisted-bar strategy and liquidity-aware broker path must pass equivalence tests first."
)
EVENT_EXECUTION_BLOCKER = (
    "Event-interval Backtest still prepares a run-local strategy frame spool; "
    "Backtest must fetch persisted strategy inputs without generating them."
)
DEFAULT_LEDGER = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\build-ledger-v2.sqlite3"
)
FIXED_RESOLUTIONS_MS = (100, 1_000, 5_000, 10_000, 30_000, 60_000, 300_000, 3_600_000)
_INTERVAL = re.compile(r"^(?P<value>[1-9][0-9]*)(?P<unit>ms|s|m|h)$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_NEW_YORK = ZoneInfo("America/New_York")


def market_day_boundary(session_date: date | str, boundary_ms: int) -> datetime:
    """Decode the persisted bucket clock, whose origin is 04:00 New York."""
    day = date.fromisoformat(session_date) if isinstance(session_date, str) else session_date
    if not 0 <= boundary_ms <= 57_600_000:
        raise ValueError("Market-day boundary must be within 04:00-20:00 New York")
    return datetime.combine(day, time(4), tzinfo=_NEW_YORK) + timedelta(
        milliseconds=boundary_ms
    )


@dataclass(frozen=True, slots=True)
class ExecutionInterval:
    kind: str
    milliseconds: int | None = None

    @classmethod
    def parse(cls, value: Any) -> "ExecutionInterval":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            kind = str(value.get("kind") or "").strip().lower()
            if not kind and value.get("unit") is not None:
                unit = str(value["unit"]).strip().lower()
                scale = {
                    "milliseconds": 1, "seconds": 1_000,
                    "minutes": 60_000, "hours": 3_600_000,
                }.get(unit)
                if scale is None:
                    raise ValueError(f"Unsupported persisted Backtest interval unit: {unit}")
                return cls.fixed(int(value.get("value") or 0) * scale)
            if kind == "events":
                return cls("events")
            if kind == "fixed":
                raw = value.get("milliseconds")
                if raw is None and value.get("nanoseconds") is not None:
                    nanoseconds = int(value["nanoseconds"])
                    if nanoseconds % 1_000_000:
                        raise ValueError("execution_interval nanoseconds must resolve to whole milliseconds")
                    raw = nanoseconds // 1_000_000
                return cls.fixed(int(raw or 0))
            raise ValueError("execution_interval kind must be events or fixed")
        text = str(value or "100ms").strip().lower()
        if text in {"events", "realtime"}:
            return cls("events")
        match = _INTERVAL.fullmatch(text)
        if match is None:
            raise ValueError("execution_interval must be events or a positive 100ms multiple")
        scale = {"ms": 1, "s": 1_000, "m": 60_000, "h": 3_600_000}[match["unit"]]
        return cls.fixed(int(match["value"]) * scale)

    @classmethod
    def fixed(cls, milliseconds: int) -> "ExecutionInterval":
        if milliseconds < 100 or milliseconds % 100:
            raise ValueError("execution_interval must be events or a positive 100ms multiple")
        return cls("fixed", milliseconds)

    @property
    def label(self) -> str:
        if self.kind == "events":
            return "events"
        assert self.milliseconds is not None
        if self.milliseconds % 3_600_000 == 0:
            return f"{self.milliseconds // 3_600_000}h"
        if self.milliseconds % 60_000 == 0:
            return f"{self.milliseconds // 60_000}m"
        if self.milliseconds % 1_000 == 0:
            return f"{self.milliseconds // 1_000}s"
        return f"{self.milliseconds}ms"

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, **({"milliseconds": self.milliseconds} if self.milliseconds else {})}


@dataclass(frozen=True, slots=True)
class MarketDayUnit:
    build_id: str
    session_date: str
    ticker: str
    stage: str
    attempt_id: str
    source_hash: str
    output_rows: int
    output_hash: str


@dataclass(frozen=True, slots=True)
class CertifiedMarketDayPlan:
    execution_interval: ExecutionInterval
    build_id: str
    definition_hash: str
    sessions: tuple[str, ...]
    tickers: tuple[str, ...]
    units: tuple[MarketDayUnit, ...]
    required_resolutions_ms: tuple[int, ...]
    token: str

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "backtest-market-day-plan-v1",
            "execution_interval": self.execution_interval.payload(),
            "build_id": self.build_id,
            "definition_hash": self.definition_hash,
            "sessions": list(self.sessions),
            "tickers": list(self.tickers),
            "required_resolutions_ms": list(self.required_resolutions_ms),
            "unit_count": len(self.units),
            "token": self.token,
        }


def project_market_day_plan(
    plan: CertifiedMarketDayPlan, tickers: Sequence[str],
) -> CertifiedMarketDayPlan:
    """Restrict computation to proven possible participants, retaining a parent pin."""
    selected = tuple(sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}))
    if not selected or not set(selected).issubset(plan.tickers):
        raise ValueError("Market-day projection must be a nonempty subset of the certified plan")
    units = tuple(unit for unit in plan.units if unit.ticker in selected)
    expected = {(day, ticker, stage) for day in plan.sessions for ticker in selected
                for stage in MARKET_DAY_STAGES}
    if {(unit.session_date, unit.ticker, unit.stage) for unit in units} != expected:
        raise ValueError("Projected market-day plan has incomplete pinned products")
    return CertifiedMarketDayPlan(
        execution_interval=plan.execution_interval,
        build_id=plan.build_id, definition_hash=plan.definition_hash,
        sessions=plan.sessions, tickers=selected, units=units,
        required_resolutions_ms=plan.required_resolutions_ms,
        token=_stable_hash({"parent_token": plan.token, "tickers": selected}),
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def effective_execution_interval(configuration: Mapping[str, Any]) -> ExecutionInterval:
    strategy = configuration.get("strategy") if isinstance(configuration.get("strategy"), Mapping) else {}
    run_plan = configuration.get("run_plan") if isinstance(configuration.get("run_plan"), Mapping) else {}
    raw = strategy.get("execution_interval") or run_plan.get("execution_interval") or "100ms"
    return ExecutionInterval.parse(raw)


def compile_required_resolutions(
    configuration: Mapping[str, Any], execution_interval: ExecutionInterval,
) -> tuple[int, ...]:
    required: set[int] = {1_000}
    if execution_interval.kind == "fixed":
        required.add(int(execution_interval.milliseconds or 100))

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {
                    "execution_interval", "interval", "left_interval", "right_interval",
                    "timeframe", "working_timeframe",
                }:
                    try:
                        parsed = ExecutionInterval.parse(child)
                    except (TypeError, ValueError):
                        pass
                    else:
                        if parsed.kind == "fixed":
                            required.add(int(parsed.milliseconds))
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(configuration)
    unavailable = sorted(required.difference(FIXED_RESOLUTIONS_MS))
    if unavailable:
        raise ValueError(f"Persisted market-day products do not provide resolutions: {unavailable}")
    return tuple(sorted(required))


def configuration_tickers(configuration: Mapping[str, Any], requested: Iterable[str]) -> tuple[str, ...]:
    values = {str(value).strip().upper() for value in requested if str(value).strip()}
    for row in configuration.get("assignments") or ():
        if not isinstance(row, Mapping):
            continue
        for key in ("ticker", "symbol"):
            value = str(row.get(key) or "").strip().upper()
            if value:
                values.add(value)
    invalid = sorted(value for value in values if _TICKER.fullmatch(value) is None)
    if invalid:
        raise ValueError(f"Invalid Backtest tickers: {invalid}")
    return tuple(sorted(values))


class MarketDayLedger:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("BACKTEST_MARKET_DAY_LEDGER", "").strip()
        self.path = path or (Path(configured) if configured else DEFAULT_LEDGER)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise ValueError(f"Certified market-day ledger is unavailable: {self.path}")
        raw_path = str(self.path)
        uri = f"file:{raw_path}?mode=ro" if raw_path.startswith("\\\\") else f"{self.path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _planned_scopes(self, build_id: str, definition_hash: str, days: tuple[str, ...]) -> set[tuple[str, str]]:
        """Read the producer's immutable population, not its completed subset."""
        manifest = self.path.parent / "market-day" / f"{build_id}.json"
        if not manifest.is_file():
            raise ValueError(f"Certified market-day population manifest is unavailable: {manifest}")
        report = json.loads(manifest.read_text(encoding="utf-8"))
        definition = report.get("definition")
        if (report.get("build_id") != build_id or not isinstance(definition, dict)
                or _stable_hash(definition) != definition_hash):
            raise ValueError("Market-day population manifest does not match the certified build")
        plan = definition.get("plan") or {}
        requested = set(plan.get("requested") or ())
        if not set(days).issubset(requested):
            raise ValueError("Backtest sessions are outside the certified market-day population")
        scopes = {
            (str(row["source_date"]), str(row["ticker"]))
            for row in plan.get("units") or ()
            if str(row.get("source_date")) in days
        }
        if not scopes or any(not any(day == scope_day for scope_day, _ in scopes) for day in days):
            raise ValueError("Certified market-day population has an empty requested session")
        return scopes

    def certified_plan(
        self,
        *,
        sessions: Sequence[date | str],
        tickers: Sequence[str],
        configuration: Mapping[str, Any],
    ) -> CertifiedMarketDayPlan:
        interval = effective_execution_interval(configuration)
        if interval.kind == "events":
            raise ValueError("Event execution does not use the fixed market-day catalogue")
        days = tuple(str(value) for value in sessions)
        if not days:
            raise ValueError("Backtest market-day plan requires at least one session")
        resolutions = compile_required_resolutions(configuration, interval)
        with closing(self._connect()) as connection:
            builds = connection.execute(
                "SELECT build_id,definition_hash,updated_at FROM builds "
                "WHERE database_name=? AND version=? AND status IN ('building','core_complete') "
                "ORDER BY updated_at DESC,build_id DESC",
                (ARTE_DATABASE, MARKET_DAY_VERSION),
            ).fetchall()
            if not builds:
                raise ValueError("No market-day-core-v5 build with certifiable sessions is available")
            errors: list[str] = []
            for build_id, definition_hash, _ in builds:
                try:
                    population = self._planned_scopes(str(build_id), str(definition_hash), days)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f"{build_id}: {exc}")
                    continue
                selected = set(population)
                if tickers:
                    selected = {(day, ticker) for day in days for ticker in tickers}
                    outside = selected.difference(population)
                    if outside:
                        errors.append(f"{build_id}: {len(outside)} ticker-days outside certified population")
                        continue
                params: list[Any] = [build_id, *days]
                where_ticker = ""
                selected_tickers = sorted({ticker for _, ticker in selected})
                where_ticker = f" AND ticker IN ({','.join('?' for _ in selected_tickers)})"
                params.extend(selected_tickers)
                rows = connection.execute(
                    f"SELECT build_id,session_date,ticker,stage,attempt_id,source_hash,"
                    f"output_rows,output_hash FROM units WHERE build_id=? "
                    f"AND session_date IN ({','.join('?' for _ in days)}){where_ticker} "
                    "AND status='complete' AND stage IN ('bars','technical','broker_100ms') "
                    "ORDER BY session_date,ticker,stage",
                    params,
                ).fetchall()
                units = tuple(MarketDayUnit(*row) for row in rows)
                scopes: dict[tuple[str, str], set[str]] = {}
                for unit in units:
                    scopes.setdefault((unit.session_date, unit.ticker), set()).add(unit.stage)
                expected = selected
                missing = sorted(scope for scope in expected if scopes.get(scope) != set(MARKET_DAY_STAGES))
                if not expected:
                    errors.append(f"{build_id}: no completed ticker-day products")
                    continue
                if missing:
                    errors.append(f"{build_id}: {len(missing)} ticker-day product gaps")
                    continue
                payload = {
                    "build_id": build_id,
                    "definition_hash": definition_hash,
                    "sessions": days,
                    "tickers": tuple(sorted({ticker for _, ticker in expected})),
                    "resolutions": resolutions,
                    "units": [unit.__dict__ if hasattr(unit, "__dict__") else [
                        unit.build_id, unit.session_date, unit.ticker, unit.stage, unit.attempt_id,
                        unit.source_hash, unit.output_rows, unit.output_hash,
                    ] for unit in units],
                }
                return CertifiedMarketDayPlan(
                    execution_interval=interval,
                    build_id=str(build_id),
                    definition_hash=str(definition_hash),
                    sessions=days,
                    tickers=tuple(payload["tickers"]),
                    units=units,
                    required_resolutions_ms=resolutions,
                    token=_stable_hash(payload),
                )
        raise ValueError("No complete compatible market-day build: " + "; ".join(errors[:3]))


def readonly_clickhouse_client(*, market_stream: bool = False):
    """Create the dedicated Backtest reader; never borrow writer credentials."""
    from research.mlops.clickhouse import ClickHouseHttpClient

    url = os.environ.get("BACKTEST_CLICKHOUSE_URL", "").strip()
    user = os.environ.get("BACKTEST_CLICKHOUSE_USER", "").strip()
    password = os.environ.get("BACKTEST_CLICKHOUSE_PASSWORD", "")
    if not url or not user:
        raise ValueError(
            "Backtest requires dedicated BACKTEST_CLICKHOUSE_URL and "
            "BACKTEST_CLICKHOUSE_USER read-only credentials"
        )
    query_params = {"readonly": 1, "max_threads": 4, "max_execution_time": 60}
    if market_stream:
        # The certified full-universe read pins thousands of independent
        # ticker-day attempts in one globally ordered causal stream. Its SQL
        # exceeds ClickHouse's small default parser limit, and backpressure
        # from strategy execution can keep the response open for hours.
        query_params.update(max_query_size=16 * 1024 * 1024,
                            max_ast_elements=500_000,
                            max_execution_time=21_600)
    return ClickHouseHttpClient(
        url,
        user,
        password,
        timeout_seconds=60,
        persistent=True,
        default_query_params=query_params,
    )


def verify_market_day_plan(plan: CertifiedMarketDayPlan, client=None) -> None:
    """Recheck pinned row counts, keys, hashes, and resolutions read-only."""
    active = client or readonly_clickhouse_client()
    close = client is None
    try:
        expected = len({(unit.session_date, unit.ticker) for unit in plan.units})
        if expected <= 0:
            raise ValueError("Certified market-day plan has no ticker-day scope")
        stage_tables = {
            "bars": "bars_v1",
            "technical": "indicators_v1",
            "broker_100ms": "liquidity_100ms_v1",
        }
        for stage, table in stage_tables.items():
            units = [unit for unit in plan.units if unit.stage == stage]
            if len(units) != expected:
                raise ValueError(f"Certified market-day plan has incomplete {stage} scope")
            by_day: dict[str, list[MarketDayUnit]] = {}
            for unit in units:
                by_day.setdefault(unit.session_date, []).append(unit)
            for day, day_units in by_day.items():
                ordered = sorted(day_units, key=lambda unit: unit.ticker)
                for offset in range(0, len(ordered), 256):
                    batch = ordered[offset:offset + 256]
                    tickers = ",".join(_literal(unit.ticker) for unit in batch)
                    sql = assert_select_only(
                        "SELECT ticker,toString(attempt_id) AS attempt_id,"
                        "count() AS n,uniqExact((resolution_ms,bucket_index)) AS unique_keys,"
                        "toString(sum(cityHash64(tuple(*)))) AS hash,"
                        "groupUniqArray(resolution_ms) AS resolutions "
                        f"FROM {ARTE_DATABASE}.{table} WHERE build_id={_literal(plan.build_id)} "
                        f"AND session_date=toDate({_literal(day)}) AND ticker IN ({tickers}) "
                        "GROUP BY ticker,attempt_id FORMAT JSONEachRow"
                    )
                    rows = [json.loads(line) for line in active.execute(sql).splitlines() if line.strip()]
                    actual = {(str(row["ticker"]), str(row["attempt_id"])): row for row in rows}
                    if len(actual) != len(rows):
                        raise ValueError(f"Persisted {ARTE_DATABASE}.{table} has duplicate integrity groups")
                    for unit in batch:
                        row = actual.get((unit.ticker, unit.attempt_id))
                        count = int(row["n"]) if row else 0
                        output_hash = str(row["hash"]) if row else "0"
                        unique = int(row["unique_keys"]) if row else 0
                        if (count != unit.output_rows or output_hash != unit.output_hash
                                or unique != count):
                            raise ValueError(
                                f"Persisted {ARTE_DATABASE}.{table} integrity changed: "
                                f"{day} {unit.ticker} expected {unit.output_rows} rows, "
                                f"found {count}"
                            )
                        if row and stage in {"bars", "technical"}:
                            missing = set(plan.required_resolutions_ms).difference(
                                int(value) for value in row["resolutions"]
                            )
                            if missing:
                                raise ValueError(
                                    f"Persisted {ARTE_DATABASE}.{table} lacks {day} "
                                    f"{unit.ticker} resolutions {sorted(missing)}"
                                )
    finally:
        if close:
            active.close()


def assert_select_only(sql: str) -> str:
    normalized = sql.strip().rstrip(";")
    if not re.match(r"^(SELECT|WITH)\b", normalized, flags=re.IGNORECASE):
        raise ValueError("Backtest ClickHouse access is SELECT-only")
    forbidden = re.search(
        r"\b(INSERT|ALTER|CREATE|DROP|TRUNCATE|OPTIMIZE|SYSTEM|KILL|GRANT|REVOKE|ATTACH|DETACH)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if forbidden:
        raise ValueError(f"Backtest ClickHouse query contains forbidden operation {forbidden.group(1)}")
    return normalized


def _literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _unit_map(plan: CertifiedMarketDayPlan, stage: str) -> dict[tuple[str, str], MarketDayUnit]:
    return {(unit.session_date, unit.ticker): unit for unit in plan.units if unit.stage == stage}


def market_day_source_sqls(
    plan: CertifiedMarketDayPlan, *, through_boundary_ms: int | None = None,
) -> tuple[str, ...]:
    """Separate pinned, sorted sources that can be merged without UNION ALL."""
    if through_boundary_ms is not None and (
        type(through_boundary_ms) is not int
        or not 0 < through_boundary_ms <= 57_600_000
        or through_boundary_ms % 100
    ):
        raise ValueError("Market-day end boundary must be a positive 100ms market-session clock")
    bars = _unit_map(plan, "bars")
    technical = _unit_map(plan, "technical")
    liquidity = _unit_map(plan, "broker_100ms")
    for day, ticker in sorted(bars):
        key = (day, ticker)
        if key not in technical or key not in liquidity:
            raise ValueError(f"Certified market-day plan lost {day} {ticker}")
    if not bars:
        raise ValueError("Certified market-day plan has no bar scopes")

    def pinned(stage: str, units: Mapping[tuple[str, str], MarketDayUnit]) -> str:
        attempts = ",".join(
            f"(toDate({_literal(day)}),{_literal(ticker)},toUUID({_literal(unit.attempt_id)}))"
            for (day, ticker), unit in sorted(units.items())
        )
        boundary_filter = (
            "" if through_boundary_ms is None else
            f" AND (toUInt64(bucket_index)+1)*"
            f"{'100' if stage == 'liquidity_100ms_v1' else 'resolution_ms'}"
            f"<={through_boundary_ms}"
        )
        return (
            f"SELECT * FROM arte.{stage} WHERE build_id={_literal(plan.build_id)} "
            f"AND (session_date,ticker,attempt_id) IN ({attempts}){boundary_filter}"
        )

    # Every 100 ms bar is copied from its liquidity bucket by the certified
    # builder, but quote-only buckets have no bar. Drive that resolution from
    # liquidity so the broker never loses a quote-only market boundary.
    bar_columns = (
        "open_int", "high_int", "low_int", "close_int", "volume", "trade_count",
        "notional", "execution_volume", "execution_notional", "price_valid",
        "extremes_valid",
    )
    liquidity_columns = (
        "first_event_us", "last_event_us", "event_count", "source_trade_count",
        "quote_event_count", "quote_timestamp_us", "bid_int", "ask_int",
        "bid_size", "ask_size", "spread", "quote_valid", "cumulative_volume",
        "cumulative_notional", "cumulative_execution_volume",
        "cumulative_execution_notional", "execution_vwap",
    )
    columns_100 = ",".join(f"b.{name} AS {name}" for name in bar_columns)
    liquidity_100 = ",".join(f"l.{name} AS {name}" for name in liquidity_columns)
    base_100 = f"""SELECT l.session_date,l.ticker,l.bucket_index,toUInt32(100) AS resolution_ms,
        (toUInt64(l.bucket_index)+1)*100 AS boundary_ms,{columns_100},{liquidity_100}
      FROM ({pinned('liquidity_100ms_v1', liquidity)}) l
      LEFT JOIN (SELECT * FROM ({pinned('bars_v1', bars)}) WHERE resolution_ms=100) b ON
        b.session_date=l.session_date AND b.ticker=l.ticker
        AND b.bucket_index=l.bucket_index AND b.resolution_ms=100"""
    higher = tuple(value for value in plan.required_resolutions_ms if value > 100)
    bases = [base_100]
    if higher:
        resolution_sql = ",".join(str(value) for value in higher)
        columns_higher = ",".join(f"b.{name} AS {name}" for name in bar_columns)
        empty_liquidity = ",".join(f"0 AS {name}" for name in liquidity_columns)
        bases.append(f"""SELECT b.session_date,b.ticker,b.bucket_index,
          b.resolution_ms,(toUInt64(b.bucket_index)+1)*b.resolution_ms AS boundary_ms,
          {columns_higher},{empty_liquidity}
          FROM (SELECT * FROM ({pinned('bars_v1', bars)})
                WHERE resolution_ms IN ({resolution_sql})) b""")
    indicators = ",".join("i." + name for name in (
        "ema_7", "ema_9", "ema_12", "ema_15", "ema_20", "ema_26", "ema_50",
        "macd_line", "macd_signal", "macd_histogram", "rsi_14", "atr_14",
        "rsi_ready", "atr_ready", "previous_close",
    ))
    return tuple(assert_select_only(f"""
      SELECT m.*,{indicators} FROM ({base}) m
      LEFT JOIN ({pinned('indicators_v1', technical)}) i ON
        i.session_date=m.session_date AND i.ticker=m.ticker
        AND i.resolution_ms=m.resolution_ms AND i.bucket_index=m.bucket_index
      ORDER BY m.session_date,m.boundary_ms,m.ticker,m.resolution_ms
      FORMAT JSONEachRow
    """) for base in bases)


def iter_market_day_rows(
    plan: CertifiedMarketDayPlan, client=None, *, through_boundary_ms: int | None = None,
) -> Iterator[dict[str, Any]]:
    active = client or readonly_clickhouse_client(market_stream=True)
    close = client is None
    sources = []
    try:
        from heapq import merge
        sources = [active.iter_json_each_row(sql) for sql in
                   market_day_source_sqls(plan, through_boundary_ms=through_boundary_ms)]
        if len(sources) == 1:
            yield from sources[0]
        else:
            yield from merge(*sources, key=lambda row: (
                str(row["session_date"]), int(row["boundary_ms"]),
                str(row["ticker"]), int(row["resolution_ms"])))
    finally:
        for source in sources:
            close_source = getattr(source, "close", None)
            if close_source is not None:
                close_source()
        if close:
            active.close()


def iter_persisted_v7_seconds(
    plan: CertifiedMarketDayPlan, *, session_date: str, ticker: str,
    through_boundary_ms: int, client=None,
) -> Iterator[dict[str, Any]]:
    """Read only completed pinned 1s bars needed for lazy intraday V7 catch-up."""
    if not 0 <= through_boundary_ms <= 57_600_000:
        raise ValueError("V7 catch-up boundary is outside the market session")
    if session_date not in plan.sessions or ticker not in plan.tickers:
        raise ValueError("V7 catch-up scope is outside the certified market-day plan")
    unit = _unit_map(plan, "bars").get((session_date, ticker))
    if unit is None:
        raise ValueError("V7 catch-up lacks a pinned bar attempt")
    completed_count = through_boundary_ms // 1_000
    if completed_count == 0:
        return
    query = assert_select_only(
        "SELECT ticker,resolution_ms,bucket_index,price_valid,extremes_valid,"
        "open_int,high_int,low_int,close_int,volume "
        "FROM arte.bars_v1 "
        f"WHERE build_id={_literal(plan.build_id)} "
        f"AND session_date=toDate({_literal(session_date)}) "
        f"AND ticker={_literal(ticker)} "
        f"AND attempt_id=toUUID({_literal(unit.attempt_id)}) "
        f"AND resolution_ms=1000 AND bucket_index<{completed_count} "
        "ORDER BY bucket_index FORMAT JSONEachRow"
    )
    active = client or readonly_clickhouse_client()
    try:
        for line in active.execute(query).splitlines():
            if line.strip():
                yield json.loads(line)
    finally:
        if client is None:
            active.close()


def iter_market_boundary_groups(
    rows: Iterable[Mapping[str, Any]],
) -> Iterator[tuple[str, int, str, dict[int, Mapping[str, Any]]]]:
    """Preserve one causal boundary across packet splits and sparse resolutions."""
    key: tuple[str, int, str] | None = None
    group: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        current = (str(row["session_date"]), int(row["boundary_ms"]), str(row["ticker"]))
        if key is not None and current < key:
            raise ValueError("Persisted market boundaries are not in causal order")
        if key is not None and current != key:
            yield (*key, group)
            group = {}
        resolution = int(row["resolution_ms"])
        if resolution in group:
            raise ValueError(f"Duplicate persisted resolution at {current}: {resolution}")
        group[resolution] = row
        key = current
    if key is not None:
        yield (*key, group)


def iter_market_time_groups(
    groups: Iterable[tuple[str, int, str, dict[int, Mapping[str, Any]]]],
) -> Iterator[tuple[str, int, list[tuple[str, dict[int, Mapping[str, Any]]]]]]:
    """Collect all tickers at one completed boundary before strategy evaluation."""
    key: tuple[str, int] | None = None
    at_boundary: list[tuple[str, dict[int, Mapping[str, Any]]]] = []
    seen_tickers: set[str] = set()
    for day, boundary_ms, ticker, resolutions in groups:
        current = (day, boundary_ms)
        if key is not None and current < key:
            raise ValueError("Persisted market time boundaries are not in causal order")
        if key is not None and current != key:
            yield (*key, at_boundary)
            at_boundary = []
            seen_tickers = set()
        if ticker in seen_tickers:
            raise ValueError(f"Duplicate ticker at persisted market boundary {current}: {ticker}")
        at_boundary.append((ticker, resolutions))
        seen_tickers.add(ticker)
        key = current
    if key is not None:
        yield (*key, at_boundary)


def vectorized_candidate_mask(rows: Sequence[Mapping[str, Any]]) -> list[bool]:
    """Cheap columnar necessary-condition mask before stateful strategy work."""
    if not rows:
        return []
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - production dependency
        raise RuntimeError("NumPy is required for vectorized Backtest execution") from exc
    close = np.fromiter((float(row.get("close_int") or 0) for row in rows), dtype=np.float64)
    bid = np.fromiter((float(row.get("bid_int") or 0) for row in rows), dtype=np.float64)
    ask = np.fromiter((float(row.get("ask_int") or 0) for row in rows), dtype=np.float64)
    quote = np.fromiter((int(row.get("quote_valid") or 0) for row in rows), dtype=np.uint8)
    price = np.fromiter((int(row.get("price_valid") or 0) for row in rows), dtype=np.uint8)
    volume = np.fromiter((float(row.get("volume") or 0) for row in rows), dtype=np.float64)
    mask = (price == 1) & (quote == 1) & (close > 0) & (bid > 0) & (ask >= bid) & (volume > 0)
    return mask.tolist()
