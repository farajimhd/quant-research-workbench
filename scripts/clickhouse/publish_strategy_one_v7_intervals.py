"""Publish causal Strategy 1 V7 intervals from certified ARTE 1s bars.

This is a separate, bounded producer campaign, never a Backtest fallback.
Only normalized clocks, level intervals, and coverage are inserted. Reruns
verify covered ticker-days; incomplete attempts are not visible to Backtest.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import platform
import sys
import traceback
from threading import Lock, local
from time import monotonic

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from pipelines.strategy_one.v7_interval_derivation import derive_ticker_day
from pipelines.strategy_one.v7_interval_publication import publish_unit
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_one_candidate_producer import (
    PRINCIPAL, WORKSTATION_IPV4, _GRANTS, _credential, _grant_set,
)
from scripts.clickhouse.publish_strategy_one_candidates import (
    DEFAULT_DAY, FULL_SESSION_BOUNDARY_MS, _certified_plan,
)
from src.backend.backtest_market_data import (
    project_market_day_plan, readonly_clickhouse_client,
)
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.structural_v7_seed import certified_seed_plan
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST
from src.trading_runtime.strategy_one_v7_interval_schema import verify_tables


class V7IntervalCampaignFailure(RuntimeError):
    """Locally constructed, secret-free operator diagnostic."""


def _writer(password: str) -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        f"http://{WORKSTATION_IPV4}:18123", PRINCIPAL, password,
        timeout_seconds=60, persistent=False)


def _plans(*, session_date: str, build_id: str, ticker: str,
           max_tickers: int):
    market = _certified_plan(session_date=session_date, build_id=build_id)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        verify_tables(reader)
        candidates = certify_candidate_plan(
            market, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=FULL_SESSION_BOUNDARY_MS, client=reader)
        candidate_tickers = strategy_one_v7_tickers(candidates.prepared)
        if not candidate_tickers:
            raise RuntimeError("No certified candidate ticker requires V7 derivation")
        if ticker and ticker not in candidate_tickers:
            raise ValueError("Requested ticker has no certified Strategy 1 V7 candidate")
        tickers = (ticker,) if ticker else candidate_tickers[:max_tickers or None]
        seeds = certified_seed_plan(project_market_day_plan(market, tickers),
                                    reader)
    return market, seeds, tickers


def publish_session(*, session_date: str, build_id: str,
                    ticker: str, max_tickers: int,
                    workers: int) -> dict[str, int]:
    started = monotonic()
    print(f"Certifying {session_date} Strategy 1 market, candidate, and V7 "
          "seed authority...", flush=True)
    market, seeds, tickers = _plans(
        session_date=session_date, build_id=build_id, ticker=ticker,
        max_tickers=max_tickers)
    password = _credential(account_exists=True)
    with closing(_writer(password)) as checked:
        if (checked.execute("SELECT currentUser()").strip() != PRINCIPAL
                or _grant_set(checked) != _GRANTS):
            raise RuntimeError("Strategy 1 producer lacks exact derived-table grants")
    print(f"Deriving V7 from completed 1s bars: {len(tickers)} candidate "
          f"tickers, {workers} workers.", flush=True)
    state = local()
    opened: list[object] = []
    opened_lock = Lock()

    def worker(symbol: str) -> str:
        clients = getattr(state, "clients", None)
        if clients is None:
            clients = (_writer(password),
                       v3_client("read", market_stream=True, persistent=False),
                       v3_client("read", market_stream=True, persistent=False))
            with opened_lock:
                opened.extend(clients)
            state.clients = clients
        writer, source, coverage_reader = clients
        item = derive_ticker_day(market=market, seeds=seeds,
                                 session_date=session_date, ticker=symbol,
                                 reader=source)
        return publish_unit(writer, coverage_reader, item)

    published = skipped = failed = 0
    first_failure: tuple[str, BaseException] | None = None
    last_report = monotonic()
    remaining = iter(tickers)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            for symbol in remaining:
                pending[pool.submit(worker, symbol)] = symbol
                if len(pending) == workers:
                    break
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    symbol = pending.pop(future)
                    try:
                        outcome = future.result()
                        if outcome == "published":
                            published += 1
                        elif outcome == "already_published":
                            skipped += 1
                        else:
                            raise RuntimeError("V7 worker returned an unknown result")
                    except BaseException as exc:
                        failed += 1
                        first_failure = first_failure or (symbol, exc)
                    if first_failure is None:
                        try:
                            successor = next(remaining)
                        except StopIteration:
                            pass
                        else:
                            pending[pool.submit(worker, successor)] = successor
                if monotonic() - last_report >= 10 or not pending or failed:
                    queued = len(tickers) - published - skipped - failed - len(pending)
                    print(f"V7 coverage: published={published} skipped={skipped} "
                          f"failed={failed} active={len(pending)} queued={queued}",
                          flush=True)
                    last_report = monotonic()
    finally:
        for client in opened:
            client.close()
    if first_failure is not None:
        symbol, exc = first_failure
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                      for frame in reversed(frames)
                      if Path(frame.filename).name in {
                          "v7_interval_derivation.py",
                          "v7_interval_publication.py"}),
                     "external_dependency")
        raise V7IntervalCampaignFailure(
            f"V7 publication stopped at {symbol}: {type(exc).__name__} "
            f"at {stage}; rerun verifies prior coverage") from None
    print(f"Verified {len(tickers)} ticker-days; "
          f"{monotonic() - started:.1f}s wall time.", flush=True)
    return {"published": published, "skipped": skipped, "failed": failed,
            "tickers": len(tickers)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default=DEFAULT_DAY)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--ticker", default="", help="one certified candidate")
    parser.add_argument("--max-tickers", type=int, default=0,
                        help="bounded first N certified candidates; 0 means all")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-v7-interval-publication", action="store_true")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
        if not 1 <= args.workers <= 16:
            raise ValueError("V7 workers must be within 1-16")
        if args.max_tickers < 0:
            raise ValueError("V7 max-tickers cannot be negative")
        if args.ticker and args.max_tickers:
            raise ValueError("Choose either ticker or max-tickers")
        if args.ticker and (not args.ticker.isascii() or not args.ticker.isupper()
                            or not args.ticker.replace(".", "").isalnum()):
            raise ValueError("Ticker must be one uppercase symbol")
    except ValueError as exc:
        parser.error(str(exc))
    if not args.apply:
        scope = args.ticker or (f"first {args.max_tickers} certified candidates"
                                if args.max_tickers else "all certified candidate tickers")
        print(f"DRY RUN: {day}, {scope}, {args.workers} bounded workers; "
              "no connection or write.")
        print("Apply on DESKTOP-SAAI85T with --apply "
              "--confirm-v7-interval-publication.")
        return 0
    if not args.confirm_v7_interval_publication:
        parser.error("--apply requires --confirm-v7-interval-publication")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: V7 publication requires the managed workstation.",
              file=sys.stderr)
        return 1
    try:
        publish_session(session_date=day, build_id=args.build_id,
                        ticker=args.ticker, max_tickers=args.max_tickers,
                        workers=args.workers)
    except Exception as exc:
        # Never echo driver errors: they may contain SQL or credentials.
        detail = str(exc) if isinstance(exc, V7IntervalCampaignFailure) else type(exc).__name__
        print(f"V7 publication stopped: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
