from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.clickhouse_compact_schema_codec_benchmark import (  # noqa: E402
    QUOTE_SCHEMA_STRING,
    TRADE_SCHEMA_STRING,
)
from research.mlops.clickhouse_delete_compact_audit_rows import (  # noqa: E402
    DeleteCandidate,
    default_clickhouse_url_with_network_fallback,
    env_status_keys,
    load_delete_candidates,
)
from research.mlops.clickhouse_ingest_sip_compact_codec import (  # noqa: E402
    DEFAULT_DATABASE,
    DEFAULT_MANIFEST_TABLE,
)
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    DEFAULT_FLATFILES_ROOT_WIN,
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    default_clickhouse_file_root,
    default_clickhouse_password,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_kinds,
    sql_string,
)


DEFAULT_AUDIT_STATUSES = "should_delete"


@dataclass(frozen=True, slots=True)
class SourceStats:
    rows: int
    min_sip_timestamp: int
    max_sip_timestamp: int
    null_or_zero_sip_timestamp_rows: int
    null_or_empty_ticker_rows: int
    null_or_zero_sequence_number_rows: int
    non_positive_price_rows: int
    non_positive_size_rows: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    key: str
    kind: str
    source_date: str
    source_file: str
    source_path_win: str
    source_path_ch: str
    audit_status: str
    expected_rows: int
    expected_min_sip_timestamp: int
    expected_max_sip_timestamp: int
    local_file_exists: bool
    file_bytes_manifest: int
    file_bytes_local: int
    polars_status: str
    clickhouse_file_status: str
    polars_stats: SourceStats | None
    clickhouse_file_stats: SourceStats | None
    validation_status: str
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate source CSV files referenced by compact SIP manifest audit labels before delete/retry."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--kinds", default="quotes,trades")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--audit-statuses", default=DEFAULT_AUDIT_STATUSES)
    parser.add_argument("--flatfiles-root-win", default=str(DEFAULT_FLATFILES_ROOT_WIN))
    parser.add_argument("--flatfiles-root-ch", default=default_clickhouse_file_root())
    parser.add_argument("--skip-clickhouse-file", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=0, help="Debug limit. 0 means all candidates.")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_source_validation"))
    return parser.parse_args()


def ch_path_to_win_path(source_path_ch: str, flatfiles_root_ch: str, flatfiles_root_win: str) -> Path:
    normalized_source = source_path_ch.replace("\\", "/")
    normalized_root = flatfiles_root_ch.replace("\\", "/").rstrip("/")
    if normalized_source.startswith(normalized_root + "/"):
        relative = normalized_source[len(normalized_root) + 1 :]
        return Path(flatfiles_root_win) / Path(*relative.split("/"))
    if normalized_source.startswith("/mnt/") and len(normalized_source) > 6:
        drive = normalized_source[5]
        rest = normalized_source[7:]
        return Path(f"{drive.upper()}:\\") / Path(*rest.split("/"))
    return Path(source_path_ch)


def collect_polars_lazy(lazy: Any) -> Any:
    try:
        return lazy.collect(engine="streaming")
    except (TypeError, ValueError):
        return lazy.collect(streaming=True)


def polars_stats(path: Path, kind: str) -> SourceStats:
    import polars as pl

    if kind == "quotes":
        price_expr = (
            (pl.col("bid_price").cast(pl.Float64, strict=False) <= 0)
            | (pl.col("ask_price").cast(pl.Float64, strict=False) <= 0)
        )
        size_expr = (
            (pl.col("bid_size").cast(pl.Float64, strict=False) <= 0)
            | (pl.col("ask_size").cast(pl.Float64, strict=False) <= 0)
        )
    else:
        price_expr = pl.col("price").cast(pl.Float64, strict=False) <= 0
        size_expr = pl.col("size").cast(pl.Float64, strict=False) <= 0

    sip = pl.col("sip_timestamp").cast(pl.UInt64, strict=False)
    seq = pl.col("sequence_number").cast(pl.UInt64, strict=False)
    ticker = pl.col("ticker").cast(pl.Utf8, strict=False)
    lazy = (
        pl.scan_csv(str(path), has_header=True, infer_schema_length=0, ignore_errors=True)
        .select(
            pl.len().alias("rows"),
            sip.min().fill_null(0).alias("min_sip_timestamp"),
            sip.max().fill_null(0).alias("max_sip_timestamp"),
            (sip.is_null() | (sip == 0)).sum().alias("null_or_zero_sip_timestamp_rows"),
            (ticker.is_null() | (ticker.str.len_chars() == 0)).sum().alias("null_or_empty_ticker_rows"),
            (seq.is_null() | (seq == 0)).sum().alias("null_or_zero_sequence_number_rows"),
            price_expr.fill_null(True).sum().alias("non_positive_price_rows"),
            size_expr.fill_null(True).sum().alias("non_positive_size_rows"),
        )
    )
    row = collect_polars_lazy(lazy).row(0, named=True)
    return SourceStats(
        rows=int(row["rows"] or 0),
        min_sip_timestamp=int(row["min_sip_timestamp"] or 0),
        max_sip_timestamp=int(row["max_sip_timestamp"] or 0),
        null_or_zero_sip_timestamp_rows=int(row["null_or_zero_sip_timestamp_rows"] or 0),
        null_or_empty_ticker_rows=int(row["null_or_empty_ticker_rows"] or 0),
        null_or_zero_sequence_number_rows=int(row["null_or_zero_sequence_number_rows"] or 0),
        non_positive_price_rows=int(row["non_positive_price_rows"] or 0),
        non_positive_size_rows=int(row["non_positive_size_rows"] or 0),
    )


