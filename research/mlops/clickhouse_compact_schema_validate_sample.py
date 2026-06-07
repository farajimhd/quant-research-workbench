from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_FILE_ROOT_ENV,
    CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_STORAGE_POLICY_ENV,
    CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
    CLICKHOUSE_URL_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_FLATFILES_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    HISTORICAL_CLICKHOUSE_DATABASE_ENV,
    ClickHouseHttpClient,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    discover_source_files,
    normalize_clickhouse_file_path,
    quote_ident,
    sql_string,
)


DEFAULT_DATABASE = "market_sip_compact_benchmark"
DEFAULT_RUN_ID = "20260606_114354"
DEFAULT_QUOTE_DATE = "2026-05-15"
DEFAULT_TRADE_DATE = "2026-05-15"
DEFAULT_SAMPLE_SIZE = 100
DEFAULT_SEED = 20260606

QUOTE_COLUMNS = [
    "ticker",
    "ask_exchange",
    "ask_price",
    "ask_size",
    "bid_exchange",
    "bid_price",
    "bid_size",
    "conditions",
    "indicators",
    "participant_timestamp",
    "sequence_number",
    "sip_timestamp",
    "tape",
]
TRADE_COLUMNS = [
    "ticker",
    "conditions",
    "correction",
    "exchange",
    "participant_timestamp",
    "price",
    "sequence_number",
    "sip_timestamp",
    "size",
    "tape",
]
UINT32_1E4_MAX_PRICE = Decimal("429496.7295")


@dataclass(frozen=True, slots=True)
class ValidationSource:
    kind: str
    date: str
    windows_path: str
    table: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate compact codec benchmark rows against random raw quote/trade rows read with Polars."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--quote-table", default="")
    parser.add_argument("--trade-table", default="")
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--quote-date", default=DEFAULT_QUOTE_DATE)
    parser.add_argument("--trade-date", default=DEFAULT_TRADE_DATE)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_schema_codec_validation"))
    return parser.parse_args()


def env_status_keys() -> list[str]:
    return [
        CLICKHOUSE_URL_ENV,
        CLICKHOUSE_WORKSTATION_USER_ENV,
        CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
        CLICKHOUSE_USER_SIMPLE_ENV,
        CLICKHOUSE_PASSWORD_SIMPLE_ENV,
        HISTORICAL_CLICKHOUSE_DATABASE_ENV,
        CLICKHOUSE_HISTORICAL_STORAGE_POLICY_ENV,
        CLICKHOUSE_STORAGE_POLICY_SIMPLE_ENV,
        CLICKHOUSE_ENDPOINT_ENV,
        CLICKHOUSE_USER_ENV,
        CLICKHOUSE_PASSWORD_ENV,
        CLICKHOUSE_FILE_ROOT_ENV,
        "CLICKHOUSE_FLATFILES_ROOT",
        CLICKHOUSE_STORAGE_POLICY_ENV,
    ]


def load_polars():
    try:
        import polars as pl
    except ImportError as exc:
        raise RuntimeError("polars is required for raw CSV sampling validation") from exc
    return pl


def collect_lazy(frame: Any):
    try:
        return frame.collect(engine="streaming")
    except (TypeError, ValueError):
        return frame.collect()


def discover_one_source(root_win: Path, root_ch: str, kind: str, date: str) -> Path:
    sources = discover_source_files(root_win, root_ch, [kind], date, date)
    if not sources:
        raise RuntimeError(f"No {kind} source file found for {date} under {root_win}")
    return sources[0].windows_path


def row_count(path: Path) -> int:
    pl = load_polars()
    scan = pl.scan_csv(str(path), infer_schema_length=0)
    result = collect_lazy(scan.select(pl.len().alias("rows")))
    return int(result["rows"][0])


def random_raw_rows(path: Path, columns: list[str], sample_size: int, seed: int):
    pl = load_polars()
    total_rows = row_count(path)
    if total_rows <= 0:
        raise RuntimeError(f"Source file has no rows: {path}")
    sample_count = min(sample_size, total_rows)
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(total_rows), sample_count))
    schema_overrides = {column: pl.Utf8 for column in columns}
    scan = pl.scan_csv(str(path), schema_overrides=schema_overrides, infer_schema_length=0).select(columns)
    try:
        scan = scan.with_row_index("__row_nr")
    except AttributeError:
        scan = scan.with_row_count("__row_nr")
    sampled = collect_lazy(scan.filter(pl.col("__row_nr").is_in(indices)))
    rows = sampled.to_dicts()
    if len(rows) != sample_count:
        raise RuntimeError(f"Expected {sample_count} sampled rows from {path}, got {len(rows)}")
    return rows, {"source_rows": total_rows, "sampled_rows": sample_count, "sampled_row_numbers": indices}


