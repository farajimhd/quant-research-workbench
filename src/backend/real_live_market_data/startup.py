from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import polars as pl

from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient, ensure_replay_tables
from src.backend.real_live_market_data.config import MarketGatewayConfig
from src.backend.real_live_market_data.massive_rest import fetch_massive_scanner_enrichment_frame, fetch_massive_stock_snapshot_frame
from src.backend.real_live_market_data.universe import default_universe_sql


EASTERN = ZoneInfo("America/New_York")


def build_startup_universe_preview(
    read_client: ClickHouseHttpClient,
    config: MarketGatewayConfig,
    *,
    enrichment_frame: pl.DataFrame | None = None,
    enrichment_status: dict[str, Any] | None = None,
    row_limit: int = 0,
    snapshot_row_limit: int = 0,
    snapshot_sort_column: str = "",
    snapshot_sort_direction: str = "desc",
) -> dict[str, Any]:
    payload, _frames = build_universe_snapshot_payload(
        read_client,
        config,
        enrichment_frame=enrichment_frame,
        enrichment_status=enrichment_status,
        enrich_scanner=enrichment_frame is not None,
        row_limit=row_limit,
        snapshot_row_limit=snapshot_row_limit,
        snapshot_sort_column=snapshot_sort_column,
        snapshot_sort_direction=snapshot_sort_direction,
    )
    payload["persistence"] = {"enabled": False, "status": "read_only_preview"}
    return payload


def build_trading_session_baseline(
    read_client: ClickHouseHttpClient,
    write_client: ClickHouseHttpClient,
    config: MarketGatewayConfig,
    *,
    trading_session_id: str,
    started_at: datetime,
    row_limit: int = 0,
) -> tuple[dict[str, Any], pl.DataFrame]:
    payload, frames = build_universe_snapshot_payload(read_client, config, row_limit=row_limit, enrich_scanner=True)
    persistence = {
        "enabled": config.enable_clickhouse_writes,
        "reference_rows_written": 0,
        "scanner_rows_written": 0,
        "status": "disabled" if not config.enable_clickhouse_writes else "pending",
        "trading_session_id": trading_session_id,
    }
    if config.enable_clickhouse_writes:
        try:
            persistence.update(
                persist_trading_session_baseline(
                    write_client,
                    config,
                    trading_session_id=trading_session_id,
                    started_at=started_at,
                    session_date=str(payload.get("session_date") or ""),
                    pulled_at=frames["pulled_at"],
                    reference_frame=frames["reference_frame"],
                    massive_snapshot_frame=frames["massive_snapshot_frame"],
                    scanner_frame=frames["scanner_frame"],
                    errors=payload.get("errors", []),
                )
            )
        except Exception as exc:
            payload.setdefault("errors", []).append({"scope": "session_baseline_persistence", "message": str(exc)})
            persistence["status"] = "failed"
    payload["persistence"] = persistence
    payload["trading_session_id"] = trading_session_id
    return payload, frames["scanner_frame"]


