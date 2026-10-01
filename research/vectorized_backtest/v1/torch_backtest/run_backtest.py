"""Real ARTE session benchmark with explicit alignment/transfer/JIT/replay costs."""

import os
import sys
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
os.environ.setdefault("POLARS_MAX_THREADS", "1")
REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import datetime
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import torch

from research.vectorized_backtest.v1.strategy_encoding import Broker, Funnel, Session
from research.vectorized_backtest.v1.strategy_encoding import run_backtest as shared
from research.vectorized_backtest.v1.torch_backtest import (
    ReplayRunner,
    compile_strategy,
    prepare_session,
    to_tensors,
)
from research.vectorized_backtest.v1.torch_backtest.examples import (
    history_example,
    seeded_example,
)
from research.vectorized_backtest.v1.torch_backtest.export import to_frames
from research.vectorized_backtest.v1.torch_backtest.reference import evaluate_reference


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument(
        "--backend",
        choices=("eager", "compile", "cudagraph", "compiled_graph"),
        default="compiled_graph",
    )
    parser.add_argument("--example", choices=("seeded", "history"), default="history")
    parser.add_argument("--lookback", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--date", default="2026-08-18")
    parser.add_argument("--start", default="04:00")
    parser.add_argument("--end", default="09:30")
    parser.add_argument("--strategy-ms", type=int, default=1000)
    parser.add_argument("--broker-ms", type=int, default=1000)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-gib", type=float, default=4.0)
    parser.add_argument("--compare-polars", action="store_true")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("D:/TradingML/runtimes/vectorized_backtest/encoded_strategy_v1"),
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=Path("D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v1"),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.batch <= 256 or not 1 <= args.repeats <= 10:
        parser.error("Batch/repeats exceed bounded defaults")
    if not args.runtime.parent.is_dir():
        raise FileNotFoundError("Required runtime parent unavailable")
    args.runtime.mkdir(exist_ok=True)
    run = args.runtime / uuid4().hex
    run.mkdir()
    for key, folder in (
        ("TORCHINDUCTOR_CACHE_DIR", "inductor"),
        ("TRITON_CACHE_DIR", "triton"),
        ("TORCH_EXTENSIONS_DIR", "extensions"),
    ):
        os.environ.setdefault(key, str(args.runtime / folder))
    print(
        "Run: "
        + subprocess.list2cmdline(
            [
                sys.executable,
                "-B",
                "-m",
                "research.vectorized_backtest.v1.torch_backtest.run_backtest",
                *sys.argv[1:],
            ]
        ),
        flush=True,
    )
    torch.set_num_threads(1)
    shared.load_env_files(shared.discover_env_files(REPO), verbose=False)
    if not any(
        os.environ.get(k)
        for k in (
            "CLICKHOUSE_URL",
            "REAL_LIVE_CLICKHOUSE_WRITE_URL",
            "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL",
        )
    ):
        os.environ["CLICKHOUSE_URL"] = (
            os.environ.get("QMD_CLICKHOUSE_URL") or shared.workstation_clickhouse_url()
        )
    for target, source in (
        ("CLICKHOUSE_USER", "QMD_CLICKHOUSE_USER"),
        ("CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_PASSWORD"),
    ):
        if os.environ.get(source):
            os.environ.setdefault(target, os.environ[source])
    ny = ZoneInfo("America/New_York")
    config = Session(
        shared.REMOTE / "market-day" / f"{shared.BUILD}.json",
        shared.REMOTE / "build-ledger-v2.sqlite3",
        args.cache_root,
        datetime.fromisoformat(f"{args.date}T{args.start}").replace(tzinfo=ny),
        datetime.fromisoformat(f"{args.date}T{args.end}").replace(tzinfo=ny),
        args.strategy_ms,
        args.broker_ms,
    )
    started = perf_counter()
    program, catalog = (
        seeded_example(args.seed)
        if args.example == "seeded"
        else history_example(args.lookback)
    )
    strategy = compile_strategy(program, catalog)
    ir_seconds = perf_counter() - started
    report = {
        "version": "torch-backtest-v1",
        "args": vars(args),
        "program": program.to_dict(),
        "catalog": asdict(catalog),
        "session": asdict(config),
        "broker": asdict(Broker()),
        "funnel": asdict(Funnel()),
        "ir_compile_seconds": ir_seconds,
        "requirements": [asdict(f) for f in strategy.dependencies],
        "torch": torch.__version__,
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "code_sha256": hashlib.sha256(
            b"".join(
                p.name.encode() + p.read_bytes()
                for p in sorted(Path(__file__).parent.glob("*.py"))
            )
        ).hexdigest(),
    }
    path = run / "report.json"

    def save():
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    save()
    progress = lambda value: print(json.dumps(value), flush=True)
    prepared = prepare_session(
        config, Funnel(), strategy.dependencies, progress=progress
    )
    report["preparation"] = prepared.metrics
    tape = to_tensors(prepared, strategy, device=args.device, max_gib=args.max_gib)
    report["tensor_preparation"] = tape.metrics
    report["device"] = str(tape.device)
    if tape.device.type == "cuda":
        report["gpu"] = torch.cuda.get_device_name(tape.device)
    save()
    values = [list(program.values)] * args.batch
    print("Preparing execution backend: " + args.backend, flush=True)
    runner = ReplayRunner(strategy, tape, values=values, backend=args.backend)
    runs = []
    for _ in range(args.repeats):
        result = runner.run(progress=progress)
        runs.append(result["wall_seconds"])
    accounts, state = to_frames(result, tape)
    accounts.write_parquet(run / "accounts.parquet")
    state.write_parquet(run / "final_state.parquet")
    report["backtest"] = {
        k: (v.cpu().tolist() if isinstance(v, torch.Tensor) else v)
        for k, v in result.items()
        if k not in {"state", "accounts"}
    }
    report["replay_seconds"] = runs
    report["median_replay_seconds"] = float(np.median(runs))
    save()
    if args.compare_polars:
        print("Validating complete trajectory against Polars...", flush=True)
        expected = evaluate_reference(program, catalog, prepared, Broker())
        reference = (
            expected["accounts"]
            .select("cash", "realized_pnl", "equity", "market_value")
            .to_numpy()
        )
        actual = accounts.select(
            "cash", "realized_pnl", "equity", "market_value"
        ).to_numpy()
        np.testing.assert_allclose(actual, reference, rtol=0, atol=1e-7)
        reference_state = expected["state"].sort("listing_id")
        assert state["listing_id"].equals(reference_state["listing_id"])
        for key in ("quantity", "remaining", "side", "submitted_us"):
            assert state[key].equals(reference_state[key]), key
        for key in ("book_cost", "stop", "target", "mark"):
            np.testing.assert_allclose(
                state[key].to_numpy(),
                reference_state[key].to_numpy(),
                rtol=0,
                atol=1e-7,
            )
        assert result["filled_shares"][0].item() == expected["filled_shares"]
        report["reference"] = {
            "full_account_and_final_state_parity": True,
            "atol": 1e-7,
            "wall_seconds": expected["wall_seconds"],
        }
        save()
    print(
        json.dumps(
            {
                "tensor_preparation": tape.metrics,
                "backtest": report["backtest"],
                "replay_seconds": runs,
                "reference": report.get("reference"),
            },
            indent=2,
        ),
        flush=True,
    )
    print("Report: " + str(path), flush=True)


if __name__ == "__main__":
    main()
