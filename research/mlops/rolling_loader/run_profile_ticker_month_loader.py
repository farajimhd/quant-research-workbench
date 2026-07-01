from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.rolling_loader.ticker_month_cache import DEFAULT_TICKER_MONTH_CACHE_ROOT, jsonable
from research.mlops.rolling_loader.ticker_month_dataset import AsyncTickerMonthBatchLoader, TickerMonthLoaderConfig
from research.mlops.rolling_loader.streaming_training import current_rss_mib


DEFAULT_PROFILE_REPORT_PATH = Path("D:/market-data/prepared/data_provider_profiles/ticker_month_loader_full_xy_xbrl_profile.jsonl")
DEFAULT_PROFILE_STATE_PATH = Path("D:/market-data/prepared/data_provider_profiles/ticker_month_loader_full_xy_xbrl_state.json")
DEFAULT_PROFILE_AUDIT_REPORT_PATH = DEFAULT_PROFILE_REPORT_PATH.with_name("ticker_month_loader_full_xy_xbrl_batch_audit.json")
DEFAULT_PROFILE_CONFIG: dict[str, Any] = {
    "cache_id": "train_201902_201907_ticker_month",
    "split": "train",
    "months": ("2019-02",),
    "start_utc": "",
    "end_utc": "",
    "tickers": "",
    "batch_size": 4096,
    "batches": 16,
    "seed": 17,
    "data_groups": "events,intraday_labels,corporate_action_labels,daily_bars,global_daily_bars,ticker_news_embeddings,market_news_embeddings,sec_filing_embeddings,xbrl,corporate_actions",
    "event_output_mode": "raw_stream",
    "event_columns": "",
    "suppress_event_columns": "ticker_id,ordinal,timestamp_us",
    "events_per_window": 128,
    "event_stream_length": 1024,
    "event_stream_chunk_size": 128,
    "context_chunks": 32,
    "context_stride_events": 64,
    "flat_coverage_events": 0,
    "ticker_news_max_items": 8,
    "market_news_max_items": 16,
    "sec_filing_max_items": 4,
    "xbrl_max_items": 4096,
    "corporate_action_max_items": 128,
    "corporate_action_label_days": "1,2,3,7,28",
    "ticker_news_token_chunks": 2,
    "market_news_token_chunks": 2,
    "sec_filing_token_chunks": 8,
    "text_max_tokens": 1024,
    "text_embedding_dim": 1024,
    "ticker_daily_bar_offsets": "1,2,3,7,14,28,40,200",
    "global_daily_bar_offsets": "1,2,7",
    "daily_bar_completion_lag_hours": 30.0,
    "loaded_parts_per_group": 8,
    "read_workers": 4,
    "materialize_workers": 16,
    "materialize_chunk_size": 512,
    "dataset_id": "bench_small_201902_v1",
    "sample_fraction": 1.0,
    "sample_hash_modulus": 0,
    "sample_hash_buckets": "",
    "max_origins_per_epoch": 1_000_000,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile the ticker/month SSD cache training loader.")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_TICKER_MONTH_CACHE_ROOT)
    parser.add_argument("--cache-id", default=DEFAULT_PROFILE_CONFIG["cache_id"])
    parser.add_argument("--split", default=DEFAULT_PROFILE_CONFIG["split"])
    parser.add_argument("--month", action="append", default=None)
    parser.add_argument("--start-utc", default=DEFAULT_PROFILE_CONFIG["start_utc"])
    parser.add_argument("--end-utc", default=DEFAULT_PROFILE_CONFIG["end_utc"])
    parser.add_argument("--tickers", default=DEFAULT_PROFILE_CONFIG["tickers"])
    parser.add_argument("--batch-size", type=int, default=DEFAULT_PROFILE_CONFIG["batch_size"])
    parser.add_argument("--batches", type=int, default=DEFAULT_PROFILE_CONFIG["batches"])
    parser.add_argument("--seed", type=int, default=DEFAULT_PROFILE_CONFIG["seed"])
    parser.add_argument("--data-groups", default=DEFAULT_PROFILE_CONFIG["data_groups"])
    parser.add_argument("--event-output-mode", choices=("none", "raw_flat", "raw_stream", "raw_windows", "encoded_uint8"), default=DEFAULT_PROFILE_CONFIG["event_output_mode"])
    parser.add_argument("--event-columns", default=DEFAULT_PROFILE_CONFIG["event_columns"], help="Comma-separated event columns to emit. Empty means all cached numeric event columns after suppression.")
    parser.add_argument(
        "--suppress-event-columns",
        default=DEFAULT_PROFILE_CONFIG["suppress_event_columns"],
        help="Comma-separated cached event columns to suppress from raw event outputs.",
    )
    parser.add_argument("--events-per-window", type=int, default=DEFAULT_PROFILE_CONFIG["events_per_window"])
    parser.add_argument("--event-stream-length", type=int, default=DEFAULT_PROFILE_CONFIG["event_stream_length"])
    parser.add_argument("--event-stream-chunk-size", type=int, default=DEFAULT_PROFILE_CONFIG["event_stream_chunk_size"])
    parser.add_argument("--context-chunks", type=int, default=DEFAULT_PROFILE_CONFIG["context_chunks"])
    parser.add_argument("--context-stride-events", type=int, default=DEFAULT_PROFILE_CONFIG["context_stride_events"])
    parser.add_argument("--flat-coverage-events", type=int, default=DEFAULT_PROFILE_CONFIG["flat_coverage_events"])
    parser.add_argument("--ticker-news-max-items", type=int, default=DEFAULT_PROFILE_CONFIG["ticker_news_max_items"])
    parser.add_argument("--market-news-max-items", type=int, default=DEFAULT_PROFILE_CONFIG["market_news_max_items"])
    parser.add_argument("--sec-filing-max-items", type=int, default=DEFAULT_PROFILE_CONFIG["sec_filing_max_items"])
    parser.add_argument("--xbrl-max-items", type=int, default=DEFAULT_PROFILE_CONFIG["xbrl_max_items"])
    parser.add_argument("--corporate-action-max-items", type=int, default=DEFAULT_PROFILE_CONFIG["corporate_action_max_items"])
    parser.add_argument("--corporate-action-label-days", default=DEFAULT_PROFILE_CONFIG["corporate_action_label_days"])
    parser.add_argument("--ticker-news-token-chunks", type=int, default=DEFAULT_PROFILE_CONFIG["ticker_news_token_chunks"])
    parser.add_argument("--market-news-token-chunks", type=int, default=DEFAULT_PROFILE_CONFIG["market_news_token_chunks"])
    parser.add_argument("--sec-filing-token-chunks", type=int, default=DEFAULT_PROFILE_CONFIG["sec_filing_token_chunks"])
    parser.add_argument("--text-max-tokens", type=int, default=DEFAULT_PROFILE_CONFIG["text_max_tokens"])
    parser.add_argument("--text-embedding-dim", type=int, default=DEFAULT_PROFILE_CONFIG["text_embedding_dim"])
    parser.add_argument("--ticker-daily-bar-offsets", default=DEFAULT_PROFILE_CONFIG["ticker_daily_bar_offsets"], help="Comma-separated completed daily-bar offsets for ticker macro context.")
    parser.add_argument("--global-daily-bar-offsets", default=DEFAULT_PROFILE_CONFIG["global_daily_bar_offsets"], help="Comma-separated completed daily-bar offsets for global market context.")
    parser.add_argument("--daily-bar-completion-lag-hours", type=float, default=DEFAULT_PROFILE_CONFIG["daily_bar_completion_lag_hours"], help="Lag after bar_start before a daily bar may be used as completed context.")
    parser.add_argument("--loaded-parts-per-group", type=int, default=DEFAULT_PROFILE_CONFIG["loaded_parts_per_group"])
    parser.add_argument("--read-workers", type=int, default=DEFAULT_PROFILE_CONFIG["read_workers"])
    parser.add_argument("--materialize-workers", type=int, default=DEFAULT_PROFILE_CONFIG["materialize_workers"])
    parser.add_argument("--materialize-chunk-size", type=int, default=DEFAULT_PROFILE_CONFIG["materialize_chunk_size"], help="Origins per CPU materialization task. Default 0 uses batch-size.")
    parser.add_argument("--drop-last-batch", action="store_true", help="Drop the final partial ready batch for each loaded group.")
    parser.add_argument("--allow-unordered-materialization", action="store_true", help="Yield completed materialization tasks as they finish. Faster but not repeatable.")
    parser.add_argument("--dataset-id", default=DEFAULT_PROFILE_CONFIG["dataset_id"], help="Stable dataset plan id used in hashing/state. Empty creates an automatic id from cache/config.")
    parser.add_argument("--randomize-seed", action="store_true", help="Generate a random run seed and save it in loader state for replay.")
    parser.add_argument("--sample-fraction", type=float, default=DEFAULT_PROFILE_CONFIG["sample_fraction"], help="Deterministic hash fraction of origins to include.")
    parser.add_argument("--sample-hash-modulus", type=int, default=DEFAULT_PROFILE_CONFIG["sample_hash_modulus"], help="Modulo for deterministic hash bucket train/validation splits.")
    parser.add_argument("--sample-hash-buckets", default=DEFAULT_PROFILE_CONFIG["sample_hash_buckets"], help="Comma-separated hash buckets to include when sample-hash-modulus is set.")
    parser.add_argument("--max-origins-per-epoch", type=int, default=DEFAULT_PROFILE_CONFIG["max_origins_per_epoch"], help="Stop after this many emitted origins in the epoch. 0 means no cap.")
    parser.add_argument("--load-state-path", type=Path, default=None, help="Resume loader state from this JSON file.")
    parser.add_argument("--save-state-path", type=Path, default=DEFAULT_PROFILE_STATE_PATH, help="Write final loader state JSON to this file.")
    parser.add_argument("--no-save-state", action="store_true", help="Disable loader state JSON writing.")
    parser.add_argument("--include-external-context", action="store_true")
    parser.add_argument("--no-strict-audit", action="store_true")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_PROFILE_REPORT_PATH)
    parser.add_argument("--no-report", action="store_true", help="Disable JSONL report writing.")
    parser.add_argument("--skip-audit", action="store_true", help="Disable the post-profile batch audit.")
    parser.add_argument("--audit-batches", type=int, default=2, help="Batches to audit after profiling.")
    parser.add_argument("--audit-samples-per-batch", type=int, default=4, help="SSD package samples to audit per audited batch.")
    parser.add_argument("--audit-source-clickhouse-samples-per-batch", type=int, default=10, help="ClickHouse source samples to audit per audited batch. Use 0 to disable source checks.")
    parser.add_argument("--skip-audit-source-clickhouse", action="store_true", help="Audit only against SSD package files, not source ClickHouse rows.")
    parser.add_argument("--audit-no-check-determinism", action="store_true", help="Skip same-seed first-batch determinism check in the post-profile audit.")
    parser.add_argument("--audit-no-check-resume", action="store_true", help="Skip resume-from-state check in the post-profile audit.")
    parser.add_argument("--audit-report-path", type=Path, default=DEFAULT_PROFILE_AUDIT_REPORT_PATH)
    parser.add_argument("--clickhouse-url", default="", help="Optional ClickHouse URL for post-profile source audit. Empty uses env/default discovery.")
    parser.add_argument("--user", default="", help="Optional ClickHouse user for post-profile source audit. Empty uses env/default discovery.")
    parser.add_argument("--password", default="", help="Optional ClickHouse password for post-profile source audit. Empty uses env/default discovery.")
    parser.add_argument("--clickhouse-query-retries", type=int, default=2, help="Retries for post-profile source audit ClickHouse queries.")
    parser.add_argument("--clickhouse-query-retry-backoff-seconds", type=float, default=2.0, help="Backoff between post-profile source audit ClickHouse query retries.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cache_root = Path(args.cache_root) / str(args.cache_id)
    started_at_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    months = tuple(str(month) for month in (args.month if args.month is not None else DEFAULT_PROFILE_CONFIG["months"]))
    config = TickerMonthLoaderConfig(
        cache_root=cache_root,
        split=args.split,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
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
        ticker_news_max_items=max(0, int(args.ticker_news_max_items)),
        market_news_max_items=max(0, int(args.market_news_max_items)),
        sec_filing_max_items=max(0, int(args.sec_filing_max_items)),
        xbrl_max_items=max(0, int(args.xbrl_max_items)),
        corporate_action_max_items=max(0, int(args.corporate_action_max_items)),
        corporate_action_label_days=tuple(int(item.strip().rstrip("dD")) for item in str(args.corporate_action_label_days).split(",") if item.strip()),
        ticker_news_token_chunks=max(1, int(args.ticker_news_token_chunks)),
        market_news_token_chunks=max(1, int(args.market_news_token_chunks)),
        sec_filing_token_chunks=max(1, int(args.sec_filing_token_chunks)),
        text_max_tokens=max(1, int(args.text_max_tokens)),
        text_embedding_dim=max(1, int(args.text_embedding_dim)),
        ticker_daily_bar_offsets=tuple(int(item.strip()) for item in str(args.ticker_daily_bar_offsets).split(",") if item.strip()),
        global_daily_bar_offsets=tuple(int(item.strip()) for item in str(args.global_daily_bar_offsets).split(",") if item.strip()),
        daily_bar_completion_lag_hours=max(0.0, float(args.daily_bar_completion_lag_hours)),
        loaded_parts_per_group=max(1, int(args.loaded_parts_per_group)),
        read_workers=max(1, int(args.read_workers)),
        materialize_workers=max(1, int(args.materialize_workers)),
        materialize_chunk_size=max(0, int(args.materialize_chunk_size)),
        drop_last_batch=bool(args.drop_last_batch),
        preserve_batch_order=not bool(args.allow_unordered_materialization),
        max_batches=max(0, int(args.batches)),
        include_external_context=bool(args.include_external_context),
        strict_audit=not bool(args.no_strict_audit),
        dataset_id=str(args.dataset_id),
        randomize_seed=bool(args.randomize_seed),
        sample_fraction=max(0.0, min(1.0, float(args.sample_fraction))),
        sample_hash_modulus=max(0, int(args.sample_hash_modulus)),
        sample_hash_buckets=tuple(int(item.strip()) for item in str(args.sample_hash_buckets).split(",") if item.strip()),
        max_origins_per_epoch=max(0, int(args.max_origins_per_epoch)),
    )
    print("TICKER MONTH LOADER PROFILE " + str(cache_root), flush=True)
    print(json.dumps(jsonable(asdict(config)), sort_keys=True), flush=True)
    if not bool(args.no_report):
        print("PROFILE_REPORT " + str(args.report_path), flush=True)
    started = time.perf_counter()
    loader_start = time.perf_counter()
    loader = AsyncTickerMonthBatchLoader(config)
    loader_init_seconds = time.perf_counter() - loader_start
    state_load_seconds = 0.0
    if args.load_state_path is not None:
        state_load_start = time.perf_counter()
        with args.load_state_path.open("r", encoding="utf-8") as handle:
            loader.load_state_dict(json.load(handle))
        state_load_seconds = time.perf_counter() - state_load_start
    discovered = len(loader.index.parts)
    print("LOADER_STATE_START " + json.dumps(loader.summary(), sort_keys=True), flush=True)
    batches = 0
    samples = 0
    materialize_seconds = 0.0
    profile_seconds: dict[str, float] = {"loader_init_seconds": loader_init_seconds}
    if state_load_seconds:
        profile_seconds["state_load_seconds"] = state_load_seconds
    max_rss = current_rss_mib()
    first_shape: dict[str, Any] = {}
    for batch in loader.iter_batches():
        batches += 1
        samples += int(batch.sample_count)
        materialize_seconds += float(batch.profile.get("materialize_seconds", 0.0))
        for key, value in batch.profile.items():
            if key == "samples" or not key.endswith("_seconds"):
                continue
            profile_seconds[key] = float(profile_seconds.get(key, 0.0)) + float(value)
        max_rss = max(max_rss, current_rss_mib())
        if not first_shape:
            first_shape = _shape_summary(batch)
        elapsed = max(time.perf_counter() - started, 1e-9)
        print(
            json.dumps(
                {
                    "batch": batches,
                    "samples": samples,
                    "samples_per_sec": samples / elapsed,
                    "materialize_seconds": materialize_seconds,
                    "profile_seconds": {key: round(value, 6) for key, value in sorted(profile_seconds.items())},
                    "rss_mib": max_rss,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if int(args.batches) > 0 and batches >= int(args.batches):
            break
    elapsed = time.perf_counter() - started
    summary = {
        "cache_root": str(cache_root),
        "profile_started_at_utc": started_at_utc,
        "profile_finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile_report_path": "" if bool(args.no_report) else str(args.report_path),
        "discovered_parts": discovered,
        "batches": batches,
        "samples": samples,
        "elapsed_seconds": elapsed,
        "samples_per_sec": samples / max(elapsed, 1e-9),
        "materialize_seconds": materialize_seconds,
        "profile_seconds": {key: float(value) for key, value in sorted(profile_seconds.items())},
        "max_rss_mib": max_rss,
        "first_batch": first_shape,
        "loader_state": loader.summary(),
    }
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    if args.save_state_path is not None and not bool(args.no_save_state):
        args.save_state_path.parent.mkdir(parents=True, exist_ok=True)
        with args.save_state_path.open("w", encoding="utf-8") as handle:
            json.dump(loader.state_dict(), handle, sort_keys=True, indent=2)
    if not bool(args.no_report):
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        with args.report_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, sort_keys=True) + "\n")
    if bool(args.skip_audit):
        return 0
    audit_result = _run_post_profile_audit(args, config)
    audit_payload = {
        "event": "post_profile_audit",
        "profile_report_path": "" if bool(args.no_report) else str(args.report_path),
        "audit_report_path": str(audit_result.report_path),
        "audit_status": audit_result.status,
        "audit_ok": bool(audit_result.ok),
        "audit_summary": audit_result.summary,
    }
    print("AUDIT_SUMMARY " + json.dumps(audit_payload, sort_keys=True), flush=True)
    if not bool(args.no_report):
        with args.report_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(audit_payload, sort_keys=True) + "\n")
    return 0 if audit_result.ok else 2


def _run_post_profile_audit(args: argparse.Namespace, config: TickerMonthLoaderConfig) -> Any:
    # Lazy import avoids a module cycle: the standalone audit script imports this
    # profiler for its default config and report path constants.
    from research.mlops.rolling_loader.audit_ticker_month_loader_batches import (
        LoaderBatchAuditConfig,
        run_audit,
    )

    print("LOADER_BATCH_AUDIT_START " + str(args.audit_report_path), flush=True)
    audit_loader_config = replace(config, max_batches=0)
    return run_audit(
        LoaderBatchAuditConfig(
            loader_config=audit_loader_config,
            batches=max(1, int(args.audit_batches)),
            samples_per_batch=max(1, int(args.audit_samples_per_batch)),
            seed=int(args.seed),
            check_determinism=not bool(args.audit_no_check_determinism),
            check_resume=not bool(args.audit_no_check_resume),
            source_clickhouse_audit=not bool(args.skip_audit_source_clickhouse) and int(args.audit_source_clickhouse_samples_per_batch) > 0,
            source_clickhouse_samples_per_batch=max(0, int(args.audit_source_clickhouse_samples_per_batch)),
            clickhouse_url=str(args.clickhouse_url or ""),
            clickhouse_user=str(args.user or ""),
            clickhouse_password=str(args.password or ""),
            clickhouse_query_retries=max(0, int(args.clickhouse_query_retries)),
            clickhouse_query_retry_backoff_seconds=max(0.0, float(args.clickhouse_query_retry_backoff_seconds)),
            report_path=Path(args.audit_report_path),
        )
    )


def _shape_summary(batch: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "samples": int(batch.sample_count),
        "event_output_mode": batch.event_output_mode,
        "ticker_shape": list(batch.ticker.shape),
        "origin_ordinal_shape": list(batch.origin_ordinal.shape),
    }
    if batch.raw_event_windows:
        first = next(iter(batch.raw_event_windows.values()))
        out["raw_event_windows_shape"] = list(first.shape)
        out["raw_event_window_columns"] = sorted(batch.raw_event_windows)
    if batch.raw_event_flat:
        first = next(iter(batch.raw_event_flat.values()))
        out["raw_event_flat_shape"] = list(first.shape)
        out["raw_event_flat_columns"] = sorted(batch.raw_event_flat)
    if batch.raw_event_stream.size:
        out["raw_event_stream_shape"] = list(batch.raw_event_stream.shape)
        out["raw_event_stream_columns"] = list(batch.raw_event_stream_feature_names)
    if batch.headers_uint8.size:
        out["headers_uint8_shape"] = list(batch.headers_uint8.shape)
        out["events_uint8_shape"] = list(batch.events_uint8.shape)
    if batch.intraday_labels:
        out["intraday_label_shapes"] = {key: list(value.shape) for key, value in batch.intraday_labels.items()}
    if batch.corporate_action_labels:
        out["corporate_action_label_shapes"] = {key: list(value.shape) for key, value in batch.corporate_action_labels.items()}
        out["corporate_action_label_days"] = list(batch.corporate_action_label_days)
    if batch.text_inputs:
        out["text_input_shapes"] = {
            name: {field: list(value.shape) for field, value in payload.items()}
            for name, payload in batch.text_inputs.items()
        }
    if batch.xbrl_inputs:
        out["xbrl_input_shapes"] = {field: list(value.shape) for field, value in batch.xbrl_inputs.items()}
    if batch.corporate_action_inputs:
        out["corporate_action_input_shapes"] = {field: list(value.shape) for field, value in batch.corporate_action_inputs.items()}
    if batch.bar_inputs:
        out["bar_input_shapes"] = {
            name: {field: list(value.shape) for field, value in payload.items()}
            for name, payload in batch.bar_inputs.items()
        }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
