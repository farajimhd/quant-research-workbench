from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.rolling_loader.run_profile_ticker_month_loader import DEFAULT_PROFILE_CONFIG, DEFAULT_PROFILE_REPORT_PATH
from research.mlops.rolling_loader.ticker_month_cache import DEFAULT_TICKER_MONTH_CACHE_ROOT, jsonable, write_json_atomic
from research.mlops.rolling_loader.ticker_month_dataset import (
    AsyncTickerMonthBatchLoader,
    LoadedTickerMonthPart,
    TickerMonthLoaderConfig,
    TickerMonthPartPlan,
    TickerMonthPartReader,
    TickerMonthTrainingBatch,
    _label_values_for_origin,
    _part_key,
)


DEFAULT_AUDIT_REPORT_PATH = DEFAULT_PROFILE_REPORT_PATH.with_name("ticker_month_loader_batch_audit.json")


@dataclass(slots=True)
class AuditIssue:
    severity: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoaderBatchAuditResult:
    ok: bool
    status: str
    summary: dict[str, Any]
    report_path: str


@dataclass(frozen=True, slots=True)
class LoaderBatchAuditConfig:
    loader_config: TickerMonthLoaderConfig
    batches: int = 2
    samples_per_batch: int = 4
    seed: int = 17
    check_determinism: bool = True
    check_resume: bool = True
    report_path: Path = DEFAULT_AUDIT_REPORT_PATH


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit materialized ticker/month loader batches against the SSD package files.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_TICKER_MONTH_CACHE_ROOT)
    parser.add_argument("--cache-id", default=DEFAULT_PROFILE_CONFIG["cache_id"])
    parser.add_argument("--split", default=DEFAULT_PROFILE_CONFIG["split"])
    parser.add_argument("--month", action="append", default=None)
    parser.add_argument("--start-utc", default=DEFAULT_PROFILE_CONFIG["start_utc"])
    parser.add_argument("--end-utc", default=DEFAULT_PROFILE_CONFIG["end_utc"])
    parser.add_argument("--tickers", default=DEFAULT_PROFILE_CONFIG["tickers"])
    parser.add_argument("--batch-size", type=int, default=DEFAULT_PROFILE_CONFIG["batch_size"])
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_PROFILE_CONFIG["seed"])
    parser.add_argument("--data-groups", default=DEFAULT_PROFILE_CONFIG["data_groups"])
    parser.add_argument("--event-output-mode", choices=("none", "raw_flat", "raw_stream", "raw_windows", "encoded_uint8"), default=DEFAULT_PROFILE_CONFIG["event_output_mode"])
    parser.add_argument("--event-columns", default=DEFAULT_PROFILE_CONFIG["event_columns"])
    parser.add_argument("--suppress-event-columns", default=DEFAULT_PROFILE_CONFIG["suppress_event_columns"])
    parser.add_argument("--events-per-window", type=int, default=DEFAULT_PROFILE_CONFIG["events_per_window"])
    parser.add_argument("--event-stream-length", type=int, default=DEFAULT_PROFILE_CONFIG["event_stream_length"])
    parser.add_argument("--event-stream-chunk-size", type=int, default=DEFAULT_PROFILE_CONFIG["event_stream_chunk_size"])
    parser.add_argument("--context-chunks", type=int, default=DEFAULT_PROFILE_CONFIG["context_chunks"])
    parser.add_argument("--context-stride-events", type=int, default=DEFAULT_PROFILE_CONFIG["context_stride_events"])
    parser.add_argument("--flat-coverage-events", type=int, default=DEFAULT_PROFILE_CONFIG["flat_coverage_events"])
    parser.add_argument("--loaded-parts-per-group", type=int, default=DEFAULT_PROFILE_CONFIG["loaded_parts_per_group"])
    parser.add_argument("--read-workers", type=int, default=DEFAULT_PROFILE_CONFIG["read_workers"])
    parser.add_argument("--materialize-workers", type=int, default=DEFAULT_PROFILE_CONFIG["materialize_workers"])
    parser.add_argument("--materialize-chunk-size", type=int, default=DEFAULT_PROFILE_CONFIG["materialize_chunk_size"])
    parser.add_argument("--dataset-id", default=DEFAULT_PROFILE_CONFIG["dataset_id"])
    parser.add_argument("--sample-fraction", type=float, default=DEFAULT_PROFILE_CONFIG["sample_fraction"])
    parser.add_argument("--sample-hash-modulus", type=int, default=DEFAULT_PROFILE_CONFIG["sample_hash_modulus"])
    parser.add_argument("--sample-hash-buckets", default=DEFAULT_PROFILE_CONFIG["sample_hash_buckets"])
    parser.add_argument("--max-origins-per-epoch", type=int, default=DEFAULT_PROFILE_CONFIG["max_origins_per_epoch"])
    parser.add_argument("--samples-per-batch", type=int, default=4)
    parser.add_argument("--include-external-context", action="store_true")
    parser.add_argument("--no-strict-audit", action="store_true")
    parser.add_argument("--no-check-determinism", action="store_true")
    parser.add_argument("--no-check-resume", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_AUDIT_REPORT_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_audit(
        LoaderBatchAuditConfig(
            loader_config=_loader_config_from_args(args),
            batches=max(1, int(args.batches)),
            samples_per_batch=max(1, int(args.samples_per_batch)),
            seed=int(args.seed),
            check_determinism=not bool(args.no_check_determinism),
            check_resume=not bool(args.no_check_resume),
            report_path=Path(args.report_path),
        )
    )
    print(json.dumps(result.summary, indent=2, sort_keys=True), flush=True)
    print(f"LOADER_BATCH_AUDIT {result.status} report={result.report_path}", flush=True)
    return 0 if result.ok else 2


def run_audit(config: LoaderBatchAuditConfig) -> LoaderBatchAuditResult:
    issues: list[AuditIssue] = []
    loader = AsyncTickerMonthBatchLoader(config.loader_config)
    part_map = {_part_key(plan): plan for plan in loader.index.parts}
    part_cache: dict[str, LoadedTickerMonthPart] = {}
    context_groups = tuple(group for group in config.loader_config.data_groups if group in {"ticker_news_tokens", "sec_filing_tokens", "xbrl", "daily_bars"})
    reader_groups = ("events", "intraday_labels", *context_groups)
    reader = TickerMonthPartReader(reader_groups, include_external_context=bool(config.loader_config.include_external_context or context_groups))
    rng = np.random.default_rng(int(config.seed))
    seen: set[tuple[str, int]] = set()
    totals = {
        "batches_checked": 0,
        "samples_checked": 0,
        "raw_stream_rows_checked": 0,
        "label_rows_checked": 0,
        "context_parts_checked": 0,
        "duplicate_identities": 0,
    }
    first_batch: TickerMonthTrainingBatch | None = None
    second_batch: TickerMonthTrainingBatch | None = None
    iterator = loader.iter_batches()
    for batch_index in range(max(1, int(config.batches))):
        try:
            batch = next(iterator)
        except StopIteration:
            break
        if batch_index == 0:
            first_batch = batch
        elif batch_index == 1:
            second_batch = batch
        totals["batches_checked"] += 1
        _check_batch_shapes(batch, issues, batch_index=batch_index)
        _check_duplicate_identities(batch, seen, issues, totals, batch_index=batch_index)
        rows = _sample_batch_rows(batch.sample_count, int(config.samples_per_batch), rng)
        for row in rows:
            _audit_batch_row(
                batch,
                int(row),
                batch_index=batch_index,
                part_map=part_map,
                part_cache=part_cache,
                reader=reader,
                issues=issues,
                totals=totals,
            )
    state_after_first = None
    if config.check_determinism:
        _check_deterministic_first_batch(config.loader_config, first_batch, issues)
    if config.check_resume:
        state_after_first = _check_resume_after_first_batch(config.loader_config, second_batch, issues)
    status = "passed" if not any(issue.severity == "error" for issue in issues) else "failed"
    report = {
        "status": status,
        "ok": status == "passed",
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "config": jsonable(asdict(config.loader_config)),
        "summary": totals,
        "loader_state_after_audit": loader.state_dict(),
        "resume_state_after_first_batch": state_after_first,
        "issues": [asdict(issue) for issue in issues],
    }
    write_json_atomic(Path(config.report_path), report)
    return LoaderBatchAuditResult(
        ok=status == "passed",
        status=status,
        summary={"totals": totals, "issues": _issue_counts(issues)},
        report_path=str(config.report_path),
    )


def _loader_config_from_args(args: argparse.Namespace) -> TickerMonthLoaderConfig:
    months = tuple(str(month) for month in (args.month if args.month is not None else DEFAULT_PROFILE_CONFIG["months"]))
    return TickerMonthLoaderConfig(
        cache_root=Path(args.cache_root) / str(args.cache_id),
        split=str(args.split),
        start_utc=str(args.start_utc),
        end_utc=str(args.end_utc),
        months=months,
        tickers=tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip()),
        batch_size=max(1, int(args.batch_size)),
        seed=int(args.seed),
        data_groups=tuple(item.strip() for item in str(args.data_groups).split(",") if item.strip()),
        event_output_mode=str(args.event_output_mode),
        event_columns=tuple(item.strip() for item in str(args.event_columns).split(",") if item.strip()),
        suppress_event_columns=tuple(item.strip() for item in str(args.suppress_event_columns).split(",") if item.strip()),
        events_per_window=max(1, int(args.events_per_window)),
        event_stream_length=max(1, int(args.event_stream_length)),
        event_stream_chunk_size=max(1, int(args.event_stream_chunk_size)),
        context_chunks=max(0, int(args.context_chunks)),
        context_stride_events=max(1, int(args.context_stride_events)),
        flat_coverage_events=max(0, int(args.flat_coverage_events)),
        loaded_parts_per_group=max(1, int(args.loaded_parts_per_group)),
        read_workers=max(1, int(args.read_workers)),
        materialize_workers=max(1, int(args.materialize_workers)),
        materialize_chunk_size=max(0, int(args.materialize_chunk_size)),
        max_batches=0,
        include_external_context=bool(args.include_external_context),
        strict_audit=not bool(args.no_strict_audit),
        dataset_id=str(args.dataset_id),
        sample_fraction=max(0.0, min(1.0, float(args.sample_fraction))),
        sample_hash_modulus=max(0, int(args.sample_hash_modulus)),
        sample_hash_buckets=tuple(int(item.strip()) for item in str(args.sample_hash_buckets).split(",") if item.strip()),
        max_origins_per_epoch=max(0, int(args.max_origins_per_epoch)),
    )


