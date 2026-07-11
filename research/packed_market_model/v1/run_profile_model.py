from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.model_artifacts import parameter_summary
from research.mlops.packed_market import ClickHouseTickerStreamConfig, ClickHouseTickerStreamDataset
from research.packed_market_model.v1.config import ModelConfig, parse_csv
from research.packed_market_model.v1.data import PackedTorchBlock, block_to_torch, make_dummy_packed_block
from research.packed_market_model.v1.losses import compute_loss
from research.packed_market_model.v1.model import PackedMarketModelV1
from research.packed_market_model.v1.train import amp_dtype_from_name, maybe_compile_model, set_seed, unwrap_model

_INTERRUPTED = False


def _handle_interrupt(_signum: int, _frame: Any) -> None:
    global _INTERRUPTED
    _INTERRUPTED = True
    print("\nInterrupt received; stopping after the current profiled block.", file=sys.stderr, flush=True)


@dataclass(slots=True)
class ProfileState:
    run_dir: Path
    blocks_done: int = 0
    warmup_done: int = 0
    origins_done: int = 0
    events_done: int = 0
    last_row: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)

    def elapsed(self) -> float:
        return time.perf_counter() - self.started_at


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile packed_market_model v1 with the real ClickHouse ticker-stream loader.")
    parser.add_argument("--months", default="2019-02")
    parser.add_argument("--tickers", default="", help="Comma-separated ticker subset. Empty uses the most active ticker/month plans.")
    parser.add_argument("--database", default="market_sip_compact")
    parser.add_argument("--events-table-base", default="events")
    parser.add_argument("--events-ticker-day-index-table", default="events_ticker_day_index")
    parser.add_argument("--ticker-workers", type=int, default=24)
    parser.add_argument("--ready-queue-blocks", type=int, default=16)
    parser.add_argument("--target-origin-count-per-block", type=int, default=65_536)
    parser.add_argument("--event-context-rows", type=int, default=1_024)
    parser.add_argument("--future-event-guard-rows", type=int, default=262_144)
    parser.add_argument("--max-plans", type=int, default=24)
    parser.add_argument("--max-threads-per-query", type=int, default=4)
    parser.add_argument("--max-memory-usage", default="32G")
    parser.add_argument("--worker-memory-limit-mib", type=int, default=12_288)
    parser.add_argument("--scanner-sidecar", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--scanner-run-id", default="")
    parser.add_argument("--scanner-table", default="packed_scanner_sidecar_bars")
    parser.add_argument("--scanner-window-seconds", type=int, default=900)
    parser.add_argument("--scanner-fetch-lookback-seconds", type=int, default=300)
    parser.add_argument("--scanner-warmup-seconds", type=int, default=5)
    parser.add_argument("--scanner-baseline-et", default="04:00:00")
    parser.add_argument("--scanner-cleanup-on-stop", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--scanner-penny-price-threshold", type=float, default=1.0)
    parser.add_argument("--scanner-small-price-threshold", type=float, default=20.0)
    parser.add_argument("--scanner-mid-price-threshold", type=float, default=100.0)
    parser.add_argument("--scanner-rank-top-k", type=int, default=16)
    parser.add_argument("--scanner-background-chunk-seconds", type=int, default=60)
    parser.add_argument("--warmup-blocks", type=int, default=1)
    parser.add_argument("--profile-blocks", type=int, default=4)
    parser.add_argument("--d-model", type=int, default=384)
    parser.add_argument("--event-layers", type=int, default=8)
    parser.add_argument("--event-kernel-size", type=int, default=9)
    parser.add_argument("--head-hidden-dim", type=int, default=512)
    parser.add_argument("--position-embedding", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--grad-clip-norm", type=float, default=1.0)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--amp-dtype", choices=("bf16", "bfloat16", "fp16", "float16", "float32"), default="bf16")
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dummy-data", action="store_true")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\packed_market_model\v1\profiles")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="rich")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_interrupt)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    set_seed(int(args.seed))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
    run_dir = Path(args.output_root) / f"model_profile_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "profile.jsonl"
    state = ProfileState(run_dir=run_dir)
    dataset: ClickHouseTickerStreamDataset | None = None

    if bool(args.dummy_data):
        model_config = ModelConfig(
            event_feature_names=tuple(f"feature_{i}" for i in range(24)),
            event_feature_dim=24,
            label_names=("future_price_primary_int_1s", "future_event_count_1s"),
            d_model=int(args.d_model),
            event_layers=int(args.event_layers),
            event_kernel_size=int(args.event_kernel_size),
            head_hidden_dim=int(args.head_hidden_dim),
            use_position_embedding=bool(args.position_embedding),
        )
        iterator = iter_dummy_blocks(model_config, device)
        loader_info: dict[str, Any] = {"data_source": "dummy"}
    else:
        stream_config = ClickHouseTickerStreamConfig(
            months=parse_csv(args.months),
            tickers=parse_csv(args.tickers),
            database=str(args.database),
            events_table_base=str(args.events_table_base),
            events_ticker_day_index_table=str(args.events_ticker_day_index_table),
            ticker_workers=int(args.ticker_workers),
            ready_queue_blocks=int(args.ready_queue_blocks),
            target_origin_count_per_block=int(args.target_origin_count_per_block),
            event_context_rows=int(args.event_context_rows),
            future_event_guard_rows=int(args.future_event_guard_rows),
            max_blocks=max(1, int(args.warmup_blocks) + int(args.profile_blocks)),
            max_plans=int(args.max_plans),
            max_threads_per_query=int(args.max_threads_per_query),
            max_memory_usage=str(args.max_memory_usage),
            worker_memory_limit_mib=int(args.worker_memory_limit_mib),
            scanner_sidecar_enabled=bool(args.scanner_sidecar),
            scanner_run_id=str(args.scanner_run_id),
            scanner_table=str(args.scanner_table),
            scanner_window_seconds=int(args.scanner_window_seconds),
            scanner_fetch_lookback_seconds=int(args.scanner_fetch_lookback_seconds),
            scanner_warmup_seconds=int(args.scanner_warmup_seconds),
            scanner_baseline_et=str(args.scanner_baseline_et),
            scanner_cleanup_on_stop=bool(args.scanner_cleanup_on_stop),
            scanner_penny_price_threshold=float(args.scanner_penny_price_threshold),
            scanner_small_price_threshold=float(args.scanner_small_price_threshold),
            scanner_mid_price_threshold=float(args.scanner_mid_price_threshold),
            scanner_rank_top_k=int(args.scanner_rank_top_k),
            scanner_background_chunk_seconds=int(args.scanner_background_chunk_seconds),
        )
        dataset = ClickHouseTickerStreamDataset(stream_config)
        model_config = ModelConfig(
            event_feature_names=dataset.event_feature_names,
            event_feature_dim=len(dataset.event_feature_names),
            label_names=dataset.label_names,
            d_model=int(args.d_model),
            event_layers=int(args.event_layers),
            event_kernel_size=int(args.event_kernel_size),
            head_hidden_dim=int(args.head_hidden_dim),
            use_position_embedding=bool(args.position_embedding),
        )
        iterator = dataset.iter_blocks()
        loader_info = {"data_source": "clickhouse_ticker_stream", **dataset.telemetry_snapshot()}

    model = PackedMarketModelV1(model_config).to(device)
    compile_start = time.perf_counter()
    model = maybe_compile_model(model, bool(args.compile_model))
    compile_seconds = time.perf_counter() - compile_start
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.learning_rate), weight_decay=float(args.weight_decay), foreach=True)
    amp_dtype = amp_dtype_from_name(str(args.amp_dtype))
    param_count = int(parameter_summary(unwrap_model(model))["total_parameters"])
    header = {
        "event": "start",
        "run_dir": str(run_dir),
        "args": vars(args),
        "device": str(device),
        "parameter_count": param_count,
        "compile_seconds": compile_seconds,
        "event_feature_dim": int(model_config.event_feature_dim),
        "label_count": len(model_config.label_names),
        "use_position_embedding": bool(model_config.use_position_embedding),
        "loader": loader_info,
    }
    append_jsonl(report_path, header)
    print(f"PACKED V1 MODEL PROFILE {run_dir}", flush=True)
    print(json.dumps({k: header[k] for k in ("device", "parameter_count", "compile_seconds", "event_feature_dim", "label_count", "use_position_embedding")}, sort_keys=True), flush=True)
    rows: list[dict[str, Any]] = []
    total_blocks = max(0, int(args.warmup_blocks)) + max(1, int(args.profile_blocks))
    try:
        with ModelProfileReporter(state=state, layout=str(args.progress_layout)) as reporter:
            for block_index in range(1, total_blocks + 1):
                if _INTERRUPTED:
                    break
                is_warmup = block_index <= max(0, int(args.warmup_blocks))
                row = profile_one_block(
                    iterator=iterator,
                    model=model,
                    optimizer=optimizer,
                    model_config=model_config,
                    device=device,
                    amp_enabled=bool(args.amp),
                    amp_dtype=amp_dtype,
                    grad_clip_norm=float(args.grad_clip_norm),
                    dataset=dataset,
                    is_warmup=is_warmup,
                    block_index=block_index,
                )
                append_jsonl(report_path, row)
                rows.append(row)
                state.last_row = row
                if is_warmup:
                    state.warmup_done += 1
                else:
                    state.blocks_done += 1
                    state.origins_done += int(row.get("origin_count", 0))
                    state.events_done += int(row.get("event_count", 0))
                reporter.update()
                print(
                    json.dumps(
                        {
                            "block": block_index,
                            "warmup": is_warmup,
                            "origin_count": row.get("origin_count", 0),
                            "event_count": row.get("event_count", 0),
                            "loader_wait_seconds": row.get("loader_wait_seconds", 0.0),
                            "transfer_seconds": row.get("transfer_seconds", 0.0),
                            "forward_seconds": row.get("forward_seconds", 0.0),
                            "backward_seconds": row.get("backward_seconds", 0.0),
                            "optimizer_seconds": row.get("optimizer_seconds", 0.0),
                            "total_step_seconds": row.get("total_step_seconds", 0.0),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    finally:
        if dataset is not None:
            dataset.stop()
    summary = summarize_rows(rows)
    append_jsonl(report_path, {"event": "summary", **summary})
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 130 if _INTERRUPTED else 0


def profile_one_block(
    *,
    iterator: Iterable[Any],
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    model_config: ModelConfig,
    device: torch.device,
    amp_enabled: bool,
    amp_dtype: torch.dtype,
    grad_clip_norm: float,
    dataset: ClickHouseTickerStreamDataset | None,
    is_warmup: bool,
    block_index: int,
) -> dict[str, Any]:
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    loader_start = time.perf_counter()
    raw_block = next(iterator)
    loader_wait_seconds = time.perf_counter() - loader_start
    transfer_start = time.perf_counter()
    block = raw_block if isinstance(raw_block, PackedTorchBlock) else block_to_torch(raw_block, model_config=model_config, device=device)
    if device.type == "cuda":
        torch.cuda.synchronize()
    transfer_seconds = time.perf_counter() - transfer_start
    optimizer.zero_grad(set_to_none=True)
    forward_start = time.perf_counter()
    with torch.autocast(device_type=device.type, dtype=amp_dtype, enabled=bool(amp_enabled and device.type == "cuda")):
        output = model(block.x)
    if device.type == "cuda":
        torch.cuda.synchronize()
    forward_seconds = time.perf_counter() - forward_start
    loss_start = time.perf_counter()
    loss_result = compute_loss(output, block)
    loss = loss_result.loss
    if device.type == "cuda":
        torch.cuda.synchronize()
    loss_seconds = time.perf_counter() - loss_start
    backward_start = time.perf_counter()
    loss.backward()
    if float(grad_clip_norm) > 0:
        torch.nn.utils.clip_grad_norm_(model.parameters(), float(grad_clip_norm))
    if device.type == "cuda":
        torch.cuda.synchronize()
    backward_seconds = time.perf_counter() - backward_start
    optimizer_start = time.perf_counter()
    optimizer.step()
    if device.type == "cuda":
        torch.cuda.synchronize()
    optimizer_seconds = time.perf_counter() - optimizer_start
    total_step_seconds = loader_wait_seconds + transfer_seconds + forward_seconds + loss_seconds + backward_seconds + optimizer_seconds
    telemetry = dataset.telemetry_snapshot() if dataset is not None else {}
    row: dict[str, Any] = {
        "event": "block",
        "block": int(block_index),
        "warmup": bool(is_warmup),
        "origin_count": int(block.origin_count),
        "event_count": int(block.event_count),
        "label_count": len(block.y),
        "loss": float(loss.detach().cpu()),
        "loader_wait_seconds": float(loader_wait_seconds),
        "transfer_seconds": float(transfer_seconds),
        "forward_seconds": float(forward_seconds),
        "loss_seconds": float(loss_seconds),
        "backward_seconds": float(backward_seconds),
        "optimizer_seconds": float(optimizer_seconds),
        "total_step_seconds": float(total_step_seconds),
        "origins_per_second": float(block.origin_count / max(total_step_seconds, 1e-9)),
        "events_per_second": float(block.event_count / max(total_step_seconds, 1e-9)),
        "cuda_max_allocated_mib": float(torch.cuda.max_memory_allocated(device) / (1024 * 1024)) if device.type == "cuda" else 0.0,
        "cuda_max_reserved_mib": float(torch.cuda.max_memory_reserved(device) / (1024 * 1024)) if device.type == "cuda" else 0.0,
    }
    row.update({f"loss_metric/{key}": value for key, value in loss_result.metrics.items()})
    row.update(telemetry)
    return row


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profiled = [row for row in rows if not bool(row.get("warmup", False))]
    if not profiled:
        profiled = list(rows)
    totals: dict[str, float] = {}
    for key in (
        "loader_wait_seconds",
        "transfer_seconds",
        "forward_seconds",
        "loss_seconds",
        "backward_seconds",
        "optimizer_seconds",
        "total_step_seconds",
    ):
        totals[key] = float(sum(float(row.get(key, 0.0)) for row in profiled))
    origins = int(sum(int(row.get("origin_count", 0)) for row in profiled))
    events = int(sum(int(row.get("event_count", 0)) for row in profiled))
    return {
        "profile_blocks": len(profiled),
        "warmup_blocks": sum(1 for row in rows if bool(row.get("warmup", False))),
        "origins": origins,
        "events": events,
        "totals": totals,
        "origins_per_second": origins / max(totals["total_step_seconds"], 1e-9),
        "events_per_second": events / max(totals["total_step_seconds"], 1e-9),
        "avg_total_step_seconds": totals["total_step_seconds"] / max(len(profiled), 1),
        "avg_forward_seconds": totals["forward_seconds"] / max(len(profiled), 1),
        "avg_backward_seconds": totals["backward_seconds"] / max(len(profiled), 1),
        "max_cuda_allocated_mib": max((float(row.get("cuda_max_allocated_mib", 0.0)) for row in rows), default=0.0),
        "max_cuda_reserved_mib": max((float(row.get("cuda_max_reserved_mib", 0.0)) for row in rows), default=0.0),
    }


def iter_dummy_blocks(model_config: ModelConfig, device: torch.device) -> Iterable[PackedTorchBlock]:
    while True:
        yield make_dummy_packed_block(model_config=model_config, device=device)


class ModelProfileReporter:
    def __init__(self, *, state: ProfileState, layout: str) -> None:
        self.state = state
        self.layout = layout
        self._live: Any | None = None

    def __enter__(self) -> "ModelProfileReporter":
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

    def update(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)
        elif self.layout == "text":
            row = self.state.last_row
            print(
                f"blocks={self.state.blocks_done:,} origins={self.state.origins_done:,} "
                f"loss={float(row.get('loss', 0.0)):.6f} total={float(row.get('total_step_seconds', 0.0)):.3f}s",
                flush=True,
            )

    def _render(self) -> Any:
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table

        row = self.state.last_row
        summary = Table.grid(expand=False, padding=(0, 4))
        summary.add_column(no_wrap=True)
        summary.add_column(no_wrap=True)
        summary.add_row(f"[bold]Profile blocks[/] {self.state.blocks_done:,}", f"[bold]Warmup[/] {self.state.warmup_done:,}")
        summary.add_row(f"[bold]Origins[/] {self.state.origins_done:,}", f"[bold]Events[/] {self.state.events_done:,}")
        summary.add_row(f"[bold]Elapsed[/] {self.state.elapsed():,.1f}s", f"[bold]Last loss[/] {float(row.get('loss', 0.0)):.6f}")
        summary.add_row(f"[bold]CUDA alloc[/] {float(row.get('cuda_max_allocated_mib', 0.0)):,.0f} MiB", f"[bold]CUDA reserved[/] {float(row.get('cuda_max_reserved_mib', 0.0)):,.0f} MiB")
        timing = Table(expand=True)
        timing.add_column("Stage", no_wrap=True)
        timing.add_column("Seconds", justify="right", no_wrap=True)
        for key in ("loader_wait_seconds", "transfer_seconds", "forward_seconds", "loss_seconds", "backward_seconds", "optimizer_seconds", "total_step_seconds"):
            timing.add_row(key.replace("_seconds", ""), f"{float(row.get(key, 0.0)):.4f}")
        loader = Table.grid(expand=False, padding=(0, 2))
        loader.add_column("Loader", justify="right", no_wrap=True)
        loader.add_column("Value", no_wrap=True)
        for key, value in [(k, v) for k, v in row.items() if str(k).startswith("loader/state/")][:12]:
            loader.add_row(str(key).replace("loader/state/", ""), f"{value}")
        return Group(
            Panel(summary, title="Packed v1 Model Profile", border_style="cyan"),
            Panel(timing, title="Last Block Timing", border_style="magenta"),
            Panel(loader, title="Loader State", border_style="green"),
        )


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True, default=str) + "\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"PROFILE FAILED: {exc!r}", file=sys.stderr, flush=True)
        traceback.print_exc()
        raise
