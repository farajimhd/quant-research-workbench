"""Serving evaluation: prepare on CPU, replay/pace through service, score separately."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import re
import time
from dataclasses import asdict
from pathlib import Path

import torch

from research.mlops.env import discover_env_files, load_env_files
from .cache import RawBar
from .config import ServiceConfig, ReleaseConfig
from .contracts import InferenceRequest
from .evaluation_data import clock, prepare, read_packet, validate_dataset
from .evaluation_store import Results, atomic_json, bind_manifest, digest, file_hash, gpu_preflight, output_directory
from .models import load_releases, release_summary, prepare_batch
from .decoding import decode_batch
from .runtime import BarGptRuntime

REPO = Path(__file__).resolve().parents[4]


def implementation_hash() -> str:
    paths = []
    for folder in (REPO / "services/bar-gpt", REPO / "research/bar_gpt/v3", REPO / "research/mlops",
                   REPO / "pipelines/market_sip/events"):
        paths.extend(folder.rglob("*.py"))
    return digest({str(path.relative_to(REPO)): file_hash(path) for path in sorted(paths)})


def configuration(root: Path, release_file: Path, model_id: str, device: str, batch_size: int) -> ServiceConfig:
    rows = json.loads(release_file.read_text(encoding="utf-8"))
    selected = [row for row in rows if row["model_id"] == model_id]
    if len(selected) != 1:
        raise ValueError("select exactly one hash-pinned model from the release manifest")
    row = selected[0]
    for field in ("checkpoint_sha256", "contract_hash"):
        if not re.fullmatch("[0-9a-f]{64}", row.get(field, "")):
            raise ValueError(f"missing or invalid {field}")
    if row["version"] != "v3":
        raise ValueError("this scorer supports v3 only")
    release = ReleaseConfig(model_id, "v3", Path(row["checkpoint"]), "shadow", True,
                            row["checkpoint_sha256"], row["contract_hash"])
    # Construct explicitly: never inherit or change production operational intent.
    return ServiceConfig("127.0.0.1:0", device, "float32" if device == "cpu" else "bfloat16",
                         root, "", "", "", batch_size, 500, 0, 16, 64, 4096, 1,
                         (release,), (release,), False, root / "unused-operational.json")


class EvaluationRuntime(BarGptRuntime):
    """Identical infer/cache/decode/journal path; intentionally no application publication."""
    async def _publish_backend(self, prediction: dict) -> None:
        return

    def _infer_sync(self, release, cache, tickers, origin_us):
        start = time.perf_counter()
        batch = prepare_batch(release, cache, tickers, origin_us)
        if release.device.type == "cuda":
            torch.cuda.synchronize(release.device)
        prepared = time.perf_counter()
        output = release.forward(batch)
        if release.device.type == "cuda":
            torch.cuda.synchronize(release.device)
        forwarded = time.perf_counter()
        rows = decode_batch(release, batch, output)
        decoded = time.perf_counter()
        if not hasattr(self, "stage_totals"):
            self.stage_totals = {"batches": 0, "prepare_seconds": 0., "forward_seconds": 0., "decode_seconds": 0.}
        self.stage_totals["batches"] += 1
        self.stage_totals["prepare_seconds"] += prepared-start
        self.stage_totals["forward_seconds"] += forwarded-prepared
        self.stage_totals["decode_seconds"] += decoded-forwarded
        return rows


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(fraction * len(values)) - 1))]


def capacity_summary(results: Results, paced: bool, latency: float, memory: int) -> dict:
    rows = list(results.db.execute("SELECT elapsed,lag,requested,status FROM attempts"))
    successful = [row for row in rows if row[3] == "completed"]
    elapsed = [row[0] for row in successful]
    lag = [row[1] for row in successful]
    return {"status": "completed", "measurement": "paced service replay" if paced else "unpaced service replay",
            "live_certified": False, "excluded": ["live compact-event aggregation", "HTTP transport", "backend delivery"],
            "sweeps": len(successful), "failed_sweeps": len(rows) - len(successful),
            "predictions": sum(row[2] for row in successful),
            "predictions_per_processing_second": sum(row[2] for row in successful) / sum(elapsed) if sum(elapsed) else None,
            "processing_seconds": {f"p{int(p*100)}": percentile(elapsed, p) for p in (.5, .95, .99)},
            "scheduled_completion_seconds": {f"p{int(p*100)}": percentile(lag, p) for p in (.5, .95, .99)},
            "deadline_misses": sum(value > latency for value in lag) if paced else None,
            "last_completion_lag_seconds": lag[-1] if lag else None,
            "peak_cuda_allocated_bytes": memory,
            "verdict": "insufficient for live certification; run live-observe with the target population"}


async def replay(args, config: ServiceConfig, code_hash: str) -> dict:
    plan, dataset = validate_dataset(args.dataset)
    output = output_directory(args.output)
    telemetry = gpu_preflight() if config.device == "cuda" else {"device": "cpu"}
    runtime = EvaluationRuntime(config)
    # start() uses the actual immutable service loader and bounded background loops.
    await runtime.start()
    results = Results(output / "results.sqlite")
    try:
        release = runtime.releases[args.model_id]
        if digest(asdict(release.data_config)) != digest(plan["manifest"]["data_config"]):
            raise RuntimeError("checkpoint data contract differs from prepared dataset")
        if code_hash != plan["manifest"]["code_hash"]:
            raise RuntimeError("source implementation changed since preparation; prepare a new dataset")
        if plan["manifest"]["partition"] == "acceptance":
            if not args.selection:
                raise RuntimeError("acceptance replay requires a frozen selection JSON")
            selection = json.loads(args.selection.read_text())
            if selection.get("checkpoint_hash") != release.checkpoint_hash or not selection.get("development_evidence_hash"):
                raise RuntimeError("acceptance selection must bind checkpoint and development evidence")
        else:
            selection = None
        experiment = {"dataset_hash": digest(dataset), "release": release_summary(release), "code_hash": code_hash,
                      "batch_size": config.maximum_batch_size, "paced": args.paced, "steps": args.steps,
                      "symbol_count": getattr(args, "symbol_count", 0),
                      "latency_seconds": args.latency_seconds, "selection": selection,
                      "python": platform.python_version(), "torch": torch.__version__,
                      "quantiles": list(release.model.config.quantiles)}
        bind_manifest(output / "experiment.json", experiment)
        atomic_json(output / "hardware.json", telemetry)
        if args.paced and results.db.execute("SELECT count(*) FROM attempts").fetchone()[0]:
            raise RuntimeError("paced timing trials cannot resume; use a fresh output directory")
        if config.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        steps = 0
        for day in plan["manifest"]["days"]:
            packets = [row for row in dataset["packets"] if row["day"] == day]
            if getattr(args, "symbol_count", 0):
                if len(packets) < args.symbol_count:
                    raise ValueError("dataset has fewer symbols than the requested capacity trial")
                packets = sorted(packets, key=lambda row: row["ticker"])[:args.symbol_count]
            scope = "evaluation-" + day
            cache = runtime._new_cache()
            runtime.caches[scope] = cache
            request = {"mode": "replay", "trigger_mode": "manual", "tickers": [row["ticker"] for row in packets],
                       "model_ids": [args.model_id], "clock_us": clock(day, plan["manifest"]["start"])}
            runtime.scopes[scope] = {"scope_id": scope, "cache_id": scope, "request": request,
                                     "expires_monotonic": time.monotonic() + 86400}
            connections = {row["ticker"]: read_packet(args.dataset / row["file"]) for row in packets}
            try:
                # Origin schedule is metadata only. Future feature/target values are never admitted.
                schedule: dict[int, list[str]] = {}
                for ticker, db in connections.items():
                    for (origin,) in db.execute("SELECT origin FROM targets ORDER BY origin"):
                        schedule.setdefault(origin, []).append(ticker)
                previous = -1
                wall_start = None
                first_origin = min(schedule)
                for origin, tickers in sorted(schedule.items()):
                    if args.steps and steps >= args.steps:
                        break
                    due = 0. if wall_start is None else wall_start + (origin - first_origin) / 1e6
                    if args.paced and wall_start is not None:
                        await asyncio.sleep(max(0., due - time.perf_counter()))
                    started = time.perf_counter()
                    for ticker, db in connections.items():
                        cursor = db.execute("SELECT payload FROM bars WHERE available>? AND available<=? ORDER BY available,view,start",
                                            (previous, origin))
                        while rows := cursor.fetchmany(2048):
                            cache.upsert_many([RawBar(**json.loads(row[0])) for row in rows], derive=False)
                    previous = origin
                    request["clock_us"] = origin
                    runtime.scopes[scope]["expires_monotonic"] = time.monotonic() + 86400
                    if wall_start is None:
                        # Warm-up and CUDA first-use are excluded from sustained timing and recorded separately.
                        await runtime.infer(InferenceRequest(scope_id=scope, tickers=tickers, model_ids=[args.model_id], origin_us=origin))
                        atomic_json(output / f"warm-{day}.json", {"seconds": time.perf_counter() - started,
                                    "readiness": [cache.readiness(t, origin, config.minimum_warm_1s_bars) for t in connections]})
                        if args.parity:
                            await verify_batch_parity(runtime, scope, tickers, args.model_id, origin, output / f"parity-{day}.json")
                        wall_start = time.perf_counter()
                        due = wall_start
                        started = wall_start
                    pending = sorted(set(tickers) - results.existing(day, origin, args.model_id))
                    if not pending:
                        continue
                    try:
                        predictions = await runtime.infer(InferenceRequest(scope_id=scope, tickers=pending,
                                                           model_ids=[args.model_id], origin_us=origin))
                        elapsed = time.perf_counter() - started
                        lag = time.perf_counter() - due if args.paced else elapsed
                        results.record(day, origin, args.model_id, pending, predictions, release.checkpoint_hash, elapsed, lag)
                    except Exception as exc:
                        results.failure(day, origin, len(pending), str(exc))
                        raise
                    steps += 1
                    if steps == 1 or steps % 60 == 0:
                        print(f"replay day={day} completed_sweeps={steps} symbols={len(pending)} elapsed={elapsed:.3f}s lag={lag:.3f}s", flush=True)
                    if args.paced and lag > args.max_backlog_seconds:
                        raise RuntimeError("paced replay exceeded backlog bound; capacity trial failed")
            finally:
                for db in connections.values():
                    db.close()
                runtime.scopes.pop(scope, None)
                runtime.caches.pop(scope, None)
        report = capacity_summary(results, args.paced, args.latency_seconds,
                                  torch.cuda.max_memory_allocated() if config.device == "cuda" else 0)
        report["instrumented_stages_including_warmup"] = getattr(runtime, "stage_totals", {})
        atomic_json(output / "capacity.json", report)
        return report
    finally:
        results.close()
        await runtime.stop()


async def verify_batch_parity(runtime, scope, tickers, model_id, origin, destination):
    from research.bar_gpt.v3.data import BarGPTExample, collate_examples
    release = runtime.releases[model_id]
    selected = tickers[:min(4, len(tickers))]
    batched = runtime._infer_sync(release, runtime.caches[scope], selected, origin)
    max_error = 0.
    for ticker, combined in zip(selected, batched):
        single = runtime._infer_sync(release, runtime.caches[scope], [ticker], origin)[0]
        actual = torch.tensor(combined["raw"]["horizon_quantiles"])
        expected = torch.tensor(single["raw"]["horizon_quantiles"])
        max_error = max(max_error, (actual - expected).abs().max().item())
        torch.testing.assert_close(actual, expected, rtol=.02 if release.device.type == "cuda" else 1e-5,
                                   atol=.005 if release.device.type == "cuda" else 1e-6)
        cache = runtime.caches[scope]
        prepared = prepare_batch(release, cache, [ticker], origin)
        raw, starts, ends, available, masks = {}, {}, {}, {}, {}
        for view in prepared.views:
            values = cache.rows(ticker, view, origin)
            raw[view] = torch.tensor([list(row.values) for row in values] + [[0.] * len(values[0].values)])
            starts[view] = torch.tensor([row.bar_start_us for row in values] + [0])
            ends[view] = torch.tensor([row.bar_end_us for row in values] + [0])
            available[view] = torch.tensor([row.available_at_us for row in values] + [0])
            masks[view] = torch.tensor([True] * len(values) + [False])
        example = BarGPTExample(ticker, "parity", raw, masks, starts, ends, available,
                   prepared.origin_indices[0].cpu(), prepared.origin_timestamps_us[0].cpu(),
                   {name: values[0].cpu() for name, values in prepared.asof_indices.items()},
                   raw["1s"][:-1], available["1s"][:-1], origin, torch.ones(len(raw["1s"])-1),
                   torch.zeros(0, dtype=torch.long), torch.zeros(0,4), prepared.origin_indices[0].cpu(),
                   None, None, tuple(release.data_config.horizons_us), 1_000_000, 0)
        offline = collate_examples([example], balance_activity_regimes=False)
        for view in prepared.views:
            torch.testing.assert_close(prepared.views[view].cpu(), offline.views[view], rtol=0, atol=0)
            torch.testing.assert_close(prepared.view_masks[view].cpu(), offline.view_mask[view])
        torch.testing.assert_close(prepared.target_clock_features.cpu(), offline.horizon_clock_features)
    atomic_json(destination, {"kind": "single-versus-batched-service", "tickers": selected,
                             "maximum_absolute_error": max_error, "offline_collation_feature_parity": "passed",
                             "source_loader_parity": "same prepared support; independent source extraction not established"})


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gpu-status", help="read-only fail-closed GPU idle check")
    for command in ("prepare", "replay", "benchmark"):
        child = commands.add_parser(command)
        child.add_argument("--releases", type=Path, required=True)
        child.add_argument("--model-id", required=True)
        child.add_argument("--output", type=Path, required=True)
        if command == "prepare":
            child.add_argument("--tickers", required=True)
            child.add_argument("--days", required=True, help="comma-separated ISO session dates")
            child.add_argument("--start", default="09:30:00")
            child.add_argument("--end", default="16:00:00")
            child.add_argument("--partition", choices=("development", "acceptance"), default="development")
        else:
            child.add_argument("--dataset", type=Path, required=True)
            child.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
            child.add_argument("--batch-size", type=int, default=8)
            child.add_argument("--steps", type=int, default=120, help="0 for the complete dataset; default bounded pilot")
            child.add_argument("--paced", action="store_true")
            child.add_argument("--latency-seconds", type=float, default=1.)
            child.add_argument("--max-backlog-seconds", type=float, default=10.)
            child.add_argument("--parity", action="store_true")
            child.add_argument("--selection", type=Path)
            child.add_argument("--symbol-count", type=int, default=0, help="0 uses all frozen symbols")
            if command == "benchmark":
                child.add_argument("--batch-sizes", default="1,4,8,16,32,64")
                child.add_argument("--symbol-counts", default="10,100,500")
    scoring = commands.add_parser("score")
    scoring.add_argument("--dataset", type=Path, required=True)
    scoring.add_argument("--run", type=Path, required=True)
    scoring.add_argument("--allow-partial", action="store_true", help="pilot report only; never acceptance")
    observe = commands.add_parser("live-observe", help="read-only actual live publication latency; no GPU work")
    observe.add_argument("--url", default="http://127.0.0.1:8805")
    observe.add_argument("--model-id", required=True)
    observe.add_argument("--tickers", required=True)
    observe.add_argument("--seconds", type=int, default=900)
    observe.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    load_env_files(discover_env_files(REPO))
    if args.command == "gpu-status":
        print(json.dumps(gpu_preflight(), indent=2))
        return
    if args.command == "score":
        from .evaluation_score import score
        print(json.dumps(score(args.dataset, args.run, args.allow_partial), indent=2))
        return
    if args.command == "live-observe":
        from .evaluation_live import observe
        print(json.dumps(asyncio.run(observe(args)), indent=2))
        return
    root = output_directory(args.output)
    code_hash = implementation_hash()
    config = configuration(root, args.releases, args.model_id, "cpu" if args.command == "prepare" else args.device,
                           8 if args.command == "prepare" else args.batch_size)
    try:
        if args.command == "prepare":
            tickers = sorted(set(args.tickers.upper().split(",")))
            if not tickers or any(not re.fullmatch(r"[A-Z0-9.^_-]+", ticker) for ticker in tickers):
                raise ValueError("invalid ticker list")
            days = sorted(set(args.days.split(",")))
            for day in days:
                if clock(day, args.start) >= clock(day, args.end):
                    raise ValueError("start must precede end")
            release = load_releases(config)[args.model_id]
            result = prepare(root, release, tickers, days, args.start, args.end, code_hash, args.partition)
            print(f"prepared packets={len(result['packets'])} output={root}")
        else:
            if args.batch_size < 1 or args.steps < 0 or args.latency_seconds <= 0 or args.max_backlog_seconds <= 0:
                raise ValueError("invalid batch, step, or latency bound")
            if args.command == "benchmark":
                from dataclasses import replace
                import copy
                for count in map(int, args.symbol_counts.split(",")):
                    for batch in map(int, args.batch_sizes.split(",")):
                        if count < 1 or batch < 1:
                            raise ValueError("benchmark sizes must be positive")
                        trial = copy.copy(args)
                        trial.symbol_count = count
                        trial.output = root / f"symbols-{count}-batch-{batch}"
                        trial.output.mkdir(exist_ok=True)
                        trial_config = replace(config, maximum_batch_size=batch, runtime_root=trial.output)
                        print(json.dumps(asyncio.run(replay(trial, trial_config, code_hash)), indent=2))
            else:
                print(json.dumps(asyncio.run(replay(args, config, code_hash)), indent=2))
    except BaseException as exc:
        atomic_json(root / "last_failure.json", {"type": type(exc).__name__, "message": str(exc),
                                               "status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"})
        raise