def to_int_or_zero(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def to_decimal_or_zero(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).strip()
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except Exception:  # noqa: BLE001
        return Decimal("0")


def uint32_from_decimal_string(value: Any) -> int:
    number = to_decimal_or_zero(value)
    if number <= 0:
        return 0
    return int(number.to_integral_value(rounding=ROUND_HALF_UP))


def trunc_div_toward_zero(value: int, divisor: int) -> int:
    if value >= 0:
        return value // divisor
    return -((-value) // divisor)


def clamp_int32(value: int) -> int:
    return max(-2147483648, min(2147483647, value))


def scale_code(price: Decimal) -> int:
    return 1 if price > 0 and (price < 1 or (has_sub_cent_precision(price) and price <= UINT32_1E4_MAX_PRICE)) else 0


def has_sub_cent_precision(price: Decimal) -> bool:
    cents = price * Decimal("100")
    return cents != cents.to_integral_value(rounding=ROUND_HALF_EVEN)


def price_precision_clipped(price: Decimal) -> bool:
    return price > UINT32_1E4_MAX_PRICE and has_sub_cent_precision(price)


def price_int(price: Decimal) -> int:
    scale = Decimal("10000") if scale_code(price) else Decimal("100")
    return int((price * scale).to_integral_value(rounding=ROUND_HALF_EVEN))


def tape_code(raw_tape: Any) -> int:
    return max(0, min(3, to_int_or_zero(raw_tape) - 1))


def participant_delta_us(row: dict[str, Any]) -> tuple[int, int]:
    delta_ns = to_int_or_zero(row.get("participant_timestamp")) - to_int_or_zero(row.get("sip_timestamp"))
    delta_us = trunc_div_toward_zero(delta_ns, 1000)
    return delta_us, clamp_int32(delta_us)


def quantized_price(price: Decimal) -> Decimal:
    decimals = Decimal("0.0001") if scale_code(price) else Decimal("0.01")
    return price.quantize(decimals, rounding=ROUND_HALF_EVEN)


def quote_expected(row: dict[str, Any]) -> dict[str, Any]:
    bid_price = to_decimal_or_zero(row.get("bid_price"))
    ask_price = to_decimal_or_zero(row.get("ask_price"))
    delta_us, clipped_delta_us = participant_delta_us(row)
    bid_size = uint32_from_decimal_string(row.get("bid_size"))
    ask_size = uint32_from_decimal_string(row.get("ask_size"))
    issue_flags = (
        (1 if bid_price <= 0 else 0)
        + (2 if ask_price <= 0 else 0)
        + (4 if bid_size <= 0 else 0)
        + (8 if ask_size <= 0 else 0)
        + (16 if delta_us < -2147483648 or delta_us > 2147483647 else 0)
        + (32 if price_precision_clipped(bid_price) else 0)
        + (64 if price_precision_clipped(ask_price) else 0)
    )
    return {
        "ticker": str(row["ticker"]),
        "sip_timestamp_us": to_int_or_zero(row.get("sip_timestamp")) // 1000,
        "sequence_number": to_int_or_zero(row.get("sequence_number")),
        "bid_price": format(quantized_price(bid_price), "f"),
        "ask_price": format(quantized_price(ask_price), "f"),
        "bid_size": bid_size,
        "ask_size": ask_size,
        "bid_exchange": to_int_or_zero(row.get("bid_exchange")),
        "ask_exchange": to_int_or_zero(row.get("ask_exchange")),
        "tape": tape_code(row.get("tape")) + 1,
        "conditions": str(row.get("conditions") or ""),
        "indicators": str(row.get("indicators") or ""),
        "issue_flags": issue_flags,
    }


def trade_expected(row: dict[str, Any]) -> dict[str, Any]:
    price = to_decimal_or_zero(row.get("price"))
    delta_us, clipped_delta_us = participant_delta_us(row)
    size = to_decimal_or_zero(row.get("size"))
    correction_code = max(0, min(15, to_int_or_zero(row.get("correction"))))
    issue_flags = (
        (1 if price <= 0 else 0)
        + (2 if size <= 0 else 0)
        + (4 if delta_us < -2147483648 or delta_us > 2147483647 else 0)
        + (8 if price_precision_clipped(price) else 0)
    )
    return {
        "ticker": str(row["ticker"]),
        "sip_timestamp_us": to_int_or_zero(row.get("sip_timestamp")) // 1000,
        "sequence_number": to_int_or_zero(row.get("sequence_number")),
        "price": format(quantized_price(price), "f"),
        "size": format(size, "f"),
        "exchange": to_int_or_zero(row.get("exchange")),
        "tape": tape_code(row.get("tape")) + 1,
        "correction": correction_code,
        "conditions": str(row.get("conditions") or ""),
        "issue_flags": issue_flags,
    }


def key_for(row: dict[str, Any]) -> tuple[str, int, int]:
    return (str(row["ticker"]), int(row["sip_timestamp_us"]), int(row["sequence_number"]))


def quote_chart_select(database: str, table: str, values: str) -> str:
    db_table = f"{quote_ident(database)}.{quote_ident(table)}"
    bid_price = "if(bitAnd(quote_flags, 1) = 1, bid_price_int / 10000.0, bid_price_int / 100.0)"
    ask_price = "if(bitAnd(bitShiftRight(quote_flags, 1), 1) = 1, ask_price_int / 10000.0, ask_price_int / 100.0)"
    tape = "bitAnd(bitShiftRight(quote_flags, 2), 3) + 1"
    return (
        "SELECT "
        "ticker, sip_timestamp_us, sequence_number, "
        f"toString({bid_price}) AS bid_price, "
        f"toString({ask_price}) AS ask_price, "
        "bid_size, ask_size, bid_exchange, ask_exchange, "
        f"{tape} AS tape, "
        "conditions, indicators, issue_flags "
        f"FROM {db_table} "
        f"WHERE (ticker, sip_timestamp_us, sequence_number) IN ({values}) "
        "FORMAT JSONEachRow"
    )


def trade_chart_select(database: str, table: str, values: str) -> str:
    db_table = f"{quote_ident(database)}.{quote_ident(table)}"
    price = "if(bitAnd(trade_flags, 1) = 1, price_int / 10000.0, price_int / 100.0)"
    tape = "bitAnd(bitShiftRight(trade_flags, 1), 3) + 1"
    correction = "bitAnd(bitShiftRight(trade_flags, 3), 15)"
    return (
        "SELECT "
        "ticker, sip_timestamp_us, sequence_number, "
        f"toString({price}) AS price, "
        "size, exchange, "
        f"{tape} AS tape, "
        f"{correction} AS correction, "
        "conditions, issue_flags "
        f"FROM {db_table} "
        f"WHERE (ticker, sip_timestamp_us, sequence_number) IN ({values}) "
        "FORMAT JSONEachRow"
    )


def query_chart_rows(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    kind: str,
    keys: list[tuple[str, int, int]],
) -> dict[tuple[str, int, int], list[dict[str, Any]]]:
    if not keys:
        return {}
    values = ", ".join(f"({sql_string(ticker)}, {sip_us}, {sequence})" for ticker, sip_us, sequence in keys)
    sql = quote_chart_select(database, table, values) if kind == "quotes" else trade_chart_select(database, table, values)
    text = client.execute(sql)
    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        grouped[key_for(row)].append(row)
    return grouped


def compare_rows(expected_rows: list[dict[str, Any]], actual_by_key: dict[tuple[str, int, int], list[dict[str, Any]]]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    matched = 0
    missing = 0
    duplicate_key_rows = 0
    field_mismatch_counts: Counter[str] = Counter()
    for expected in expected_rows:
        key = key_for(expected)
        candidates = actual_by_key.get(key) or []
        if not candidates:
            missing += 1
            mismatches.append({"key": key, "reason": "missing_in_clickhouse", "expected": expected})
            continue
        if len(candidates) > 1:
            duplicate_key_rows += 1
        actual = candidates[0]
        row_mismatches = {}
        for field, expected_value in expected.items():
            actual_value = actual.get(field)
            if field in {"bid_price", "ask_price", "price", "size"} and isinstance(expected_value, str):
                actual_decimal = Decimal(str(actual_value))
                expected_decimal = Decimal(str(expected_value))
                tolerance = Decimal("0.00001") if field == "size" else Decimal("0.0000001")
                field_matches = abs(actual_decimal - expected_decimal) <= tolerance
            else:
                field_matches = actual_value == expected_value
            if not field_matches:
                row_mismatches[field] = {"expected": expected_value, "actual": actual_value}
                field_mismatch_counts[field] += 1
        if row_mismatches:
            mismatches.append({"key": key, "reason": "field_mismatch", "fields": row_mismatches})
        else:
            matched += 1
    return {
        "expected_rows": len(expected_rows),
        "matched_rows": matched,
        "missing_rows": missing,
        "duplicate_key_rows": duplicate_key_rows,
        "mismatch_rows": len(mismatches),
        "field_mismatch_counts": dict(field_mismatch_counts),
        "mismatch_examples": mismatches[:10],
    }


def validate_kind(
    *,
    client: ClickHouseHttpClient,
    database: str,
    run_id: str,
    kind: str,
    date: str,
    path: Path,
    table: str,
    sample_size: int,
    seed: int,
) -> dict[str, Any]:
    if kind == "quotes":
        columns = QUOTE_COLUMNS
        transform = quote_expected
    elif kind == "trades":
        columns = TRADE_COLUMNS
        transform = trade_expected
    else:
        raise ValueError(f"Unsupported kind: {kind}")
    raw_rows, sample_meta = random_raw_rows(path, columns, sample_size, seed)
    expected_rows = [transform(row) for row in raw_rows]
    actual_by_key = query_chart_rows(client, database, table, kind, [key_for(row) for row in expected_rows])
    comparison = compare_rows(expected_rows, actual_by_key)
    return {
        "kind": kind,
        "date": date,
        "source_path": str(path),
        "table": table,
        "sample": sample_meta,
        "comparison": comparison,
    }


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    root_win = Path(args.flatfiles_root_win)
    root_ch = normalize_clickhouse_file_path(args.flatfiles_root_ch)
    output_root = Path(args.output_root_win)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"compact_schema_codec_validation_{args.run_id}_{args.seed}.json"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    quote_table = args.quote_table.strip() or f"quotes_codec_{args.run_id}"
    trade_table = args.trade_table.strip() or f"trades_codec_{args.run_id}"

    quote_path = discover_one_source(root_win, root_ch, "quotes", args.quote_date)
    trade_path = discover_one_source(root_win, root_ch, "trades", args.trade_date)
    print("=" * 96, flush=True)
    print("Compact codec validation against raw flatfiles", flush=True)
    print(f"database={args.database} run_id={args.run_id} sample_size={args.sample_size} seed={args.seed}", flush=True)
    print(f"tables={quote_table},{trade_table}", flush=True)
    print(f"quote_source={quote_path}", flush=True)
    print(f"trade_source={trade_path}", flush=True)
    print(f"output={output_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    quote_result = validate_kind(
        client=client,
        database=args.database,
        run_id=args.run_id,
        kind="quotes",
        date=args.quote_date,
        path=quote_path,
        table=quote_table,
        sample_size=args.sample_size,
        seed=args.seed,
    )
    print(f"QUOTE comparison={quote_result['comparison']}", flush=True)
    trade_result = validate_kind(
        client=client,
        database=args.database,
        run_id=args.run_id,
        kind="trades",
        date=args.trade_date,
        path=trade_path,
        table=trade_table,
        sample_size=args.sample_size,
        seed=args.seed + 1,
    )
    print(f"TRADE comparison={trade_result['comparison']}", flush=True)
    payload = {
        "config": {
            "database": args.database,
            "run_id": args.run_id,
            "quote_table": quote_table,
            "trade_table": trade_table,
            "quote_date": args.quote_date,
            "trade_date": args.trade_date,
            "sample_size": args.sample_size,
            "seed": args.seed,
            "flatfiles_root_win": str(root_win),
            "flatfiles_root_ch": root_ch,
        },
        "quote_result": quote_result,
        "trade_result": trade_result,
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    total_mismatches = quote_result["comparison"]["mismatch_rows"] + trade_result["comparison"]["mismatch_rows"]
    print("=" * 96, flush=True)
    print(f"DONE total_mismatches={total_mismatches} output={output_path}", flush=True)
    print("=" * 96, flush=True)
    if total_mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