def _check_batch_shapes(batch: TickerMonthTrainingBatch, issues: list[AuditIssue], *, batch_index: int) -> None:
    n = int(batch.sample_count)
    arrays = {
        "ticker": batch.ticker,
        "origin_ordinal": batch.origin_ordinal,
        "origin_timestamp_us": batch.origin_timestamp_us,
        "source_part_key": batch.source_part_key,
    }
    for name, array in arrays.items():
        if int(array.shape[0]) != n:
            issues.append(AuditIssue("error", "batch_shape_mismatch", f"{name} row count does not match sample count.", {"batch": batch_index, "name": name, "shape": list(array.shape), "samples": n}))
    if batch.raw_event_stream.size and int(batch.raw_event_stream.shape[0]) != n:
        issues.append(AuditIssue("error", "raw_stream_shape_mismatch", "raw_event_stream row count does not match sample count.", {"batch": batch_index, "shape": list(batch.raw_event_stream.shape), "samples": n}))
    for name, values in batch.intraday_labels.items():
        if int(values.shape[0]) != n:
            issues.append(AuditIssue("error", "label_shape_mismatch", f"{name} row count does not match sample count.", {"batch": batch_index, "name": name, "shape": list(values.shape), "samples": n}))


def _check_duplicate_identities(batch: TickerMonthTrainingBatch, seen: set[tuple[str, int]], issues: list[AuditIssue], totals: dict[str, int], *, batch_index: int) -> None:
    tickers = np.asarray(batch.ticker).reshape(-1)
    ordinals = np.asarray(batch.origin_ordinal, dtype=np.int64).reshape(-1)
    local: set[tuple[str, int]] = set()
    for row, (ticker, ordinal) in enumerate(zip(tickers, ordinals)):
        identity = (str(ticker), int(ordinal))
        if identity in local or identity in seen:
            totals["duplicate_identities"] += 1
            issues.append(AuditIssue("error", "duplicate_identity", "Batch stream emitted a duplicate sample identity.", {"batch": batch_index, "row": int(row), "ticker": identity[0], "origin_ordinal": identity[1]}))
        local.add(identity)
        seen.add(identity)


