"""Runnable real-data example; reports cold preparation separately from replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

# Set these before importing any research modules or Polars.
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
os.environ.setdefault("POLARS_MAX_THREADS", "1")
REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.mlops.env import discover_env_files, load_env_files
from research.vectorized_backtest.v1.strategy_encoding import compile_strategy
from research.vectorized_backtest.v1.strategy_encoding.clickhouse import prepare_session
from research.vectorized_backtest.v1.strategy_encoding.config import (
    Broker,
    Funnel,
    Session,
)
from research.vectorized_backtest.v1.strategy_encoding.examples import momentum_example
from research.vectorized_backtest.v1.strategy_encoding.replay import evaluate
from scripts.clickhouse.install_market_day_certificate_layout import (
    workstation_clickhouse_url,
)

BUILD = "1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5"
REMOTE = Path("//DESKTOP-SAAI85T/Workstation-D/TradingML/runtimes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-08-18")
    parser.add_argument("--start", default="04:00")
    parser.add_argument("--end", default="09:30")
    parser.add_argument("--strategy-ms", type=int, default=1000)
    parser.add_argument("--broker-ms", type=int, default=1000)
    parser.add_argument(
        "--manifest", type=Path, default=REMOTE / "market-day" / f"{BUILD}.json"
    )
    parser.add_argument(
        "--ledger", type=Path, default=REMOTE / "build-ledger-v2.sqlite3"
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=Path("D:/TradingML/runtimes/vectorized_backtest/encoded_strategy_v1"),
    )
    parser.add_argument("--min-price", type=float, default=1.0)
    parser.add_argument("--max-price", type=float, default=50.0)
    parser.add_argument("--gap-bps", type=float, default=2.0)
    parser.add_argument("--min-rsi", type=float, default=50.0)
    parser.add_argument("--cash-fraction", type=float, default=0.02)
    args = parser.parse_args(argv)
    load_env_files(discover_env_files(REPO), verbose=False)
    if not any(
        os.environ.get(key)
        for key in (
            "CLICKHOUSE_URL",
            "REAL_LIVE_CLICKHOUSE_WRITE_URL",
            "TD__DATABASE__CLICKHOUSE__ENDPOINT_URL",
        )
    ):
        os.environ["CLICKHOUSE_URL"] = (
            os.environ.get("QMD_CLICKHOUSE_URL") or workstation_clickhouse_url()
        )
    for target, source in (
        ("CLICKHOUSE_USER", "QMD_CLICKHOUSE_USER"),
        ("CLICKHOUSE_PASSWORD", "QMD_CLICKHOUSE_PASSWORD"),
    ):
        if os.environ.get(source):
            os.environ.setdefault(target, os.environ[source])
    ny = ZoneInfo("America/New_York")
    config = Session(
        args.manifest,
        args.ledger,
        args.runtime,
        datetime.fromisoformat(f"{args.date}T{args.start}").replace(tzinfo=ny),
        datetime.fromisoformat(f"{args.date}T{args.end}").replace(tzinfo=ny),
        args.strategy_ms,
        args.broker_ms,
    )
    program, catalog = momentum_example(args.gap_bps, args.min_rsi, args.cash_fraction)
    compiled = compile_strategy(program, catalog)
    command = [
        "python",
        "-B",
        "-m",
        "research.vectorized_backtest.v1.strategy_encoding.run_backtest",
    ]
    for name, value in vars(args).items():
        command.extend(["--" + name.replace("_", "-"), str(value)])
    print("Run: " + subprocess.list2cmdline(command), flush=True)
    progress = lambda item: print(json.dumps(item), flush=True)
    prepared = prepare_session(
        config,
        Funnel(args.min_price, args.max_price),
        compiled.dependencies,
        progress=progress,
    )
    result = evaluate(compiled, prepared, Broker(), progress=progress)
    metrics = {
        key: value
        for key, value in result.items()
        if key not in {"accounts", "state", "fills"}
    }
    run_id = uuid4().hex
    report = {
        "run_id": run_id,
        "version": "atomic-strategy-backtest-v1",
        "program": program.to_dict(),
        "catalog": asdict(catalog),
        "session": asdict(config),
        "broker": asdict(Broker()),
        "funnel": asdict(Funnel(args.min_price, args.max_price)),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "code_sha256": hashlib.sha256(
            b"".join(
                path.name.encode() + path.read_bytes()
                for path in sorted(Path(__file__).parent.glob("*.py"))
            )
        ).hexdigest(),
        "credentials_present": any(
            os.environ.get(key)
            for key in (
                "CLICKHOUSE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
            )
        ),
        "preparation": prepared.metrics,
        "backtest": metrics,
        "strategy_ms": args.strategy_ms,
        "broker_ms": args.broker_ms,
        "fill_model": "persistent-participation-vwap-close-protection",
        "example_policy": "EMA/RSI research policy, not Candidate 328 lifecycle",
    }
    run_directory = args.runtime / "runs" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    result["accounts"].write_parquet(run_directory / "accounts.parquet")
    result["state"].write_parquet(run_directory / "final_state.parquet")
    path = run_directory / "report.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps({"preparation": prepared.metrics, "backtest": metrics}, indent=2),
        flush=True,
    )
    print(f"Report: {path}", flush=True)


if __name__ == "__main__":
    main()
