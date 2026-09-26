"""Publish reusable Strategy 1 causal entry evidence into normalized arte tables.

Dry-run is nonconnecting. --apply requires the managed workstation and exact
producer grants. Each completed ticker is coverage-sealed after child readback;
Ctrl+C drains admitted work and a rerun verifies existing coverage.
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

from pipelines.strategy_one.entry_evidence_derivation import (
    derive_unit_sync, publication_scope,
)
from pipelines.strategy_one.entry_evidence_publication import (
    EntryReadbackMismatch, publish_unit,
)
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_one_candidate_producer import (
    PRINCIPAL, WORKSTATION_IPV4, _GRANTS, _credential, _grant_set,
)
from scripts.clickhouse.publish_strategy_one_candidates import (
    DEFAULT_DAY, FULL_SESSION_BOUNDARY_MS, _certified_plan,
)
from src.backend.backtest_liquidity_price import certify_price_level_plan
from src.backend.backtest_market_data import (
    project_market_day_plan, readonly_clickhouse_client,
)
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_strategy_one_activation import load_strategy_one_activations
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.backtest_strategy_one_entry_store import certify_entry_evidence_plan
from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.structural_v7_seed import certified_seed_plan
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST
from src.trading_runtime.strategy_one_entry_evidence_schema import verify_tables


class EntryCampaignFailure(RuntimeError):
    """Secret-free campaign failure suitable for operator output."""


def _writer(password: str) -> ClickHouseHttpClient:
    # V7 fitting can outlive an idle socket. Never retry an uncertain insert
    # with its original UUID; each attempt is independently coverage-sealed.
    return ClickHouseHttpClient(
        f"http://{WORKSTATION_IPV4}:18123", PRINCIPAL, password,
        timeout_seconds=60, persistent=False)


def _plans(*, session_date: str, build_id: str):
    started = monotonic()
    market = _certified_plan(session_date=session_date, build_id=build_id)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        verify_tables(reader)
        candidates = certify_candidate_plan(
            market, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=FULL_SESSION_BOUNDARY_MS, client=reader)
        tickers = strategy_one_v7_tickers(candidates.prepared)
        if not tickers:
            raise RuntimeError("No certified Strategy 1 candidate ticker requires entry evidence")
        selected_market = project_market_day_plan(market, tickers)
        prices = certify_price_level_plan(selected_market, reader)
        activations = load_strategy_one_activations(
            market, candidates, client=reader)
        pivots = certify_pivot_plan(
            market, session_date=session_date,
            candidate_tickers=tickers, client=reader)
        seeds = certified_seed_plan(selected_market, reader)
        hod = certify_hod_plan(market, candidates, seeds, client=reader)
    print(f"Certified {len(tickers)} candidate ticker-days and "
          f"{sum(len(row.boundary_ms) for row in candidates.prepared)} "
          f"boundaries in {monotonic() - started:.1f}s.", flush=True)
    return market, candidates, activations, pivots, hod, seeds, prices, tickers


def publish_session(*, session_date: str, build_id: str,
                    workers: int, ticker: str = "") -> dict[str, int]:
    started = monotonic()
    print(f"Certifying Strategy 1 source: {session_date}, full session...",
          flush=True)
    market, candidates, activations, pivots, hod, seeds, prices, tickers = _plans(
        session_date=session_date, build_id=build_id)
    if ticker:
        if ticker not in tickers:
            raise ValueError("Requested ticker has no certified Strategy 1 candidates")
        tickers = (ticker,)
    password = _credential(account_exists=True)
    with closing(_writer(password)) as checked:
        if (checked.execute("SELECT currentUser()").strip() != PRINCIPAL
                or _grant_set(checked) != _GRANTS):
            raise RuntimeError("Strategy 1 entry producer lacks exact derived-table grants")
    state = local()
    opened = []
    opened_lock = Lock()

    def worker(symbol: str) -> str:
        writer = getattr(state, "writer", None)
        if writer is None:
            writer = _writer(password)
            with opened_lock:
                opened.append(writer)
            state.writer = writer
        scope = publication_scope(
            market, candidates, activations, pivots, hod, seeds,
            ticker=symbol)
        return publish_unit(writer, scope, derive=lambda: derive_unit_sync(
            scope, market, candidates, activations, pivots, hod, seeds,
            prices, client_factory=lambda: v3_client(
                "read", market_stream=True, persistent=False)))

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
                        result = future.result()
                        if result == "published":
                            published += 1
                        elif result == "skipped":
                            skipped += 1
                        else:
                            raise RuntimeError("Strategy 1 entry worker returned unknown state")
                    except BaseException as exc:
                        failed += 1
                        first_failure = first_failure or (symbol, exc)
                    if first_failure is None:
                        try:
                            next_symbol = next(remaining)
                        except StopIteration:
                            pass
                        else:
                            pending[pool.submit(worker, next_symbol)] = next_symbol
                if monotonic() - last_report >= 10 or not pending or failed:
                    queued = len(tickers) - published - skipped - failed - len(pending)
                    print(f"Entry evidence: published={published} skipped={skipped} "
                          f"failed={failed} active={len(pending)} queued={queued}",
                          flush=True)
                    last_report = monotonic()
    finally:
        for writer in opened:
            writer.close()
    if first_failure is not None:
        symbol, exc = first_failure
        stage = next((f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                      for frame in reversed(traceback.extract_tb(exc.__traceback__))
                      if Path(frame.filename).is_relative_to(REPO_ROOT)),
                     "external_dependency")
        detail = f" detail={exc}" if isinstance(exc, EntryReadbackMismatch) else ""
        raise EntryCampaignFailure(
            f"Entry publication stopped at {symbol}: {type(exc).__name__} "
            f"at {stage}{detail}; rerun verifies completed coverage")
    if not ticker:
        with closing(readonly_clickhouse_client(
                market_stream=True, v3_read_principal=True)) as reader:
            sealed = certify_entry_evidence_plan(
                market, candidates, activations, pivots, hod, seeds,
                client=reader)
        if len(sealed.coverage) != len(tickers):
            raise RuntimeError("Strategy 1 entry publication omitted a ticker")
        print(f"Read-only session seal: {sealed.token}.", flush=True)
    print(f"Entry evidence complete: {len(tickers)} ticker-days, "
          f"{monotonic() - started:.1f}s wall.", flush=True)
    return {"published": published, "skipped": skipped,
            "failed": failed, "tickers": len(tickers)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default=DEFAULT_DAY)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--ticker", default="", help="one-ticker pilot; default all candidates")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-entry-publication", action="store_true")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
        ticker = args.ticker.strip().upper()
        if ticker and (not ticker.isascii() or not ticker.isalnum()
                       or len(ticker) > 16):
            raise ValueError("Ticker must be one ASCII symbol")
        if not 1 <= args.workers <= 16:
            raise ValueError("Workers must be within 1-16")
    except ValueError as exc:
        parser.error(str(exc))
    if not args.apply:
        print(f"DRY RUN: {day}, {ticker or 'all certified candidates'}, "
              f"{args.workers} bounded workers; no connection or write.")
        print("Apply on DESKTOP-SAAI85T with --apply "
              "--confirm-entry-publication; covered ticker-days verify and skip.")
        return 0
    if not args.confirm_entry_publication:
        parser.error("--apply requires --confirm-entry-publication")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: entry publication requires the managed workstation.",
              file=sys.stderr)
        return 1
    try:
        publish_session(session_date=day, build_id=args.build_id,
                        workers=args.workers, ticker=ticker)
    except KeyboardInterrupt:
        print("Interrupted: admitted entry workers drained; rerun safely.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        detail = str(exc) if isinstance(exc, EntryCampaignFailure) else (
            f"{type(exc).__name__} at " + next((
                f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                for frame in reversed(traceback.extract_tb(exc.__traceback__))
                if Path(frame.filename).is_relative_to(REPO_ROOT)),
                "external_dependency"))
        print(f"Entry publication stopped: {detail}; coverage remains "
              "restart-safe.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
