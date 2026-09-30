"""Publish one-time normalized Strategy 1 late-HOD candidate context.

The producer reads only certified arte bars and V7 seeds. It does not write
bars, indicators, liquidity, structural levels, journals, or local files.
Covered ticker-days verify and skip on rerun; admitted workers drain on Ctrl+C.
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

from pipelines.strategy_one.hod_publication import (
    HodReadbackMismatch, _child, _derive, _scope, _verify_existing,
    publish_unit,
)
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
from src.backend.backtest_input_scope import certify_scoped_candidate_plan as certify_candidate_plan
from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.structural_v7_seed import certified_seed_plan
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST
from src.trading_runtime.strategy_one_hod_schema import verify_tables


class HodCampaignFailure(RuntimeError):
    """Only locally constructed, secret-free diagnostics reach the console."""


def _writer(password: str) -> ClickHouseHttpClient:
    # V7 fitting can leave a worker's writer socket idle beyond keepalive.
    # Never retry an uncertain INSERT under its original attempt UUID.
    return ClickHouseHttpClient(
        f"http://{WORKSTATION_IPV4}:18123", PRINCIPAL, password,
        timeout_seconds=60, persistent=False)


def _plans(*, session_date: str, build_id: str):
    market = _certified_plan(session_date=session_date, build_id=build_id)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        verify_tables(reader)
        candidates = certify_candidate_plan(
            market, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=FULL_SESSION_BOUNDARY_MS, client=reader)
        tickers = strategy_one_v7_tickers(candidates.prepared)
        if not tickers:
            raise RuntimeError("No candidate ticker requires HOD derivation")
        scoped = project_market_day_plan(market, tickers)
        seeds = certified_seed_plan(scoped, reader)
    return market, candidates, seeds, tickers


def verify_session(*, session_date: str, build_id: str):
    market, candidates, seeds, tickers = _plans(
        session_date=session_date, build_id=build_id)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        sealed = certify_hod_plan(market, candidates, seeds, client=reader)
    if len(sealed.contexts) != len(tickers):
        raise RuntimeError("HOD product omits a candidate ticker")
    print(f"Certified {len(tickers)} candidate ticker-days; "
          f"token {sealed.token}.", flush=True)
    return sealed


def verify_recomputed_ticker(*, session_date: str, build_id: str,
                             ticker: str = "") -> None:
    """Compare a sealed ticker with today's causal derivation; write nothing."""
    market, candidates, seeds, tickers = _plans(
        session_date=session_date, build_id=build_id)
    ticker = ticker or tickers[0]
    if ticker not in tickers:
        raise ValueError("Requested ticker has no certified HOD candidates")
    scope = _scope(market, candidates, seeds, ticker=ticker)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as coverage_reader, \
            closing(v3_client("read", market_stream=True,
                              persistent=False)) as bars_reader, \
            closing(v3_client("read", market_stream=True,
                              persistent=False)) as seconds_reader:
        attempt = _verify_existing(coverage_reader, scope)
        if attempt is None:
            raise RuntimeError("Ticker has no sealed HOD context to compare")
        saved = _child(coverage_reader, scope, attempt)
        recomputed = _derive(bars_reader, seconds_reader,
                             market, seeds, scope)
    if saved != recomputed:
        first = next((index for index, (left, right) in enumerate(
            zip(saved, recomputed, strict=True)) if left != right), None)
        raise RuntimeError(
            f"Sealed HOD context differs for {ticker}: first_boundary_index={first}")
    print(f"HOD recomputation MATCH: {session_date} {ticker}, "
          f"{len(saved)} certified boundaries; writes=0", flush=True)