def _sample_batch_rows(sample_count: int, samples: int, rng: np.random.Generator) -> np.ndarray:
    n = int(sample_count)
    if n <= 0:
        return np.asarray([], dtype=np.int64)
    if n <= int(samples):
        return np.arange(n, dtype=np.int64)
    return np.sort(rng.choice(n, size=int(samples), replace=False).astype(np.int64, copy=False))


def _audit_batch_row(
    batch: TickerMonthTrainingBatch,
    row: int,
    *,
    batch_index: int,
    part_map: Mapping[str, TickerMonthPartPlan],
    part_cache: dict[str, LoadedTickerMonthPart],
    reader: TickerMonthPartReader,
    issues: list[AuditIssue],
    totals: dict[str, int],
) -> None:
    part_key = str(batch.source_part_key[row])
    plan = part_map.get(part_key)
    if plan is None:
        issues.append(AuditIssue("error", "unknown_source_part", "Batch source_part_key does not resolve to a cache part.", {"batch": batch_index, "row": row, "source_part_key": part_key}))
        return
    part = _loaded_part(part_key, plan, part_cache, reader)
    origin_ordinal = int(batch.origin_ordinal[row])
    origin_timestamp_us = int(batch.origin_timestamp_us[row])
    origin_idx = _find_origin_row(part, origin_ordinal, issues, batch_index=batch_index, row=row, part_key=part_key)
    if origin_idx is None:
        return
    origin_row = part.origins.row(origin_idx, named=True)
    if str(origin_row.get("ticker")) != str(batch.ticker[row]):
        issues.append(AuditIssue("error", "ticker_mismatch", "Batch ticker does not match origin row.", {"batch": batch_index, "row": row, "source_part_key": part_key, "batch_ticker": str(batch.ticker[row]), "origin_ticker": str(origin_row.get("ticker"))}))
    if int(origin_row.get("origin_timestamp_us")) != origin_timestamp_us:
        issues.append(AuditIssue("error", "origin_timestamp_mismatch", "Batch timestamp does not match origin row.", {"batch": batch_index, "row": row, "source_part_key": part_key, "batch_ts": origin_timestamp_us, "origin_ts": int(origin_row.get("origin_timestamp_us"))}))
    event_offset = int(origin_row.get("event_row_offset"))
    _check_origin_event_row(part, event_offset, origin_ordinal, origin_timestamp_us, issues, batch_index=batch_index, row=row, part_key=part_key)
    if batch.raw_event_stream.size:
        _check_raw_event_stream(batch, row, part, event_offset, issues, totals, batch_index=batch_index, part_key=part_key)
    if batch.intraday_labels:
        _check_intraday_labels(batch, row, part, origin_ordinal, issues, totals, batch_index=batch_index, part_key=part_key)
    _check_context_files(part, origin_timestamp_us, issues, totals, batch_index=batch_index, row=row, part_key=part_key)
    totals["samples_checked"] += 1


