"""Publish certified full-universe Strategy 1 candidate masks from arte inputs.

This is a producer campaign, never part of Backtest launch. Re-running the
same session/build/scan verifies and skips completed ticker-days. Child rows
are inserted before each ticker's coverage row; interrupted attempts are not
visible to the Backtest reader.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import platform
import re
import sys
import traceback
from threading import Lock, local
from time import monotonic

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from pipelines.strategy_one.candidate_producer import (
    candidate_scan_authority, publish_unit,
)
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_one_candidate_producer import (
    PRINCIPAL, WORKSTATION_IPV4, _GRANTS, _credential, _grant_set,
)
from scripts.clickhouse.provision_fixed_backtest_v3_principals import _secret_path
from scripts.clickhouse.provision_trading_journal import _restrict_secret_file
from src.backend.backtest_market_data import (
    certified_market_plan_from_arte, readonly_clickhouse_client,
)
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.backtest_strategy_one_preparation import prepare_strategy_one_session
from src.backend.fixed_bar_signal import (
    canonical_stream_activation, load_first_squeeze_occurrences,
)
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST, verify_tables


DEFAULT_DAY = "2026-08-18"
FULL_SESSION_BOUNDARY_MS = 57_600_000


def _bootstrap_reader_credential() -> None:
    """Use the workstation's existing private V3 reader file, never copy it."""
    keys = ("BACKTEST_V3_READ_CREDENTIAL_FILE",
            "BACKTEST_V3_READ_CLICKHOUSE_URL",
            "BACKTEST_V3_READ_CLICKHOUSE_USER",
            "BACKTEST_V3_READ_CLICKHOUSE_PASSWORD")
    if any(os.environ.get(key) for key in keys):
        return
    path = _secret_path("read")
    if not path.is_file():
        raise RuntimeError("Private V3 read credential is unavailable")
    _restrict_secret_file(path)
    os.environ[keys[0]] = str(path)


def _writer_client(password: str):
    return ClickHouseHttpClient(
        f"http://{WORKSTATION_IPV4}:18123", PRINCIPAL, password,
        timeout_seconds=60, persistent=True)


def _certified_plan(*, session_date: str, build_id: str):
    configuration = {
        "strategy": {"strategy_number": 1, "execution_interval": "100ms"},
        **({"market_day_build_id": build_id} if build_id else {}),
    }
    _bootstrap_reader_credential()
    return certified_market_plan_from_arte(
        sessions=(session_date,), tickers=(), configuration=configuration)


def verify_session(*, session_date: str, through_boundary_ms: int,
                   build_id: str, plan=None):
    print(f"Certifying arte source and candidate coverage: {session_date}...",
          flush=True)
    if plan is None:
        plan = _certified_plan(session_date=session_date, build_id=build_id)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as reader:
        verify_tables(reader)
        sealed = certify_candidate_plan(
            plan, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=through_boundary_ms, client=reader)
    if len(sealed.coverage) != len(plan.tickers):
        raise RuntimeError("Candidate publication lacks full ticker coverage")
    print(f"Certified {len(plan.tickers)} ticker-days; token {sealed.token}.",
          flush=True)
    return sealed