def build_universe_snapshot_payload(
    read_client: ClickHouseHttpClient,
    config: MarketGatewayConfig,
    *,
    enrichment_frame: pl.DataFrame | None = None,
    enrichment_status: dict[str, Any] | None = None,
    row_limit: int,
    enrich_scanner: bool,
    snapshot_row_limit: int = 0,
    snapshot_sort_column: str = "",
    snapshot_sort_direction: str = "desc",
) -> tuple[dict[str, Any], dict[str, Any]]:
    pulled_at = datetime.now(timezone.utc)
    session_date = pulled_at.astimezone(EASTERN).date().isoformat()
    errors: list[dict[str, Any]] = []
    progress_steps: list[dict[str, Any]] = []
    universe_query = (config.universe_sql or default_universe_sql(config)).strip()
    reference_frame = pl.DataFrame()
    massive_snapshot_frame = pl.DataFrame()
    joined_frame = pl.DataFrame()
    scanner_frame = pl.DataFrame()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reference_started = perf_counter()
        reference_future = executor.submit(load_reference_frame, read_client, universe_query)
        massive_started = perf_counter()
        massive_snapshot_future = executor.submit(fetch_massive_stock_snapshot_frame, config, timeout=35)
        futures = {
            reference_future: ("reference_query", "ClickHouse reference universe", reference_started, lambda frame: f"{frame.height:,} rows"),
            massive_snapshot_future: ("massive_snapshot", "Massive full-market snapshot", massive_started, lambda frame: f"{frame.height:,} rows"),
        }
        resolved_steps: dict[str, dict[str, Any]] = {}
        for future in as_completed(futures):
            step_id, label, started, detail_factory = futures[future]
            frame, step, error = resolve_frame_future(future, started, step_id, label, detail_factory)
            if step_id == "reference_query":
                reference_frame = frame
            elif step_id == "massive_snapshot":
                massive_snapshot_frame = frame
            resolved_steps[step_id] = step
            if error:
                errors.append(error)
    progress_steps.extend(step for step in (resolved_steps.get("reference_query"), resolved_steps.get("massive_snapshot")) if step)
    if not reference_frame.is_empty():
        reference_frame = add_logo_columns(reference_frame)

    if not reference_frame.is_empty() and not massive_snapshot_frame.is_empty():
        started = perf_counter()
        joined_frame = join_reference_with_snapshot(reference_frame, massive_snapshot_frame)
        scanner_frame = joined_frame
        progress_steps.append(progress_step("snapshot_join", "Reference and snapshot join", "success", started, f"{joined_frame.height:,} rows"))
    else:
        progress_steps.append(progress_step("snapshot_join", "Reference and snapshot join", "waiting", None, "Waiting for reference and snapshot rows"))

    if enrich_scanner and not joined_frame.is_empty():
        try:
            started = perf_counter()
            used_cached_enrichment = enrichment_frame is not None
            if enrichment_frame is None:
                tickers = [str(row["candidate_massive_ticker"]) for row in joined_frame.select("candidate_massive_ticker").to_dicts()]
                enrichment_frame = fetch_massive_scanner_enrichment_frame(config, tickers, timeout=45)
                detail = f"{enrichment_frame.height:,} remote enrichment rows"
            else:
                detail = string_value(enrichment_status, "message") or f"{enrichment_frame.height:,} cached enrichment rows"
            scanner_frame = join_scanner_enrichment(joined_frame, enrichment_frame)
            if not used_cached_enrichment:
                detail = f"{scanner_frame.height:,} scanner rows"
            progress_steps.append(progress_step("scanner_enrichment", "Massive float and short data", "success", started, detail))
        except Exception as exc:
            errors.append({"scope": "massive_scanner_enrichment", "message": str(exc)})
            scanner_frame = add_scanner_labels(joined_frame)
            progress_steps.append(progress_step("scanner_enrichment", "Massive float and short data", "failed", started, str(exc)))
    elif not scanner_frame.is_empty():
        scanner_frame = add_scanner_labels(scanner_frame)
        progress_steps.append(progress_step("scanner_enrichment", "Massive float and short data", "deferred", None, "Runs after live session start"))
    else:
        progress_steps.append(progress_step("scanner_enrichment", "Massive float and short data", "waiting", None, "Waiting for joined snapshot rows"))

    reference_rows = frame_preview_rows(reference_frame, row_limit)
    snapshot_rows = sorted_frame_preview_rows(
        scanner_frame,
        snapshot_row_limit if snapshot_row_limit > 0 else row_limit,
        snapshot_sort_column,
        snapshot_sort_direction,
    )
    payload = {
        "can_query_universe": not any(error["scope"] == "reference_query" for error in errors),
        "errors": errors,
        "joined_snapshot_row_count": joined_frame.height,
        "massive_snapshot_row_count": massive_snapshot_frame.height,
        "persistence": {"enabled": False, "status": "not_requested"},
        "pulled_at_utc": pulled_at.isoformat(),
        "reference_columns": visible_reference_columns(reference_frame),
        "reference_row_count": reference_frame.height,
        "reference_rows": reference_rows,
        "run_id": "",
        "session_date": session_date,
        "snapshot_columns": visible_snapshot_columns(scanner_frame),
        "snapshot_rows": snapshot_rows,
        "preview_columns": visible_reference_columns(reference_frame),
        "progress_steps": progress_steps,
        "row_count": reference_frame.height,
        "rows": reference_rows,
        "scanner_row_count": scanner_frame.height,
        "startup_enrichment": enrichment_status or {"status": "not_requested"},
        "universe_query": universe_query,
    }
    return payload, {
        "joined_frame": joined_frame,
        "massive_snapshot_frame": massive_snapshot_frame,
        "enrichment_frame": enrichment_frame if enrichment_frame is not None else pl.DataFrame(),
        "pulled_at": pulled_at,
        "reference_frame": reference_frame,
        "scanner_frame": scanner_frame,
    }