def _loaded_part(part_key: str, plan: TickerMonthPartPlan, cache: dict[str, LoadedTickerMonthPart], reader: TickerMonthPartReader) -> LoadedTickerMonthPart:
    if part_key not in cache:
        cache[part_key] = reader.load_payload(reader.load_origins(plan))
    return cache[part_key]


def _find_origin_row(part: LoadedTickerMonthPart, origin_ordinal: int, issues: list[AuditIssue], *, batch_index: int, row: int, part_key: str) -> int | None:
    ordinals = part.origin_array("origin_ordinal").astype(np.int64, copy=False)
    idx = int(np.searchsorted(ordinals, int(origin_ordinal), side="left"))
    if idx >= int(ordinals.shape[0]) or int(ordinals[idx]) != int(origin_ordinal):
        issues.append(AuditIssue("error", "origin_missing", "Batch origin ordinal does not exist in source origins.", {"batch": batch_index, "row": row, "source_part_key": part_key, "origin_ordinal": int(origin_ordinal)}))
        return None
    return idx


def _check_origin_event_row(part: LoadedTickerMonthPart, event_offset: int, origin_ordinal: int, origin_timestamp_us: int, issues: list[AuditIssue], *, batch_index: int, row: int, part_key: str) -> None:
    if part.events is None or event_offset < 0 or event_offset >= int(part.events.height):
        issues.append(AuditIssue("error", "event_offset_out_of_bounds", "Origin event_row_offset is outside event table.", {"batch": batch_index, "row": row, "source_part_key": part_key, "event_row_offset": event_offset}))
        return
    event_row = part.events.row(event_offset, named=True)
    if int(event_row.get("ordinal")) != int(origin_ordinal):
        issues.append(AuditIssue("error", "origin_event_mismatch", "event_row_offset does not point to origin ordinal.", {"batch": batch_index, "row": row, "source_part_key": part_key, "event_row_offset": event_offset, "event_ordinal": int(event_row.get("ordinal")), "origin_ordinal": int(origin_ordinal)}))
    event_ts = int(event_row.get("timestamp_us"))
    if event_ts != int(origin_timestamp_us):
        issues.append(AuditIssue("error", "origin_event_timestamp_mismatch", "Origin timestamp does not match event row timestamp.", {"batch": batch_index, "row": row, "source_part_key": part_key, "event_ts": event_ts, "origin_ts": int(origin_timestamp_us)}))


