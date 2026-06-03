from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import polars as pl

from src.backend.real_live_market_data.clickhouse import ClickHouseHttpClient, ensure_replay_tables
from src.backend.real_live_market_data.config import MarketGatewayConfig
from src.backend.real_live_market_data.massive_rest import fetch_massive_stock_snapshot_frame
from src.backend.real_live_market_data.universe import default_universe_sql


EASTERN = ZoneInfo("America/New_York")


def build_startup_universe_preview(
    read_client: ClickHouseHttpClient,
    write_client: ClickHouseHttpClient,
    config: MarketGatewayConfig,
    *,
    row_limit: int = 50,
) -> dict[str, Any]:
    pulled_at = datetime.now(timezone.utc)
    session_date = pulled_at.astimezone(EASTERN).date().isoformat()
    run_id = f"live-startup-{session_date}-{pulled_at.strftime('%H%M%S')}-{uuid4().hex[:8]}"
    errors: list[dict[str, Any]] = []
    universe_query = (config.universe_sql or default_universe_sql(config)).strip()

    reference_frame = pl.DataFrame()
    massive_snapshot_frame = pl.DataFrame()
    joined_frame = pl.DataFrame()

    try:
        reference_frame = read_client.query_frame(universe_query, timeout=30)
        if not reference_frame.is_empty():
            reference_frame = reference_frame.with_columns(
                pl.col("candidate_massive_ticker").cast(pl.Utf8).str.to_uppercase().alias("candidate_massive_ticker")
            )
    except Exception as exc:
        errors.append({"scope": "reference_query", "message": str(exc)})

    try:
        massive_snapshot_frame = fetch_massive_stock_snapshot_frame(config, timeout=35)
    except Exception as exc:
        errors.append({"scope": "massive_snapshot", "message": str(exc)})

    if not reference_frame.is_empty() and not massive_snapshot_frame.is_empty():
        joined_frame = join_reference_with_snapshot(reference_frame, massive_snapshot_frame)

    persistence = {
        "enabled": config.enable_clickhouse_writes,
        "reference_rows_written": 0,
        "run_id": run_id,
        "snapshot_rows_written": 0,
        "status": "disabled" if not config.enable_clickhouse_writes else "pending",
    }
    if config.enable_clickhouse_writes:
        try:
            persistence.update(
                persist_startup_universe(
                    write_client,
                    config,
                    run_id=run_id,
                    session_date=session_date,
                    pulled_at=pulled_at,
                    reference_frame=reference_frame,
                    massive_snapshot_frame=massive_snapshot_frame,
                    joined_frame=joined_frame,
                    errors=errors,
                )
            )
        except Exception as exc:
            errors.append({"scope": "startup_persistence", "message": str(exc)})
            persistence["status"] = "failed"

    reference_rows = frame_preview_rows(reference_frame, row_limit)
    snapshot_rows = frame_preview_rows(joined_frame, row_limit)
    return {
        "can_query_universe": not any(error["scope"] == "reference_query" for error in errors),
        "errors": errors,
        "joined_snapshot_row_count": joined_frame.height,
        "massive_snapshot_row_count": massive_snapshot_frame.height,
        "persistence": persistence,
        "pulled_at_utc": pulled_at.isoformat(),
        "reference_columns": reference_frame.columns,
        "reference_row_count": reference_frame.height,
        "reference_rows": reference_rows,
        "run_id": run_id,
        "session_date": session_date,
        "snapshot_columns": visible_snapshot_columns(joined_frame),
        "snapshot_rows": snapshot_rows,
        # Backward-compatible fields used by the existing gate.
        "preview_columns": reference_frame.columns,
        "row_count": reference_frame.height,
        "rows": reference_rows,
        "universe_query": universe_query,
    }


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


def persist_startup_universe(
    client: ClickHouseHttpClient,
    config: MarketGatewayConfig,
    *,
    run_id: str,
    session_date: str,
    pulled_at: datetime,
    reference_frame: pl.DataFrame,
    massive_snapshot_frame: pl.DataFrame,
    joined_frame: pl.DataFrame,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    ensure_replay_tables(client)
    client.insert_json_each_row(
        "live_startup_universe_runs",
        [
            {
                "run_id": run_id,
                "session_date": session_date,
                "pulled_at_utc": clickhouse_datetime(pulled_at),
                "reference_row_count": reference_frame.height,
                "massive_snapshot_row_count": massive_snapshot_frame.height,
                "joined_snapshot_row_count": joined_frame.height,
                "read_database": config.read_clickhouse.database,
                "write_database": config.write_clickhouse.database,
                "errors": json.dumps(errors, separators=(",", ":"), default=str),
            }
        ],
    )
    reference_rows = [startup_reference_row(run_id, session_date, pulled_at, index, row) for index, row in enumerate(reference_frame.to_dicts())]
    snapshot_rows = [startup_snapshot_row(run_id, session_date, pulled_at, index, row) for index, row in enumerate(joined_frame.to_dicts())]
    client.insert_json_each_row("live_startup_reference_universe", reference_rows)
    client.insert_json_each_row("live_startup_snapshot_universe", snapshot_rows)
    return {
        "reference_rows_written": len(reference_rows),
        "run_id": run_id,
        "snapshot_rows_written": len(snapshot_rows),
        "status": "written",
    }


def startup_reference_row(run_id: str, session_date: str, pulled_at: datetime, index: int, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run_id,
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


def startup_snapshot_row(run_id: str, session_date: str, pulled_at: datetime, index: int, row: dict[str, Any]) -> dict[str, Any]:
    base = startup_reference_row(run_id, session_date, pulled_at, index, row)
    return {
        **{key: base[key] for key in (
            "run_id",
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
        "raw_reference": json.dumps({key: value for key, value in row.items() if not key.startswith("snapshot_")}, separators=(",", ":"), default=str),
        "raw_snapshot": text_value(row.get("snapshot_raw")),
    }


def frame_preview_rows(frame: pl.DataFrame, row_limit: int) -> list[dict[str, Any]]:
    if frame.is_empty():
        return []
    return frame.head(max(1, row_limit)).to_dicts()


def visible_snapshot_columns(frame: pl.DataFrame) -> list[str]:
    if frame.is_empty():
        return []
    hidden = {"snapshot_raw"}
    preferred = [
        "candidate_massive_ticker",
        "ibkr_conid",
        "exchange_code",
        "currency_code",
        "issuer_name",
        "security_product_type",
        "snapshot_last_price",
        "snapshot_day_open",
        "snapshot_day_high",
        "snapshot_day_low",
        "snapshot_day_close",
        "snapshot_day_volume",
        "snapshot_trade_count",
        "snapshot_bid",
        "snapshot_ask",
        "snapshot_spread_bps",
        "snapshot_todays_change",
        "snapshot_todays_change_pct",
        "logo_relative_path",
    ]
    columns = [column for column in preferred if column in frame.columns]
    columns.extend(column for column in frame.columns if column not in hidden and column not in columns)
    return columns


def optional_text(value: Any) -> str | None:
    text = text_value(value)
    return text or None


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def optional_number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def clickhouse_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
