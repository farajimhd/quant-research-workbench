from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.packed_market import ClickHouseTickerStreamConfig, ClickHouseTickerStreamDataset
from research.packed_market_model.v1.data import block_to_torch
from research.packed_market_model.v1.losses import compute_loss
from research.packed_market_model.v1.model import PackedMarketModelV1
from research.packed_market_model.v1.config import ModelConfig, parse_csv


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile direct ClickHouse ticker-stream packed loader.")
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker smoke subset. Empty means all plans.")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table-base", default="events")
    parser.add_argument("--events-ticker-day-index-table", default="events_ticker_day_index")
    parser.add_argument("--ticker-workers", type=int, default=24)
    parser.add_argument("--ready-queue-blocks", type=int, default=8)
    parser.add_argument("--target-origin-count-per-block", type=int, default=65_536)
    parser.add_argument("--event-context-rows", type=int, default=1_024)
    parser.add_argument("--future-event-guard-rows", type=int, default=262_144)
    parser.add_argument("--max-blocks", type=int, default=20)
    parser.add_argument("--max-plans", type=int, default=0)
    parser.add_argument("--max-threads-per-query", type=int, default=4)
    parser.add_argument("--max-memory-usage", default="32G")
    parser.add_argument("--worker-memory-limit-mib", type=int, default=12_288)
    parser.add_argument("--with-model-step", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--d-model", type=int, default=256)
    parser.add_argument("--event-layers", type=int, default=4)
    parser.add_argument("--head-hidden-dim", type=int, default=256)
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\packed_market_model\v1\profiles")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    run_dir = Path(args.output_root) / f"ticker_stream_profile_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "profile.jsonl"
    config = ClickHouseTickerStreamConfig(
        months=parse_csv(args.months),
        tickers=parse_csv(args.tickers),
        database=args.database,
        events_table_base=args.events_table_base,
        events_ticker_day_index_table=args.events_ticker_day_index_table,
        ticker_workers=int(args.ticker_workers),
        ready_queue_blocks=int(args.ready_queue_blocks),
        target_origin_count_per_block=int(args.target_origin_count_per_block),
        event_context_rows=int(args.event_context_rows),
        future_event_guard_rows=int(args.future_event_guard_rows),
        max_blocks=int(args.max_blocks),
        max_plans=int(args.max_plans),
        max_threads_per_query=int(args.max_threads_per_query),
        max_memory_usage=str(args.max_memory_usage),
        worker_memory_limit_mib=int(args.worker_memory_limit_mib),
    )
    dataset = ClickHouseTickerStreamDataset(config)
    model_config = ModelConfig(
        event_feature_names=dataset.event_feature_names,
        event_feature_dim=len(dataset.event_feature_names),
        label_names=dataset.label_names,
        d_model=int(args.d_model),
        event_layers=int(args.event_layers),
        head_hidden_dim=int(args.head_hidden_dim),
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = PackedMarketModelV1(model_config).to(device) if args.with_model_step else None
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3) if model is not None else None
    print(f"TICKER STREAM PROFILE {run_dir}", flush=True)
    print(json.dumps({**vars(args), "device": str(device), "labels": len(dataset.label_names), "event_features": len(dataset.event_feature_names)}, sort_keys=True), flush=True)
    append_jsonl(report_path, {"event": "start", "args": vars(args), "device": str(device), "label_count": len(dataset.label_names)})
    reporter = StreamingProfileReporter(dataset=dataset, layout=args.progress_layout)
    started = time.perf_counter()
    blocks = 0
    origins = 0
    events = 0
    with reporter:
        for raw_block in dataset.iter_blocks():
            block_start = time.perf_counter()
            transfer_start = time.perf_counter()
            torch_block = block_to_torch(raw_block, model_config=model_config, device=device)
            transfer_seconds = time.perf_counter() - transfer_start
            model_seconds = 0.0
            loss_value = 0.0
            if model is not None and optimizer is not None:
                optimizer.zero_grad(set_to_none=True)
                model_start = time.perf_counter()
                output = model(torch_block.x)
                loss = compute_loss(output, torch_block).loss
                loss.backward()
                optimizer.step()
                if device.type == "cuda":
                    torch.cuda.synchronize()
                model_seconds = time.perf_counter() - model_start
                loss_value = float(loss.detach().cpu())
            block_seconds = time.perf_counter() - block_start
            blocks += 1
            origins += int(raw_block.origin_count)
            events += int(raw_block.event_count)
            row = {
                "event": "block",
                "block": blocks,
                "ticker": raw_block.block_manifest.ticker,
                "month": raw_block.block_manifest.month,
                "origin_count": int(raw_block.origin_count),
                "event_count": int(raw_block.event_count),
                "transfer_seconds": transfer_seconds,
                "model_seconds": model_seconds,
                "block_seconds": block_seconds,
                "loss": loss_value,
                **dataset.telemetry_snapshot(),
            }
            append_jsonl(report_path, row)
            reporter.update(row)
            print(json.dumps({k: row[k] for k in ("block", "ticker", "origin_count", "event_count", "transfer_seconds", "model_seconds", "block_seconds")}, sort_keys=True), flush=True)
            if int(args.max_blocks) > 0 and blocks >= int(args.max_blocks):
                break
    elapsed = time.perf_counter() - started
    summary = {
        "event": "summary",
        "blocks": blocks,
        "origins": origins,
        "events": events,
        "elapsed_seconds": elapsed,
        "origins_per_second": origins / max(elapsed, 1e-9),
        "events_per_second": events / max(elapsed, 1e-9),
        **dataset.telemetry_snapshot(),
    }
    append_jsonl(report_path, summary)
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 0


class StreamingProfileReporter:
    def __init__(self, *, dataset: ClickHouseTickerStreamDataset, layout: str) -> None:
        self.dataset = dataset
        self.layout = layout
        self._live: Any | None = None
        self.last_row: dict[str, Any] = {}

    def __enter__(self) -> "StreamingProfileReporter":
        if self.layout in {"auto", "rich"}:
            try:
                from rich.console import Console
                from rich.live import Live

                self._live = Live(self._render(), console=Console(), screen=True, auto_refresh=False, transient=False)
                self._live.start()
            except Exception:
                if self.layout == "rich":
                    raise
                self._live = None
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
            self._live.stop()

    def update(self, row: dict[str, Any]) -> None:
        self.last_row = dict(row)
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table

        telemetry = self.dataset.telemetry_snapshot()
        summary = Table.grid(expand=False, padding=(0, 4))
        summary.add_column(no_wrap=True)
        summary.add_column(no_wrap=True)
        summary.add_row(f"[bold]Blocks[/] {int(telemetry.get('loader/state/blocks_emitted', 0)):,}", f"[bold]Ready[/] {int(telemetry.get('loader/state/ready_queue', 0)):,}/{int(telemetry.get('loader/state/ready_queue_limit', 0)):,}")
        summary.add_row(f"[bold]Origins[/] {int(telemetry.get('loader/state/origins_emitted', 0)):,}", f"[bold]Events[/] {int(telemetry.get('loader/state/events_fetched', 0)):,}")
        summary.add_row(f"[bold]Plans[/] {int(telemetry.get('loader/state/plans_done', 0)):,}/{int(telemetry.get('loader/state/plans_total', 0)):,}", f"[bold]Origin/s[/] {float(telemetry.get('loader/state/origins_per_second', 0.0)):,.1f}")
        workers = Table(expand=True)
        workers.add_column("W", no_wrap=True)
        workers.add_column("Status", no_wrap=True)
        workers.add_column("Ticker", no_wrap=True)
        workers.add_column("Rows", justify="right", no_wrap=True)
        workers.add_column("Fetch", justify="right", no_wrap=True)
        workers.add_column("Process", justify="right", no_wrap=True)
        workers.add_column("Mem", justify="right", no_wrap=True)
        for i in range(8):
            prefix = f"loader/worker_{i:02d}/"
            if prefix + "status" not in telemetry:
                continue
            workers.add_row(
                str(i),
                str(telemetry.get(prefix + "status", "")),
                str(telemetry.get(prefix + "ticker", "")),
                f"{int(float(telemetry.get(prefix + 'event_rows', 0))):,}",
                f"{float(telemetry.get(prefix + 'fetch_seconds', 0.0)):.2f}",
                f"{float(telemetry.get(prefix + 'process_seconds', 0.0)):.2f}",
                f"{float(telemetry.get(prefix + 'memory_mib', 0.0)):.0f} MiB",
            )
        return Group(Panel(summary, title="Ticker Stream Profile", border_style="cyan"), Panel(workers, title="Workers", border_style="green"))


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True, default=str) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