def _check_raw_event_stream(batch: TickerMonthTrainingBatch, row: int, part: LoadedTickerMonthPart, event_offset: int, issues: list[AuditIssue], totals: dict[str, int], *, batch_index: int, part_key: str) -> None:
    stream = batch.raw_event_stream[row]
    columns = tuple(batch.raw_event_stream_feature_names)
    if not columns:
        issues.append(AuditIssue("error", "raw_stream_columns_missing", "raw_event_stream is present without feature names.", {"batch": batch_index, "row": row, "source_part_key": part_key}))
        return
    length = int(stream.shape[0])
    start = int(event_offset) - length + 1
    end = int(event_offset) + 1
    if start < 0:
        issues.append(AuditIssue("error", "raw_stream_start_out_of_bounds", "Raw stream starts before loaded event table.", {"batch": batch_index, "row": row, "source_part_key": part_key, "start": start, "event_offset": int(event_offset)}))
        return
    event_ordinals = part.event_array("ordinal").astype(np.int64, copy=False)[start:end]
    if int(event_ordinals.shape[0]) != length:
        issues.append(AuditIssue("error", "raw_stream_length_mismatch", "Source event stream length does not match batch stream.", {"batch": batch_index, "row": row, "source_part_key": part_key, "expected": length, "actual": int(event_ordinals.shape[0])}))
        return
    if length > 1 and not bool(np.all(np.diff(event_ordinals) == 1)):
        issues.append(AuditIssue("error", "raw_stream_ordinal_gap", "Source stream contains an ordinal gap.", {"batch": batch_index, "row": row, "source_part_key": part_key, "start": start, "end": end}))
    expected = part.events.select(list(columns)).slice(start, length).to_numpy().astype(np.float32, copy=False)
    if expected.shape != stream.shape or not bool(np.allclose(expected, stream, rtol=0.0, atol=0.0, equal_nan=True)):
        issues.append(AuditIssue("error", "raw_stream_value_mismatch", "Batch raw_event_stream does not match source events.", {"batch": batch_index, "row": row, "source_part_key": part_key, "shape": list(stream.shape), "expected_shape": list(expected.shape)}))
    totals["raw_stream_rows_checked"] += 1