def publish_session(*, session_date: str, through_boundary_ms: int,
                    build_id: str, read_workers: int, write_workers: int) -> dict[str, int]:
    """Full-population campaign with bounded ticker publication and final seal."""
    started = monotonic()
    print(f"Certifying arte 100ms source: {session_date}, all planned tickers...",
          flush=True)
    plan = _certified_plan(session_date=session_date, build_id=build_id)
    stream, activation = canonical_stream_activation()
    print(f"Certified build {plan.build_id[:12]}: {len(plan.tickers)} tickers; "
          "scanning completed-bar squeeze episodes...", flush=True)
    with closing(readonly_clickhouse_client(
            market_stream=True, v3_read_principal=True)) as scanner:
        verify_tables(scanner)
        scan = load_first_squeeze_occurrences(
            plan, stream=stream, activation=activation,
            through_boundary_ms=through_boundary_ms, client=scanner)
        print(f"Certified {len(scan['occurrences'])} episode starts; "
              "preparing columnar candidates...", flush=True)
        prepared = prepare_strategy_one_session(
            plan, session_date=session_date,
            through_boundary_ms=through_boundary_ms,
            stream=stream, activation=activation,
            scan_client=scanner,
            client_factory=lambda: readonly_clickhouse_client(
                market_stream=True, v3_read_principal=True),
            max_workers=read_workers, certified_scan=scan)
    authority = candidate_scan_authority(
        plan, through_boundary_ms=through_boundary_ms)
    if authority.query_sha256 != scan["authority"]["query_sha256"]:
        raise RuntimeError("Prepared candidates differ from certified squeeze scan")
    by_ticker = {item.ticker: item for item in prepared}
    if len(by_ticker) != len(prepared) or not set(by_ticker) <= set(plan.tickers):
        raise RuntimeError("Columnar preparation has duplicate/out-of-scope tickers")
    candidates = sum(len(item.boundary_ms) for item in prepared)
    print(f"Prepared {candidates} sparse boundaries on {len(prepared)} episode tickers; "
          f"publishing {len(plan.tickers)} ticker coverage rows...", flush=True)
    password = _credential(account_exists=True)
    with closing(_writer_client(password)) as checked_writer:
        if checked_writer.execute("SELECT currentUser()").strip() != PRINCIPAL:
            raise RuntimeError("Candidate writer identity changed")
        if _grant_set(checked_writer) != _GRANTS:
            raise RuntimeError("Candidate writer no longer has exact grants")
    state = local()
    opened = []
    opened_lock = Lock()

    def worker(ticker: str) -> str:
        client = getattr(state, "client", None)
        if client is None:
            client = _writer_client(password)
            with opened_lock:
                opened.append(client)
            state.client = client
        item = by_ticker.get(ticker)
        return publish_unit(
            client, plan, session_date=session_date, ticker=ticker,
            candidate_rule_digest=RULE_DIGEST, scan_authority=authority,
            has_episode=item is not None, prepared=item)

    completed = skipped = failed = 0
    first_failure: tuple[str, BaseException] | None = None
    last_report = monotonic()
    tickers = iter(plan.tickers)
    try:
        with ThreadPoolExecutor(max_workers=write_workers) as pool:
            pending = {}
            for ticker in tickers:
                pending[pool.submit(worker, ticker)] = ticker
                if len(pending) == write_workers:
                    break
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    ticker = pending.pop(future)
                    try:
                        outcome = future.result()
                        if outcome == "published":
                            completed += 1
                        elif outcome == "skipped":
                            skipped += 1
                        else:
                            raise RuntimeError("Candidate producer returned an unknown status")
                    except BaseException as exc:
                        failed += 1
                        first_failure = first_failure or (ticker, exc)
                    if first_failure is None:
                        try:
                            successor = next(tickers)
                        except StopIteration:
                            pass
                        else:
                            pending[pool.submit(worker, successor)] = successor
                if (monotonic() - last_report >= 10 or not pending or failed):
                    active = len(pending)
                    queued = len(plan.tickers) - completed - skipped - failed - active
                    print(f"Coverage: published={completed} skipped={skipped} "
                          f"failed={failed} active={active} queued={queued}", flush=True)
                    last_report = monotonic()
    finally:
        for client in opened:
            client.close()
    if first_failure is not None:
        ticker, exc = first_failure
        raise RuntimeError(f"Candidate publication stopped at {ticker}: "
                           f"{type(exc).__name__}; rerun verifies completed coverage")
    sealed = verify_session(session_date=session_date,
                            through_boundary_ms=through_boundary_ms,
                            build_id=plan.build_id, plan=plan)
    if len(sealed.coverage) != len(plan.tickers):
        raise RuntimeError("Candidate publication lacks full ticker coverage")
    elapsed = monotonic() - started
    print(f"Certified candidate token {sealed.token}; {len(plan.tickers)} ticker-days, "
          f"{candidates} boundaries, {elapsed:.1f}s wall time.", flush=True)
    return {"published": completed, "skipped": skipped, "failed": failed,
            "tickers": len(plan.tickers), "candidates": candidates}


def _report_failure(exc: Exception) -> None:
    frames = traceback.extract_tb(exc.__traceback__)
    stage = next((f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                  for frame in reversed(frames)
                  if Path(frame.filename).is_relative_to(REPO_ROOT)),
                 "external_dependency")
    status = getattr(exc, "status_code", None)
    match = re.search(r"\bCode:\s*(\d+)", str(exc)) if status else None
    code = f" HTTP {status}" if isinstance(status, int) else ""
    if match:
        code += f" ClickHouse code {match.group(1)}"
    print(f"Candidate campaign stopped: {type(exc).__name__} at {stage}{code}. "
          "Completed ticker coverage is restart-safe; inspect private diagnostics.",
          file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", default=DEFAULT_DAY)
    parser.add_argument("--through-boundary-ms", type=int,
                        default=FULL_SESSION_BOUNDARY_MS)
    parser.add_argument("--build-id", default="",
                        help="pin a certified market-day build if more than one exists")
    parser.add_argument("--read-workers", type=int, default=8)
    parser.add_argument("--write-workers", type=int, default=8)
    parser.add_argument("--apply", action="store_true",
                        help="publish candidate child and coverage rows")
    parser.add_argument("--verify-only", action="store_true",
                        help="read-only exact coverage audit; no candidate writes")
    parser.add_argument("--confirm-candidate-publication", action="store_true",
                        help="required second confirmation for --apply")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.session_date).isoformat()
        if not 0 < args.through_boundary_ms <= FULL_SESSION_BOUNDARY_MS or \
                args.through_boundary_ms % 100 or \
                not 1 <= args.read_workers <= 16 or \
                not 1 <= args.write_workers <= 16:
            raise ValueError("Invalid session boundary or bounded worker count")
    except ValueError as exc:
        parser.error(str(exc))
    if not args.apply:
        if args.verify_only:
            if platform.node().upper() != "DESKTOP-SAAI85T":
                print("Blocked: verification requires the managed workstation.",
                      file=sys.stderr)
                return 1
            try:
                verify_session(session_date=day,
                               through_boundary_ms=args.through_boundary_ms,
                               build_id=args.build_id)
            except Exception as exc:
                _report_failure(exc)
                return 1
            return 0
        print(f"DRY RUN: {day} through {args.through_boundary_ms}ms; "
              "all certified tickers; no connection or write.")
        print("Apply on DESKTOP-SAAI85T with --apply "
              "--confirm-candidate-publication.")
        return 0
    if not args.confirm_candidate_publication:
        parser.error("--apply requires --confirm-candidate-publication")
    if platform.node().upper() != "DESKTOP-SAAI85T":
        print("Blocked: producer campaign requires the managed workstation.",
              file=sys.stderr)
        return 1
    try:
        publish_session(
            session_date=day, through_boundary_ms=args.through_boundary_ms,
            build_id=args.build_id, read_workers=args.read_workers,
            write_workers=args.write_workers)
    except KeyboardInterrupt:
        print("Interrupted: admitted ticker workers drained; rerun safely.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        _report_failure(exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
