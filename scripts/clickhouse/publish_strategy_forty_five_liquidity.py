"""Publish certified completed liquidity windows; default checks without writes."""
import argparse
from contextlib import closing
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

from src.backend.backtest_market_data import readonly_clickhouse_client
from src.backend.backtest_strategy_forty_five_plan import certify_source_plan
from src.backend.backtest_strategy_forty_five_liquidity import certify_liquidity
from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from src.trading_runtime.strategy_forty_five_liquidity_schema import verify_tables
from pipelines.strategy_one.strategy_forty_five_liquidity_publication import publish_unit
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_forty_five_liquidity import credential, grant_set, GRANTS, PRINCIPAL, URL
from scripts.clickhouse.publish_strategy_forty_three_v7 import BUILD
from scripts.clickhouse.install_market_day_certificate_layout import workstation_clickhouse_url


def run(*, session, build, apply):
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Strategy 45 publication requires the managed workstation")
    root = Path("D:/TradingML/runtimes")
    secret = Path("D:/TradingML/secrets/backtest_v3_read.env")
    if not root.is_dir() or not secret.is_file():
        raise RuntimeError("Required workstation runtime or reader credential unavailable")
    os.environ.setdefault("BACKTEST_V3_READ_CREDENTIAL_FILE", str(secret))
    started = monotonic()
    print(f"Strategy 45 liquidity | {session} premarket | {'APPLY' if apply else 'CHECK'}", flush=True)
    with closing(readonly_clickhouse_client(market_stream=True, v3_read_principal=True)) as reader:
        plan = certify_source_plan(session=session, build=build, reader=reader)
        verify_tables(reader)
        print(f"Certified parents | {len(plan.tickers)} squeeze candidates | completed 30s candles", flush=True)
        if not apply:
            certificate = certify_liquidity(plan, reader)
            print(f"CHECK passed | source {certificate.token}", flush=True)
            return
        artifact = root / "strategy45" / "liquidity-publication" / uuid4().hex
        artifact.mkdir(parents=True, exist_ok=False)
        completed = []
        with closing(ClickHouseHttpClient(workstation_clickhouse_url(), PRINCIPAL, credential(account_exists=True), persistent=True)) as writer:
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL or grant_set(writer) != GRANTS:
                raise RuntimeError("Strategy 45 producer authority differs")
            verify_tables(reader)
            with closing(open_workstation_keeper_session()) as owner:
                for ticker in plan.tickers:
                    print(f"Windows | completed {len(completed)}/{len(plan.tickers)} | active {ticker} | queued {len(plan.tickers)-len(completed)-1} | skipped 0 | retried 0 | failed 0 | {monotonic()-started:.1f}s", flush=True)
                    try:
                        receipt = publish_unit(plan, ticker, reader, writer, owner.client)
                    except BaseException as error:
                        (artifact / "failure.json").write_text(json.dumps(dict(ticker=ticker,
                            error_type=type(error).__name__, completed=len(completed),
                            remaining=len(plan.tickers)-len(completed))), encoding="utf-8")
                        print(f"STOPPED | completed {len(completed)} | failed/interrupted 1 | restart with the same arguments", flush=True)
                        raise
                    completed.append(dict(ticker=ticker, receipt=receipt))
                    temporary = artifact / "progress.tmp"
                    temporary.write_text(json.dumps(dict(session=session, build=build, completed=completed,
                        pending=len(plan.tickers)-len(completed)), indent=2), encoding="utf-8")
                    temporary.replace(artifact / "progress.json")
            certificate = certify_liquidity(plan, reader)
            verify_tables(reader)
            (artifact / "certificate.json").write_text(json.dumps(dict(token=certificate.token,
                completed=len(completed), elapsed_seconds=monotonic()-started), indent=2), encoding="utf-8")
            print(f"COMPLETE | {len(completed)} certified units | receipt {artifact}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default="2026-09-03")
    parser.add_argument("--build", default=BUILD)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        run(session=args.session, build=args.build, apply=args.apply)
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Strategy 45 liquidity FAILED: {type(error).__name__}: {error}" if type(error) is RuntimeError
              else f"Strategy 45 liquidity FAILED: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