def load_reference_frame(read_client: ClickHouseHttpClient, universe_query: str) -> pl.DataFrame:
    frame = read_client.query_frame(universe_query, timeout=30)
    if frame.is_empty():
        return frame
    return frame.with_columns(
        pl.col("candidate_massive_ticker").cast(pl.Utf8).str.to_uppercase().alias("candidate_massive_ticker")
    )


def resolve_frame_future(future: Any, started: float, step_id: str, label: str, detail_factory: Any) -> tuple[pl.DataFrame, dict[str, Any], dict[str, str] | None]:
    try:
        frame = future.result()
        if not isinstance(frame, pl.DataFrame):
            frame = pl.DataFrame()
        return frame, progress_step(step_id, label, "success", started, detail_factory(frame)), None
    except Exception as exc:
        return pl.DataFrame(), progress_step(step_id, label, "failed", started, str(exc)), {"scope": step_id, "message": str(exc)}


def join_reference_with_snapshot(reference_frame: pl.DataFrame, snapshot_frame: pl.DataFrame) -> pl.DataFrame:
    reference = reference_frame.with_columns(
        pl.col("candidate_massive_ticker").cast(pl.Utf8).str.to_uppercase().alias("_snapshot_join_ticker")
    )
    snapshot = snapshot_frame.with_columns(
        pl.col("snapshot_ticker").cast(pl.Utf8).str.to_uppercase().alias("_snapshot_join_ticker")
    )
    return (
        reference.join(snapshot, on="_snapshot_join_ticker", how="inner")
        .drop("_snapshot_join_ticker")
        .sort("candidate_massive_ticker")
    )