def clickhouse_file_stats(client: ClickHouseHttpClient, source_path_ch: str, kind: str) -> SourceStats:
    schema = QUOTE_SCHEMA_STRING if kind == "quotes" else TRADE_SCHEMA_STRING
    if kind == "quotes":
        price_expr = "(toFloat64OrZero(bid_price) <= 0 OR toFloat64OrZero(ask_price) <= 0)"
        size_expr = "(toFloat64OrZero(bid_size) <= 0 OR toFloat64OrZero(ask_size) <= 0)"
    else:
        price_expr = "toFloat64OrZero(price) <= 0"
        size_expr = "toFloat64OrZero(size) <= 0"
    query = f"""
SELECT
    count(),
    if(count() = 0, 0, min(toUInt64OrZero(sip_timestamp))),
    if(count() = 0, 0, max(toUInt64OrZero(sip_timestamp))),
    countIf(toUInt64OrZero(sip_timestamp) = 0),
    countIf(ticker = ''),
    countIf(toUInt64OrZero(sequence_number) = 0),
    countIf({price_expr}),
    countIf({size_expr})
FROM file({sql_string(source_path_ch)}, 'CSVWithNames', {sql_string(schema)})
"""
    row = client.query_tsv(query).strip().split("\t")
    return SourceStats(
        rows=int(row[0] or 0),
        min_sip_timestamp=int(row[1] or 0),
        max_sip_timestamp=int(row[2] or 0),
        null_or_zero_sip_timestamp_rows=int(row[3] or 0),
        null_or_empty_ticker_rows=int(row[4] or 0),
        null_or_zero_sequence_number_rows=int(row[5] or 0),
        non_positive_price_rows=int(row[6] or 0),
        non_positive_size_rows=int(row[7] or 0),
    )


def stats_match_manifest(stats: SourceStats, candidate: DeleteCandidate) -> bool:
    return (
        stats.rows == candidate.expected_rows
        and stats.min_sip_timestamp == candidate.expected_min_sip_timestamp
        and stats.max_sip_timestamp == candidate.expected_max_sip_timestamp
    )


def choose_validation_status(
    candidate: DeleteCandidate,
    local_exists: bool,
    polars_status: str,
    ch_status: str,
    polars: SourceStats | None,
    ch: SourceStats | None,
) -> tuple[str, str]:
    if not local_exists:
        return "source_file_missing", "Windows source file path does not exist."
    if polars_status != "ok":
        return "source_file_unreadable_by_polars", "Polars could not fully scan the CSV/GZip source file."
    if polars is None:
        return "source_validation_incomplete", "Polars stats were unavailable."
    if not stats_match_manifest(polars, candidate):
        return "source_mismatch_manifest", "Polars source row/min/max SIP stats do not match manifest expected stats."
    if ch_status == "skipped":
        return "source_matches_manifest_polars_only", "Polars source stats match manifest; ClickHouse file() validation was skipped."
    if ch_status != "ok":
        return "source_matches_manifest_clickhouse_file_failed", "Polars stats match manifest, but ClickHouse file() validation failed."
    if ch is None:
        return "source_validation_incomplete", "ClickHouse file() stats were unavailable."
    if not stats_match_manifest(ch, candidate):
        return "source_mismatch_clickhouse_file", "ClickHouse file() stats do not match manifest expected stats."
    if ch.rows != polars.rows or ch.min_sip_timestamp != polars.min_sip_timestamp or ch.max_sip_timestamp != polars.max_sip_timestamp:
        return "source_reader_mismatch", "Polars and ClickHouse file() produced different row/min/max SIP stats."
    return "source_ok", "Source file is readable and row/min/max SIP stats match manifest expected stats."


