from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "research").is_dir():
            sys.path.insert(0, str(parent))
            break

from research.mlops.clickhouse import (
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.data.config import RollingMarketDataConfig
from research.mlops.env import load_env_files
from research.mlops.rolling_loader.streaming_training import (
    StreamingClickHouseTrainingSource,
    StreamingProfiler,
    StreamingRollingTrainingProvider,
    StreamingStageRecord,
    batch_nbytes,
    batch_shape_summary,
    current_rss_mib,
    parse_utc_us,
)


DEFAULTS: dict[str, Any] = {
    "database": "market_sip_compact",
    "sec_context_database": "market_sip_compact",
    "events_table": "events",
    "macro_bars_table": "macro_bars_by_time_symbol",
    "news_token_table": "news_text_tokens",
    "sec_filing_text_token_table": "sec_filing_text_tokens",
    "sec_xbrl_context_table": "sec_xbrl_context",
    "category_reference_table": "training_category_reference",
    "start_utc": "2019-01-05T00:00:00Z",
    "days": 3,
    "block_days": 3,
    "warmup_days": 3,
    "batch_size": 4096,
    "batches": 4,
    "sample_stride_events": 1,
    "max_ready_samples": 0,
    "max_threads": 8,
    "max_memory_usage": "80G",
    "macro_lookback_days": 40,
    "label_lookahead_days": 400,
    "news_lookback_days": 30,
    "sec_lookback_days": 365,
    "xbrl_lookback_days": 730,
    "event_row_limit": 0,
    "ready_queue_size": 4,
    "shutdown_timeout_seconds": 2.0,
    "simulate_gpu_seconds": 0.0,
    "output_root": "D:/market-data/prepared/data_provider_profiles/streaming_rolling_loader_training",
}


@dataclass(slots=True)
class BatchProfileRow:
    batch_index: int
    block_index: int
    samples: int
    materialized_mib: float
    queue_wait_seconds: float
    consumer_seconds: float
    first_origin_timestamp_us: int
    last_origin_timestamp_us: int
    shapes: dict[str, Any]
    materialize_metrics: dict[str, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the guide-aligned streaming rolling training loader. "
            "The profiler bulk streams ClickHouse date blocks into memory, uses Polars/Arrow at the source boundary, "
            "feeds the shared rolling cache engine, and measures each low-level stage."
        )
    )
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--database", default=DEFAULTS["database"])
    parser.add_argument("--sec-context-database", default=DEFAULTS["sec_context_database"])
    parser.add_argument("--events-table", default=DEFAULTS["events_table"])
    parser.add_argument("--macro-bars-table", default=DEFAULTS["macro_bars_table"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-filing-text-token-table", default=DEFAULTS["sec_filing_text_token_table"])
    parser.add_argument("--sec-xbrl-context-table", default=DEFAULTS["sec_xbrl_context_table"])
    parser.add_argument("--category-reference-table", default=DEFAULTS["category_reference_table"])
    parser.add_argument("--start-utc", default=DEFAULTS["start_utc"])
    parser.add_argument("--start-timestamp-us", type=int, default=0)
    parser.add_argument("--end-utc", default="")
    parser.add_argument("--end-timestamp-us", type=int, default=0)
    parser.add_argument("--days", type=int, default=DEFAULTS["days"])
    parser.add_argument("--block-days", type=int, default=DEFAULTS["block_days"])
    parser.add_argument("--warmup-days", type=int, default=DEFAULTS["warmup_days"])
    parser.add_argument("--batch-size", type=int, default=DEFAULTS["batch_size"])
    parser.add_argument("--batches", type=int, default=DEFAULTS["batches"], help="Maximum emitted batches. Use 0 for no cap.")
    parser.add_argument("--sample-stride-events", type=int, default=DEFAULTS["sample_stride_events"])
    parser.add_argument("--max-ready-samples", type=int, default=DEFAULTS["max_ready_samples"])
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--macro-lookback-days", type=int, default=DEFAULTS["macro_lookback_days"])
    parser.add_argument("--label-lookahead-days", type=int, default=DEFAULTS["label_lookahead_days"])
    parser.add_argument("--news-lookback-days", type=int, default=DEFAULTS["news_lookback_days"])
    parser.add_argument("--sec-lookback-days", type=int, default=DEFAULTS["sec_lookback_days"])
    parser.add_argument("--xbrl-lookback-days", type=int, default=DEFAULTS["xbrl_lookback_days"])
    parser.add_argument("--event-row-limit", type=int, default=DEFAULTS["event_row_limit"], help="Debug cap per event block. Use 0 for full blocks.")
    parser.add_argument("--ready-queue-size", type=int, default=DEFAULTS["ready_queue_size"])
    parser.add_argument("--shutdown-timeout-seconds", type=float, default=DEFAULTS["shutdown_timeout_seconds"])
    parser.add_argument("--simulate-gpu-seconds", type=float, default=DEFAULTS["simulate_gpu_seconds"])
    parser.add_argument("--skip-token-contexts", action="store_true")
    parser.add_argument("--skip-xbrl", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path(DEFAULTS["output_root"]))
    parser.add_argument("--run-name", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    loaded_env_files = load_env_files(
        discover_clickhouse_env_files() if args.env_file is None else discover_clickhouse_env_files() + [args.env_file]
    )
    start_timestamp_us = int(args.start_timestamp_us or parse_utc_us(args.start_utc))
    end_timestamp_us = _resolve_end_timestamp_us(args, start_timestamp_us)
    run_dir = _make_run_dir(args.output_root, args.run_name)
    profiler = StreamingProfiler(output_path=run_dir / "profile_events.jsonl")
    batch_rows_path = run_dir / "batch_profiles.jsonl"
    summary_path = run_dir / "summary.json"
    args_path = run_dir / "args.json"

    contexts = ["ticker_news", "market_news", "sec_filings"]
    if not args.skip_xbrl:
        contexts.append("xbrl")
    if args.skip_token_contexts:
        contexts = []

    config = RollingMarketDataConfig(
        database=args.database,
        sec_context_database=args.sec_context_database,
        events_table=args.events_table,
        macro_bars_table=args.macro_bars_table,
        news_token_table=args.news_token_table,
        sec_filing_text_token_table=args.sec_filing_text_token_table,
        sec_xbrl_context_table=args.sec_xbrl_context_table,
        category_reference_table=args.category_reference_table,
        sample_stride_events=max(1, int(args.sample_stride_events)),
        batch_size=max(1, int(args.batch_size)),
        max_ready_samples=max(0, int(args.max_ready_samples)),
        max_threads=max(1, int(args.max_threads)),
        max_memory_usage=str(args.max_memory_usage),
        macro_timeframes=("1d",),
        label_timeframes=("1d",),
        macro_lookback_days=max(0, int(args.macro_lookback_days)),
        label_lookahead_days=max(0, int(args.label_lookahead_days)),
        q_live_contexts=tuple(contexts),
        news_lookback_days=max(0, int(args.news_lookback_days)),
        sec_lookback_days=max(0, int(args.sec_lookback_days)),
        xbrl_lookback_days=max(0, int(args.xbrl_lookback_days)),
    )
    source = StreamingClickHouseTrainingSource(
        config=config,
        clickhouse_url=args.clickhouse_url or default_clickhouse_url(),
        user=args.user or default_clickhouse_user(),
        password=args.password or default_clickhouse_password(),
    )
    provider = StreamingRollingTrainingProvider(
        source=source,
        config=config,
        start_timestamp_us=start_timestamp_us,
        end_timestamp_us=end_timestamp_us,
        block_days=max(1, int(args.block_days)),
        warmup_days=max(0, int(args.warmup_days)),
        max_batches=max(0, int(args.batches)),
        event_row_limit=max(0, int(args.event_row_limit)),
        load_token_contexts=not args.skip_token_contexts,
        load_xbrl=not args.skip_xbrl,
        ready_queue_size=max(1, int(args.ready_queue_size)),
        shutdown_timeout_seconds=max(0.0, float(args.shutdown_timeout_seconds)),
        profiler=profiler,
    )
    run_args = {
        **vars(args),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "start_timestamp_us": start_timestamp_us,
        "end_timestamp_us": end_timestamp_us,
        "start_utc_resolved": _utc_iso(start_timestamp_us),
        "end_utc_resolved": _utc_iso(end_timestamp_us),
        "macro_timeframes": ["1d"],
        "label_timeframes": ["1d"],
        "q_live_contexts": list(config.q_live_contexts),
    }
    args_path.write_text(json.dumps(_jsonable(run_args), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"STREAMING PROFILE RUN {run_dir}", flush=True)
    print(json.dumps(_jsonable(run_args), sort_keys=True), flush=True)
    started = time.perf_counter()
    batches: list[BatchProfileRow] = []
    sample_count = 0
    interrupted = False
    try:
        with batch_rows_path.open("w", encoding="utf-8") as batch_handle:
            for envelope in provider:
                consumer_started = time.perf_counter()
                if args.simulate_gpu_seconds > 0:
                    time.sleep(float(args.simulate_gpu_seconds))
                consumer_seconds = time.perf_counter() - consumer_started
                batch_bytes = batch_nbytes(envelope.batch)
                sample_count += len(envelope.samples)
                row = BatchProfileRow(
                    batch_index=int(envelope.batch_index),
                    block_index=int(envelope.block_index),
                    samples=len(envelope.samples),
                    materialized_mib=batch_bytes / (1024 * 1024),
                    queue_wait_seconds=float(envelope.source_queue_wait_seconds),
                    consumer_seconds=float(consumer_seconds),
                    first_origin_timestamp_us=int(envelope.samples[0].origin_timestamp_us),
                    last_origin_timestamp_us=int(envelope.samples[-1].origin_timestamp_us),
                    shapes=batch_shape_summary(envelope.batch),
                    materialize_metrics=(
                        envelope.batch.profile.to_metrics(prefix="rolling_training")
                        if envelope.batch.profile is not None
                        else {}
                    ),
                )
                batches.append(row)
                batch_handle.write(json.dumps(_jsonable(asdict(row)), sort_keys=True) + "\n")
                batch_handle.flush()
                elapsed = time.perf_counter() - started
                print(
                    "BATCH "
                    f"{row.batch_index} block={row.block_index} samples={row.samples} "
                    f"batch_mib={row.materialized_mib:.2f} queue_wait_s={row.queue_wait_seconds:.4f} "
                    f"elapsed_s={elapsed:.2f}",
                    flush=True,
                )
    except KeyboardInterrupt:
        interrupted = True
        print("INTERRUPT received; requesting streaming loader shutdown...", flush=True)
        profiler.add(
            StreamingStageRecord(
                stage="consumer_keyboard_interrupt",
                seconds=0.0,
                rss_before_mib=current_rss_mib(),
                rss_after_mib=current_rss_mib(),
                metadata={"batches": len(batches), "samples": sample_count},
            )
        )
        provider.stop(join_timeout=max(0.0, float(args.shutdown_timeout_seconds)))
    finally:
        source.close()

    elapsed_seconds = time.perf_counter() - started
    summary = {
        "run_dir": str(run_dir),
        "elapsed_seconds": elapsed_seconds,
        "batches": len(batches),
        "status": "interrupted" if interrupted else "complete",
        "samples": sample_count,
        "samples_per_second": sample_count / elapsed_seconds if elapsed_seconds > 0 else 0.0,
        "batches_per_second": len(batches) / elapsed_seconds if elapsed_seconds > 0 else 0.0,
        "peak_observed_rss_mib": max([current_rss_mib()] + [record.rss_after_mib for record in profiler.records]),
        "batch_materialized_mib_total": sum(row.materialized_mib for row in batches),
        "batch_materialized_mib_max": max((row.materialized_mib for row in batches), default=0.0),
        "materialize_metric_totals": _sum_batch_metric_values(batches),
        "profiler": profiler.aggregate(),
        "args": run_args,
    }
    summary_path.write_text(json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SUMMARY {summary_path}", flush=True)
    print(json.dumps(_jsonable({k: v for k, v in summary.items() if k != "profiler" and k != "args"}), sort_keys=True), flush=True)
    return 0


def _resolve_end_timestamp_us(args: argparse.Namespace, start_timestamp_us: int) -> int:
    if int(args.end_timestamp_us) > 0:
        return int(args.end_timestamp_us)
    if str(args.end_utc).strip():
        return parse_utc_us(str(args.end_utc))
    return start_timestamp_us + max(1, int(args.days)) * 86_400_000_000 - 1


def _make_run_dir(root: Path, run_name: str) -> Path:
    name = run_name.strip() or dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = root / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def _utc_iso(timestamp_us: int) -> str:
    return dt.datetime.fromtimestamp(int(timestamp_us) / 1_000_000.0, tz=dt.timezone.utc).isoformat()


def _sum_batch_metric_values(rows: list[BatchProfileRow]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        for key, value in row.materialize_metrics.items():
            totals[key] = totals.get(key, 0.0) + float(value)
    return dict(sorted(totals.items()))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
