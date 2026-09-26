"""Publish normalized Strategy 1 pivot intervals from certified ARTE 1s bars.

Only candidate tickers are derived. This is a separate producer campaign,
never a Backtest fallback. An interrupted attempt is invisible without its
coverage row. Reruns verify and skip complete ticker-days.
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
from threading import Lock, local
from time import monotonic

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from pipelines.strategy_one.pivot_publication import publish_unit
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_one_candidate_producer import (
    PRINCIPAL, WORKSTATION_IPV4, _GRANTS, _credential, _grant_set,
)
from scripts.clickhouse.publish_strategy_one_candidates import (
    DEFAULT_DAY, FULL_SESSION_BOUNDARY_MS, _certified_plan,
)
from src.backend.backtest_market_data import readonly_clickhouse_client
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST
from src.trading_runtime.strategy_one_pivot_schema import verify_tables


def _writer(password: str) -> ClickHouseHttpClient:
    return ClickHouseHttpClient(f"http://{WORKSTATION_IPV4}:18123",
                                PRINCIPAL, password, timeout_seconds=60,
                                persistent=True)


def publish_session(*, session_date: str, build_id: str,
                    workers: int) -> dict[str, int]:
    """Publish only certified candidate tickers, bounded by worker capacity."""
    started = monotonic()
    plan = _certified_plan(session_date=session_date, build_id=build_id)
    print(f"Certifying candidate coverage: {session_date}, "
          f"{len(plan.tickers)} tradable tickers...", flush=True)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        verify_tables(reader)
        candidates = certify_candidate_plan(
            plan, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=FULL_SESSION_BOUNDARY_MS, client=reader)
    tickers = strategy_one_v7_tickers(candidates.prepared)
    if not tickers:
        raise RuntimeError("No certified candidate ticker requires pivot derivation")
    print(f"Deriving confirmed pivots from pinned completed 1s bars: "
          f"{len(tickers)} candidate tickers, {workers} workers...", flush=True)
    password = _credential(account_exists=True)
    with closing(_writer(password)) as checked_writer:
        if (checked_writer.execute("SELECT currentUser()").strip() != PRINCIPAL
                or _grant_set(checked_writer) != _GRANTS):
            raise RuntimeError("Strategy 1 product writer lacks exact authority")
    state = local()
    opened = []
    opened_lock = Lock()

    def worker(ticker: str) -> str:
        pair = getattr(state, "clients", None)
        if pair is None:
            pair = (_writer(password), readonly_clickhouse_client(
                market_stream=True, v3_read_principal=True))
            with opened_lock:
                opened.extend(pair)
            state.clients = pair
        writer, reader = pair
        return publish_unit(writer, reader, plan,
                            session_date=session_date, ticker=ticker)

    published = skipped = failed = 0
    first_failure: tuple[str, BaseException] | None = None
    last_report = monotonic()
    remaining = iter(tickers)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            for ticker in remaining:
                pending[pool.submit(worker, ticker)] = ticker
                if len(pending) == workers:
                    break
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    ticker = pending.pop(future)
                    try:
                        result = future.result()
                        if result == "published":
                            published += 1
                        elif result == "skipped":
                            skipped += 1
                        else:
                            raise RuntimeError("Pivot worker returned an unknown result")
                    except BaseException as exc:
                        failed += 1
                        first_failure = first_failure or (ticker, exc)
                    if first_failure is None:
                        try:
                            successor = next(remaining)
                        except StopIteration:
                            pass
                        else:
                            pending[pool.submit(worker, successor)] = successor
                if monotonic() - last_report >= 10 or not pending or failed:
                    queued = len(tickers) - published - skipped - failed - len(pending)
                    print(f"Pivot coverage: published={published} skipped={skipped} "
                          f"failed={failed} active={len(pending)} queued={queued}",
                          flush=True)
                    last_report = monotonic()
    finally:
        for client in opened:
            client.close()
    if first_failure is not None:
        ticker, exc = first_failure
        raise RuntimeError(f"Pivot publication stopped at {ticker}: "
                           f"{type(exc).__name__}; rerun verifies prior coverage")
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
        sealed = certify_pivot_plan(plan, session_date=session_date,
                                   candidate_tickers=tickers, client=reader)
    elapsed = monotonic() - started
    print(f"Certified {len(sealed.coverage)} candidate ticker-days; "
          f"token {sealed.token}; {elapsed:.1f}s wall time.", flush=True)
    return {"published": published, "skipped": skipped, "failed": failed,
            "tickers": len(tickers)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default=DEFAULT_DAY)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-pivot-publication", action="store_true")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
        if not 1 <= args.workers <= 16:
            raise ValueError("Pivot workers must be within 1-16")
    except ValueError as exc:
        parser.error(str(exc))
    if not args.apply:
        print(f"DRY RUN: {day}, certified candidate tickers, "
              f"{args.workers} bounded workers; no connection or write.")
        print("Apply on DESKTOP-SAAI85T with --apply "
              "--confirm-pivot-publication.")
        return 0
    if not args.confirm_pivot_publication:
        parser.error("--apply requires --confirm-pivot-publication")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: pivot publication requires the managed workstation.",
              file=sys.stderr)
        return 1
    try:
        publish_session(session_date=day, build_id=args.build_id,
                        workers=args.workers)
    except KeyboardInterrupt:
        print("Interrupted: admitted ticker workers drained; rerun safely.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        # ClickHouse exceptions may embed credentials or SQL.
        print(f"Pivot campaign stopped: {type(exc).__name__}; "
              "completed ticker coverage remains restart-safe. "
              "Inspect private diagnostics.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