def add_logo_columns(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    rows: list[dict[str, Any]] = []
    for row in frame.to_dicts():
        relative_path = text_value(row.get("logo_relative_path"))
        row["logo"] = relative_path
        row["logo_url"] = logo_asset_url(relative_path)
        rows.append(row)
    return pl.DataFrame(rows, infer_schema_length=None)


def logo_asset_url(relative_path: str) -> str:
    path = relative_path.strip().replace("\\", "/")
    if not path:
        return ""
    return f"/api/real-live-trading/logo?path={quote(path, safe='')}"


def progress_step(step_id: str, label: str, status: str, started: float | None, detail: str) -> dict[str, Any]:
    return {
        "detail": detail,
        "duration_ms": round((perf_counter() - started) * 1000, 1) if started is not None else None,
        "id": step_id,
        "label": label,
        "status": status,
    }


def join_scanner_enrichment(joined_frame: pl.DataFrame, enrichment_frame: pl.DataFrame) -> pl.DataFrame:
    if enrichment_frame.is_empty():
        return add_scanner_labels(joined_frame)
    frame = joined_frame.join(enrichment_frame, on="candidate_massive_ticker", how="left")
    return add_scanner_labels(frame)


def add_scanner_labels(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    rows = []
    for row in frame.to_dicts():
        float_shares = number_value(row.get("massive_float")) or number_value(row.get("float")) or 0.0
        short_interest = number_value(row.get("massive_short_interest")) or number_value(row.get("short_interest")) or 0.0
        short_volume_ratio = number_value(row.get("massive_short_volume_ratio"))
        days_to_cover = number_value(row.get("massive_days_to_cover"))
        row["float_profile"] = float_profile_label(float_shares)
        row["short_setup"] = short_setup_label(float_shares, short_interest, short_volume_ratio, days_to_cover)
        rows.append(row)
    return pl.DataFrame(rows, infer_schema_length=None)


def persist_trading_session_baseline(
    client: ClickHouseHttpClient,
    config: MarketGatewayConfig,
    *,
    trading_session_id: str,
    started_at: datetime,
    session_date: str,
    pulled_at: datetime,
    reference_frame: pl.DataFrame,
    massive_snapshot_frame: pl.DataFrame,
    scanner_frame: pl.DataFrame,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    ensure_replay_tables(client)
    status = "written" if not errors else "written_with_errors"
    client.insert_json_each_row(
        "live_trading_sessions",
        [
            {
                "trading_session_id": trading_session_id,
                "session_date": session_date,
                "started_at_utc": clickhouse_datetime(started_at),
                "baseline_pulled_at_utc": clickhouse_datetime(pulled_at),
                "baseline_status": status,
                "reference_row_count": reference_frame.height,
                "massive_snapshot_row_count": massive_snapshot_frame.height,
                "joined_snapshot_row_count": scanner_frame.height,
                "scanner_row_count": scanner_frame.height,
                "read_database": config.read_clickhouse.database,
                "write_database": config.write_clickhouse.database,
                "errors": json.dumps(errors, separators=(",", ":"), default=str),
            }
        ],
    )
    reference_rows = [session_reference_row(trading_session_id, session_date, pulled_at, index, row) for index, row in enumerate(reference_frame.to_dicts())]
    scanner_rows = [session_scanner_row(trading_session_id, session_date, pulled_at, index, row) for index, row in enumerate(scanner_frame.to_dicts())]
    client.insert_json_each_row("live_trading_session_reference_universe", reference_rows)
    client.insert_json_each_row("live_trading_session_scanner_universe", scanner_rows)
    return {
        "reference_rows_written": len(reference_rows),
        "scanner_rows_written": len(scanner_rows),
        "status": status,
    }


def session_reference_row(trading_session_id: str, session_date: str, pulled_at: datetime, index: int, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trading_session_id": trading_session_id,
        "session_date": session_date,
        "pulled_at_utc": clickhouse_datetime(pulled_at),
        "row_index": index,
        "candidate_massive_ticker": text_value(row.get("candidate_massive_ticker")),
        "symbol_id": text_value(row.get("symbol_id")),
        "listing_id": text_value(row.get("listing_id")),
        "ibkr_conid": int_value(row.get("ibkr_conid")),
        "exchange_code": text_value(row.get("exchange_code")),
        "currency_code": text_value(row.get("currency_code")),
        "security_product_type": text_value(row.get("security_product_type")),
        "security_type": text_value(row.get("security_type")),
        "issuer_id": text_value(row.get("issuer_id")),
        "issuer_name": text_value(row.get("issuer_name")),
        "logo_asset_id": optional_text(row.get("logo_asset_id")),
        "logo_relative_path": optional_text(row.get("logo_relative_path")),
        "raw": json.dumps(row, separators=(",", ":"), default=str),
    }


def session_scanner_row(trading_session_id: str, session_date: str, pulled_at: datetime, index: int, row: dict[str, Any]) -> dict[str, Any]:
    base = session_reference_row(trading_session_id, session_date, pulled_at, index, row)
    return {
        **{key: base[key] for key in (
            "trading_session_id",
            "session_date",
            "pulled_at_utc",
            "row_index",
            "candidate_massive_ticker",
            "symbol_id",
            "listing_id",
            "ibkr_conid",
            "exchange_code",
            "currency_code",
            "security_product_type",
            "security_type",
            "issuer_id",
            "issuer_name",
            "logo_asset_id",
            "logo_relative_path",
        )},
        "snapshot_last_price": optional_number(row.get("snapshot_last_price")),
        "snapshot_day_open": optional_number(row.get("snapshot_day_open")),
        "snapshot_day_high": optional_number(row.get("snapshot_day_high")),
        "snapshot_day_low": optional_number(row.get("snapshot_day_low")),
        "snapshot_day_close": optional_number(row.get("snapshot_day_close")),
        "snapshot_day_volume": optional_number(row.get("snapshot_day_volume")),
        "snapshot_trade_count": optional_number(row.get("snapshot_trade_count")),
        "snapshot_bid": optional_number(row.get("snapshot_bid")),
        "snapshot_ask": optional_number(row.get("snapshot_ask")),
        "snapshot_spread_bps": optional_number(row.get("snapshot_spread_bps")),
        "snapshot_todays_change": optional_number(row.get("snapshot_todays_change")),
        "snapshot_todays_change_pct": optional_number(row.get("snapshot_todays_change_pct")),
        "massive_float": optional_number(row.get("massive_float")),
        "massive_float_percent": optional_number(row.get("massive_float_percent")),
        "massive_float_date": optional_text(row.get("massive_float_date")),
        "massive_short_interest": optional_number(row.get("massive_short_interest")),
        "massive_short_interest_date": optional_text(row.get("massive_short_interest_date")),
        "massive_days_to_cover": optional_number(row.get("massive_days_to_cover")),
        "massive_short_volume": optional_number(row.get("massive_short_volume")),
        "massive_short_volume_date": optional_text(row.get("massive_short_volume_date")),
        "massive_short_volume_ratio": optional_number(row.get("massive_short_volume_ratio")),
        "massive_short_volume_total_volume": optional_number(row.get("massive_short_volume_total_volume")),
        "float_profile": text_value(row.get("float_profile")) or "unknown",
        "short_setup": text_value(row.get("short_setup")) or "unknown",
        "raw_reference": json.dumps({key: value for key, value in row.items() if not key.startswith(("snapshot_", "massive_"))}, separators=(",", ":"), default=str),
        "raw_snapshot": text_value(row.get("snapshot_raw")),
        "raw_enrichment": json.dumps(
            {
                "float": row.get("massive_float_raw"),
                "short_interest": row.get("massive_short_interest_raw"),
                "short_volume": row.get("massive_short_volume_raw"),
            },
            separators=(",", ":"),
            default=str,
        ),
    }


def frame_preview_rows(frame: pl.DataFrame, row_limit: int) -> list[dict[str, Any]]:
    if frame.is_empty():
        return []
    if row_limit <= 0:
        return frame.to_dicts()
    return frame.head(max(1, row_limit)).to_dicts()


def sorted_frame_preview_rows(frame: pl.DataFrame, row_limit: int, sort_column: str, sort_direction: str) -> list[dict[str, Any]]:
    if frame.is_empty():
        return []
    column = (sort_column or "").strip()
    sorted_frame = frame
    if column in frame.columns:
        descending = sort_direction.strip().lower() != "asc"
        sorted_frame = frame.sort(column, descending=descending, nulls_last=True)
    return frame_preview_rows(sorted_frame, row_limit)


def visible_snapshot_columns(frame: pl.DataFrame) -> list[str]:
    if frame.is_empty():
        return []
    hidden = {"logo_relative_path", "logo_url", "snapshot_raw", "massive_float_raw", "massive_short_interest_raw", "massive_short_volume_raw"}
    preferred = [
        "logo",
        "candidate_massive_ticker",
        "issuer_name",
        "snapshot_last_price",
        "snapshot_todays_change_pct",
        "snapshot_todays_change",
        "snapshot_day_volume",
        "snapshot_trade_count",
        "snapshot_day_open",
        "snapshot_day_high",
        "snapshot_day_low",
        "snapshot_day_close",
        "snapshot_bid",
        "snapshot_ask",
        "snapshot_spread_bps",
        "float_profile",
        "short_setup",
        "massive_short_interest",
        "massive_days_to_cover",
        "massive_short_volume_ratio",
        "massive_float",
        "massive_float_percent",
        "massive_short_volume",
        "ibkr_conid",
        "exchange_code",
        "currency_code",
        "security_product_type",
        "massive_float_date",
        "massive_short_interest_date",
        "massive_short_volume_date",
    ]
    columns = [column for column in preferred if column in frame.columns]
    columns.extend(column for column in frame.columns if column not in hidden and column not in columns)
    return columns


def visible_reference_columns(frame: pl.DataFrame) -> list[str]:
    if frame.is_empty():
        return []
    hidden = {"logo_relative_path", "logo_url"}
    preferred = [
        "logo",
        "candidate_massive_ticker",
        "issuer_name",
        "exchange_code",
        "currency_code",
        "ibkr_conid",
        "security_product_type",
        "security_type",
        "ticker_type_provider_code",
        "symbol_status",
        "listing_status",
        "listing_id",
        "symbol_id",
    ]
    columns = [column for column in preferred if column in frame.columns]
    columns.extend(column for column in frame.columns if column not in hidden and column not in columns)
    return columns


def float_profile_label(float_shares: float) -> str:
    if float_shares <= 0:
        return "unknown"
    if float_shares < 10_000_000:
        return "micro_float"
    if float_shares < 50_000_000:
        return "low_float"
    if float_shares < 250_000_000:
        return "mid_float"
    return "large_float"


def short_setup_label(float_shares: float, short_interest: float, short_volume_ratio: float, days_to_cover: float) -> str:
    short_interest_pct = short_interest / float_shares if float_shares > 0 and short_interest > 0 else 0.0
    if short_interest_pct >= 0.12 and days_to_cover >= 2:
        return "squeeze_watch"
    if short_interest_pct >= 0.12:
        return "crowded_short"
    if short_volume_ratio >= 55:
        return "short_sale_pressure"
    if short_interest > 0 or short_volume_ratio > 0:
        return "normal"
    return "unknown"


def optional_text(value: Any) -> str | None:
    text = text_value(value)
    return text or None


def string_value(row: dict[str, Any] | None, key: str) -> str:
    if not row:
        return ""
    return text_value(row.get(key))


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def number_value(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric == numeric else 0.0


def optional_number(value: Any) -> float | None:
    numeric = number_value(value)
    return numeric if numeric != 0 else None


def clickhouse_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