def _check_intraday_labels(batch: TickerMonthTrainingBatch, row: int, part: LoadedTickerMonthPart, origin_ordinal: int, issues: list[AuditIssue], totals: dict[str, int], *, batch_index: int, part_key: str) -> None:
    if part.labels is None:
        issues.append(AuditIssue("error", "labels_not_loaded", "Batch has labels but source labels were not loaded.", {"batch": batch_index, "row": row, "source_part_key": part_key}))
        return
    expected_count = next(iter(batch.intraday_labels.values())).shape[1]
    values = _label_values_for_origin(part.labels, int(origin_ordinal), int(expected_count))
    if values is None:
        issues.append(AuditIssue("error", "label_origin_missing", "Source labels missing for batch origin.", {"batch": batch_index, "row": row, "source_part_key": part_key, "origin_ordinal": int(origin_ordinal)}))
        return
    for key, expected in values.items():
        if key not in batch.intraday_labels:
            continue
        actual = batch.intraday_labels[key][row]
        expected = expected.astype(actual.dtype, copy=False)
        if actual.shape != expected.shape or not bool(np.array_equal(actual, expected)):
            issues.append(AuditIssue("error", "label_value_mismatch", f"Batch intraday label {key} does not match source labels.", {"batch": batch_index, "row": row, "source_part_key": part_key, "origin_ordinal": int(origin_ordinal), "label": key}))
    if batch.future_intraday_bars.size:
        _check_future_bar_projection(batch, row, values, issues, batch_index=batch_index, part_key=part_key, origin_ordinal=origin_ordinal)
    totals["label_rows_checked"] += 1


def _check_future_bar_projection(batch: TickerMonthTrainingBatch, row: int, labels: Mapping[str, np.ndarray], issues: list[AuditIssue], *, batch_index: int, part_key: str, origin_ordinal: int) -> None:
    bars = batch.future_intraday_bars[row]
    if bars.shape[0] <= 0 or bars.shape[1] < 5:
        return
    expected = np.zeros_like(bars)
    expected[:, 0] = labels["price_primary_int"].astype(np.float32, copy=False)
    expected[:, 1] = labels["price_primary_int"].astype(np.float32, copy=False)
    expected[:, 2] = labels["price_primary_int"].astype(np.float32, copy=False)
    expected[:, 3] = labels["price_secondary_int"].astype(np.float32, copy=False)
    expected[:, 4] = labels["size_primary_sum"].astype(np.float32, copy=False)
    if not bool(np.allclose(expected, bars, rtol=0.0, atol=0.0, equal_nan=True)):
        issues.append(AuditIssue("error", "future_bar_projection_mismatch", "future_intraday_bars do not match label projection.", {"batch": batch_index, "source_part_key": part_key, "origin_ordinal": int(origin_ordinal)}))