def publish_session(*, session_date: str, build_id: str,
                    workers: int) -> dict[str, int]:
    started = monotonic()
    print(f"Certifying Strategy 1 inputs: {session_date}, "
          "full session, all tradable tickers...", flush=True)
    market, candidates, seeds, tickers = _plans(
        session_date=session_date, build_id=build_id)
    count = sum(len(item.boundary_ms) for item in candidates.prepared)
    print(f"Deriving HOD context for {count} certified boundaries "
          f"on {len(tickers)} candidate tickers; {workers} workers.", flush=True)
    password = _credential(account_exists=True)
    with closing(_writer(password)) as checked:
        if (checked.execute("SELECT currentUser()").strip() != PRINCIPAL
                or _grant_set(checked) != _GRANTS):
            raise RuntimeError("Strategy 1 producer lacks exact derived-table grants")
    retried = 0
    retry_lock = Lock()

    def record_source_retry() -> None:
        nonlocal retried
        with retry_lock:
            retried += 1

    state = local()
    opened = []
    opened_lock = Lock()

    def worker(ticker: str) -> str:
        clients = getattr(state, "clients", None)
        if clients is None:
            # Each ticker can spend seconds fitting V7 between reads. The
            # workstation closes idle HTTP sockets; fresh SELECT connections
            # avoid stale keepalive without retrying uncertain INSERTs.
            clients = (_writer(password),
                       v3_client("read", market_stream=True, persistent=False),
                       v3_client("read", market_stream=True, persistent=False))
            with opened_lock:
                opened.extend(clients)
            state.clients = clients
        return publish_unit(
            *clients, market, candidates, seeds, ticker=ticker,
            on_source_retry=record_source_retry)

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
                        outcome = future.result()
                        if outcome == "published":
                            published += 1
                        elif outcome == "skipped":
                            skipped += 1
                        else:
                            raise RuntimeError("HOD worker returned unknown status")
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
                    print(f"HOD coverage: published={published} skipped={skipped} "
                          f"retried={retried} failed={failed} "
                          f"active={len(pending)} queued={queued}",
                          flush=True)
                    last_report = monotonic()
    finally:
        for client in opened:
            client.close()
    if first_failure is not None:
        ticker, exc = first_failure
        frames = traceback.extract_tb(exc.__traceback__)
        stage = next((f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                      for frame in reversed(frames)
                      if Path(frame.filename).is_relative_to(REPO_ROOT)),
                     "external_dependency")
        detail = f" detail={exc}" if isinstance(exc, HodReadbackMismatch) else ""
        raise HodCampaignFailure(
            f"HOD publication stopped at {ticker}: {type(exc).__name__} "
            f"at {stage}{detail}; rerun verifies completed coverage")
    sealed = verify_session(session_date=session_date, build_id=market.build_id)
    print(f"HOD publication complete: {len(sealed.contexts)} ticker-days, "
          f"{count} candidate boundaries, {monotonic() - started:.1f}s wall.",
          flush=True)
    return {"published": published, "skipped": skipped,
            "retried": retried, "failed": failed,
            "tickers": len(tickers), "candidates": count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default=DEFAULT_DAY)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--recompute-ticker", default="",
                        help="read-only comparison against sealed HOD rows")
    parser.add_argument("--recompute-first", action="store_true",
                        help="read-only comparison of the first certified ticker")
    parser.add_argument("--confirm-hod-publication", action="store_true")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
        if not 1 <= args.workers <= 16:
            raise ValueError("HOD workers must be within 1-16")
        if sum(bool(value) for value in (
                args.apply, args.verify_only, args.recompute_ticker,
                args.recompute_first)) > 1:
            raise ValueError("HOD run, verification, and recomputation modes are exclusive")
        if args.recompute_ticker and (not args.recompute_ticker.isascii()
                or not args.recompute_ticker.isalnum()
                or args.recompute_ticker != args.recompute_ticker.upper()):
            raise ValueError("Recompute ticker must be an uppercase ASCII symbol")
    except ValueError as exc:
        parser.error(str(exc))
    if not any((args.apply, args.verify_only, args.recompute_ticker,
                args.recompute_first)):
        print(f"DRY RUN: {day}, certified candidate ticker-days, "
              f"{args.workers} bounded workers; no connection or write.")
        print("Apply on DESKTOP-SAAI85T with --apply "
              "--confirm-hod-publication; reruns verify and skip coverage.")
        return 0
    if args.apply and not args.confirm_hod_publication:
        parser.error("--apply requires --confirm-hod-publication")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: HOD publication requires the managed workstation.",
              file=sys.stderr)
        return 1
    try:
        if args.verify_only:
            verify_session(session_date=day, build_id=args.build_id)
        elif args.recompute_ticker or args.recompute_first:
            verify_recomputed_ticker(
                session_date=day, build_id=args.build_id,
                ticker=args.recompute_ticker)
        else:
            publish_session(session_date=day, build_id=args.build_id,
                            workers=args.workers)
    except KeyboardInterrupt:
        print("Interrupted: admitted HOD workers drained; rerun safely.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        safe_seed_gap = (type(exc) is ValueError and str(exc).startswith(
            "V7 prior coverage is missing or duplicated: "))
        detail = (str(exc) if isinstance(exc, HodCampaignFailure) or safe_seed_gap
                  else f"{type(exc).__name__} at " + next((
                      f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                      for frame in reversed(traceback.extract_tb(exc.__traceback__))
                      if Path(frame.filename).is_relative_to(REPO_ROOT)),
                      "external_dependency"))
        print(f"HOD campaign stopped: {detail}; covered tickers remain "
              "restart-safe. Inspect private diagnostics.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
