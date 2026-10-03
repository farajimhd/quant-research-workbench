"""Run the approved Sep3 premarket comparison through the native app service."""
import argparse
import asyncio
from contextlib import closing
from datetime import date, time
import json
import os
from pathlib import Path
import platform
import sys
from time import monotonic
from uuid import uuid4

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def run(apply):
    from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
    from src.backend.backtest_strategy_forty_four_configuration import certify_configuration
    from src.backend.backtest_strategy_forty_four_controller import reader_factory
    from src.backend.replay_run_service import ReplayRunService, ReplayRunDefinition, backtest_preflight
    from src.trading_runtime.runtime import RunMode
    from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
    root = Path("D:/TradingML/runtimes/strategy44/comparison")
    if platform.node().upper() != "DESKTOP-SAAI85T" or not root.parent.is_dir():
        raise RuntimeError("Comparison requires the managed workstation runtime")
    _load_private_credentials()
    root = root / uuid4().hex
    root.mkdir(parents=True)
    began = monotonic()
    print("Strategy 44 | 2026-09-03 premarket | $10,000 | 1s decisions / 100ms fills", flush=True)
    try:
        with closing(reader_factory()) as reader:
            revision = certify_configuration(reader).revision()
        preparation = asyncio.create_task(asyncio.to_thread(backtest_preflight,
            anchor_date=date(2026,9,4), session_count=1, initial_cash=10_000,
            start_time=time(4), end_time=time(9,30), tickers=(), configuration_revision=revision))
        while not preparation.done():
            print(f"Preflight | active 1 | queued 0 | completed 0 | failed 0 | elapsed {monotonic()-began:.0f}s", flush=True)
            await asyncio.wait((preparation,), timeout=20)
        preflight = await preparation
        (root / "preflight.json").write_text(json.dumps(preflight, indent=2, default=str), encoding="utf-8")
        if not preflight["ready"]:
            blocked = [row["summary"] for row in preflight["checks"] if row["status"] != "ready"]
            raise RuntimeError("Comparison preflight blocked: " + "; ".join(blocked))
        print(f"Preflight ready | source receipt {root / 'preflight.json'}", flush=True)
        if not apply:
            return
        definition = ReplayRunDefinition(mode=RunMode.BACKTEST, session_date=date(2026,9,3),
            final_session_date=date(2026,9,3), start_time=time(4), end_time=time(9,30),
            initial_cash=10_000, tickers=(), configuration_revision=revision, execution_interval="100ms",
            market_data_plan=preflight["market_data_plan"], causal_v7_plan=preflight["causal_v7_plan"],
            new_order_activation_delay_ms=0., experimental_structure_book="level-book-v7")
        service = ReplayRunService(runtime_root=root.parent)
        controller = await service.create(definition)
        (root / "run.json").write_text(json.dumps(dict(run_id=controller.run_id,
            session="2026-09-03", kind="premarket", initial_cash=10_000), indent=2), encoding="utf-8")
        while not controller._task.done():
            print(f"Backtest {controller.run_id} | {controller.status} | phase {controller._preparation_stage} | "
                f"clock {controller.current_time} | completed {controller.processed_events}/198000 100ms boundaries | "
                f"active 1 | queued 0 | failed {int(controller.status == 'failed')} | elapsed {monotonic()-began:.0f}s", flush=True)
            await asyncio.wait((controller._task,), timeout=20)
        await controller._task
        result = dict(run_id=controller.run_id, status=controller.status, error=controller.error,
                      receipt=getattr(controller, "comparison_receipt", None), elapsed_seconds=monotonic()-began)
        (root / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        if controller.status != "completed":
            raise RuntimeError(f"Native comparison failed: {controller.error}")
        from src.backend.backtest_v4_saved_review import load_v4_performance_report
        from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env
        with closing(backtest_v4_operator_client_from_env()) as reader:
            report = await asyncio.to_thread(load_v4_performance_report, reader, controller.run_id)
        (root / "native-performance.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        torch = dict(net_pnl=-371.26932136827236, fees=163.87, max_drawdown=434.15468174429043,
                     acquired_shares=16387, fills=390, ticker_batches=2, decision_ms=1000, broker_ms=1000)
        comparison = dict(session="2026-09-03 premarket", initial_cash=10_000,
            prior_torch_v2_revision2=torch, native_summary=report.get("report", {}).get("summary"), native_receipt=controller.comparison_receipt,
            comparison_scope="Original Torch v2 revision2 baseline; revised v2 financial replay is a separate experiment",
            broker_difference="Native 100ms vs Torch 1s; execution differences accepted")
        (root / "comparison.json").write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
        print(f"Native comparison completed and cold-read | results {root}", flush=True)
        print(json.dumps(comparison, default=str), flush=True)
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Run one financial backtest after complete preflight")
    args = parser.parse_args()
    asyncio.run(run(args.apply))


if __name__ == "__main__":
    main()