def _check_context_files(part: LoadedTickerMonthPart, origin_timestamp_us: int, issues: list[AuditIssue], totals: dict[str, int], *, batch_index: int, row: int, part_key: str) -> None:
    if not part.context:
        return
    for name, frame in part.context.items():
        totals["context_parts_checked"] += 1
        if "timestamp_us" not in frame.columns or int(frame.height) <= 0:
            continue
        timestamps = frame.get_column("timestamp_us").to_numpy().astype(np.int64, copy=False)
        if np.any(timestamps > int(origin_timestamp_us)) and np.any(timestamps <= int(origin_timestamp_us)):
            continue
        if np.any(timestamps <= int(origin_timestamp_us)):
            continue
        issues.append(AuditIssue("warning", "context_no_asof_rows", "Context file has rows, but none are as-of this sampled origin.", {"batch": batch_index, "row": row, "source_part_key": part_key, "context": str(name), "origin_timestamp_us": int(origin_timestamp_us), "min_context_timestamp_us": int(timestamps.min())}))


def _check_deterministic_first_batch(config: TickerMonthLoaderConfig, first_batch: TickerMonthTrainingBatch | None, issues: list[AuditIssue]) -> None:
    if first_batch is None:
        issues.append(AuditIssue("error", "determinism_no_batch", "Cannot check determinism because no first batch was emitted."))
        return
    other = AsyncTickerMonthBatchLoader(config)
    try:
        repeat = next(other.iter_batches())
    except StopIteration:
        issues.append(AuditIssue("error", "determinism_no_repeat_batch", "Second loader emitted no first batch."))
        return
    _compare_batches(first_batch, repeat, issues, code="determinism_mismatch", message="Same config/seed did not produce the same first batch.")


def _check_resume_after_first_batch(config: TickerMonthLoaderConfig, expected_second_batch: TickerMonthTrainingBatch | None, issues: list[AuditIssue]) -> dict[str, Any] | None:
    if expected_second_batch is None:
        issues.append(AuditIssue("warning", "resume_no_second_batch", "Cannot check resume because audit did not emit a second continuous batch."))
        return None
    base = AsyncTickerMonthBatchLoader(config)
    iterator = base.iter_batches()
    try:
        next(iterator)
    except StopIteration:
        issues.append(AuditIssue("error", "resume_no_first_batch", "Cannot check resume because base loader emitted no first batch."))
        return None
    state = base.state_dict()
    if int(state.get("origin_cursor") or 0) <= 0 and int(state.get("package_position") or 0) == 0:
        issues.append(AuditIssue("error", "resume_state_not_advanced", "Loader state did not advance after first yielded batch.", {"state": state}))
    resumed = AsyncTickerMonthBatchLoader(config)
    resumed.load_state_dict(state)
    try:
        resumed_second = next(resumed.iter_batches())
    except StopIteration:
        issues.append(AuditIssue("error", "resume_no_resumed_batch", "Resumed loader emitted no next batch."))
        return state
    _compare_batches(expected_second_batch, resumed_second, issues, code="resume_mismatch", message="Resumed loader did not produce the same next batch as continuous loading.")
    return state


def _compare_batches(left: TickerMonthTrainingBatch, right: TickerMonthTrainingBatch, issues: list[AuditIssue], *, code: str, message: str) -> None:
    if not np.array_equal(left.ticker, right.ticker) or not np.array_equal(left.origin_ordinal, right.origin_ordinal) or not np.array_equal(left.origin_timestamp_us, right.origin_timestamp_us):
        issues.append(AuditIssue("error", code, message, {"field": "identity", "left_samples": int(left.sample_count), "right_samples": int(right.sample_count)}))
        return
    if left.raw_event_stream.size or right.raw_event_stream.size:
        if left.raw_event_stream.shape != right.raw_event_stream.shape or not bool(np.array_equal(left.raw_event_stream, right.raw_event_stream)):
            issues.append(AuditIssue("error", code, message, {"field": "raw_event_stream", "left_shape": list(left.raw_event_stream.shape), "right_shape": list(right.raw_event_stream.shape)}))
    for key in set(left.intraday_labels).union(right.intraday_labels):
        if key not in left.intraday_labels or key not in right.intraday_labels or not np.array_equal(left.intraday_labels[key], right.intraday_labels[key]):
            issues.append(AuditIssue("error", code, message, {"field": f"intraday_labels.{key}"}))


def _issue_counts(issues: Sequence[AuditIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