def validate_candidate(
    client: ClickHouseHttpClient,
    candidate: DeleteCandidate,
    *,
    flatfiles_root_win: str,
    flatfiles_root_ch: str,
    skip_clickhouse_file: bool,
) -> ValidationResult:
    source_path_win = ch_path_to_win_path(candidate.source_path_ch, flatfiles_root_ch, flatfiles_root_win)
    local_exists = source_path_win.exists()
    local_bytes = source_path_win.stat().st_size if local_exists else 0
    polars_status = "not_run"
    clickhouse_status = "skipped" if skip_clickhouse_file else "not_run"
    polars_result: SourceStats | None = None
    clickhouse_result: SourceStats | None = None
    if local_exists:
        try:
            polars_result = polars_stats(source_path_win, candidate.kind)
            polars_status = "ok"
        except Exception as exc:  # noqa: BLE001
            polars_status = f"error: {exc!r}"
    if not skip_clickhouse_file:
        try:
            clickhouse_result = clickhouse_file_stats(client, candidate.source_path_ch, candidate.kind)
            clickhouse_status = "ok"
        except Exception as exc:  # noqa: BLE001
            clickhouse_status = f"error: {exc!r}"
    validation_status, note = choose_validation_status(
        candidate,
        local_exists,
        polars_status,
        clickhouse_status,
        polars_result,
        clickhouse_result,
    )
    return ValidationResult(
        key=candidate.key,
        kind=candidate.kind,
        source_date=candidate.source_date,
        source_file=candidate.source_file,
        source_path_win=str(source_path_win),
        source_path_ch=candidate.source_path_ch,
        audit_status=candidate.audit_status,
        expected_rows=candidate.expected_rows,
        expected_min_sip_timestamp=candidate.expected_min_sip_timestamp,
        expected_max_sip_timestamp=candidate.expected_max_sip_timestamp,
        local_file_exists=local_exists,
        file_bytes_manifest=candidate.file_bytes,
        file_bytes_local=local_bytes,
        polars_status=polars_status,
        clickhouse_file_status=clickhouse_status,
        polars_stats=polars_result,
        clickhouse_file_stats=clickhouse_result,
        validation_status=validation_status,
        note=note,
    )


def to_jsonable(result: ValidationResult) -> dict[str, object]:
    payload = asdict(result)
    return payload


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def format_optional_int(value: int | None) -> str:
    return "None" if value is None else f"{value:,}"


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    kinds = parse_kinds(args.kinds)
    audit_statuses = [item.strip() for item in args.audit_statuses.split(",") if item.strip()]
    if not audit_statuses:
        raise SystemExit("--audit-statuses resolved to an empty list")
    run_id = "compact_source_validate_" + time.strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.output_root_win) / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print("=" * 96, flush=True)
    print("Compact SIP audit source validator", flush=True)
    print(f"database={args.database} manifest_table={args.manifest_table}", flush=True)
    print(f"kinds={kinds} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(f"audit_statuses={audit_statuses} skip_clickhouse_file={args.skip_clickhouse_file}", flush=True)
    print(f"flatfiles_root_win={args.flatfiles_root_win}", flush=True)
    print(f"flatfiles_root_ch={args.flatfiles_root_ch}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    candidates = load_delete_candidates(
        client,
        args.database,
        args.manifest_table,
        kinds,
        args.start_date,
        args.end_date,
        audit_statuses,
    )
    candidates = [candidate for candidate in candidates if candidate.audit_status == "should_delete"]
    if args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]
    print(f"Loaded source validation candidates={len(candidates):,}", flush=True)

    started_at = time.perf_counter()
    summary: dict[str, int] = {}
    for index, candidate in enumerate(candidates, start=1):
        result = validate_candidate(
            client,
            candidate,
            flatfiles_root_win=args.flatfiles_root_win,
            flatfiles_root_ch=args.flatfiles_root_ch,
            skip_clickhouse_file=args.skip_clickhouse_file,
        )
        summary[result.validation_status] = summary.get(result.validation_status, 0) + 1
        append_jsonl(report_path, {"type": "validation", **to_jsonable(result)})
        elapsed = time.perf_counter() - started_at
        print(
            f"VALIDATE [{index:,}/{len(candidates):,}] {result.validation_status} {result.key} "
            f"expected_rows={result.expected_rows:,} "
            f"polars_rows={format_optional_int(None if result.polars_stats is None else result.polars_stats.rows)} "
            f"clickhouse_rows={format_optional_int(None if result.clickhouse_file_stats is None else result.clickhouse_file_stats.rows)} "
            f"elapsed_min={elapsed / 60:.1f}",
            flush=True,
        )

    print("=" * 96, flush=True)
    print(f"SUMMARY candidates={len(candidates):,} {summary}", flush=True)
    print(f"report={report_path}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
