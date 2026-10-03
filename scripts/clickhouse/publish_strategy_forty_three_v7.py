"""Prepare missing causal V7 intervals for Strategy 43's complete squeeze scope.

Default is certification only. --apply writes only missing V7 derivative units
using the existing narrow producer; it never rebuilds bars, indicators,
liquidity, prior checkpoints or existing sealed intervals.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
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

from src.backend.backtest_market_data import certified_market_plan_from_arte, project_market_day_plan, readonly_clickhouse_client, _literal
from src.backend.fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
from src.backend.structural_v7_seed import certified_seed_plan
from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
from src.trading_runtime.strategy_one_v7_interval_schema import COVERAGE_TABLE, verify_tables
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from pipelines.strategy_one.v7_interval_derivation import derive_ticker_day
from pipelines.strategy_one.v7_interval_publication import publish_unit
from scripts.clickhouse.publish_strategy_one_v7_intervals import _writer
from scripts.clickhouse.provision_strategy_one_candidate_producer import PRINCIPAL, _credential, _grant_set, _GRANTS

BUILD = "1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5"


def run(*, session, build, apply, workers):
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Strategy 43 V7 preparation runs on the managed workstation")
    credential = Path("D:/TradingML/secrets/backtest_v3_read.env")
    if not credential.is_file():
        raise RuntimeError("Dedicated Backtest reader credential is unavailable")
    os.environ.setdefault("BACKTEST_V3_READ_CREDENTIAL_FILE", str(credential))
    runtime_root = Path("D:/TradingML/runtimes")
    if not runtime_root.is_dir():
        raise RuntimeError("Required workstation runtime root is unavailable")
    print(f"Strategy 43 V7 | {session} premarket | {'APPLY missing units' if apply else 'CHECK only'}", flush=True)
    market = certified_market_plan_from_arte(sessions=(session,), tickers=(),
        configuration={"market_day_build_id": build, "strategy": {"execution_interval": "100ms"}})
    market = project_market_day_plan(market, tuple(t for t in market.tickers if t != "LGHL"))
    with closing(readonly_clickhouse_client(market_stream=True, v3_read_principal=True)) as reader:
        stream, activation = canonical_stream_activation()
        scan = load_first_squeeze_occurrences(market, stream=stream, activation=activation,
            through_boundary_ms=19_800_000, client=reader)
        tickers = tuple(sorted({row["ticker"] for row in scan["occurrences"] if 1 <= row["last_price"] <= 50}))
        if not tickers:
            raise RuntimeError("Strategy 43 squeeze scope is empty")
        market = project_market_day_plan(market, tickers)
        seeds = certified_seed_plan(market, reader)
        verify_tables(reader)
        inventory = [json.loads(line)["ticker"] for line in reader.execute(
            f"SELECT ticker FROM {COVERAGE_TABLE} WHERE source_build_id={_literal(build)} "
            f"AND session_date=toDate({_literal(session)}) FORMAT JSONEachRow").splitlines() if line.strip()]
        if len(set(inventory)) != len(inventory):
            raise RuntimeError("Existing V7 derivative seals are ambiguous")
        covered = tuple(sorted(set(tickers) & set(inventory)))
        missing = tuple(sorted(set(tickers) - set(covered)))
        if covered:
            certify_v7_interval_plan(market, seeds, session_date=session,
                candidate_tickers=covered, client=reader)
    print(f"Certified {len(tickers)} candidates: {len(covered)} existing, {len(missing)} missing.", flush=True)
    if not apply:
        print("No source rows changed. Run with --apply to publish missing causal derivatives.", flush=True)
        return
    password = _credential(account_exists=True)
    with closing(_writer(password)) as writer:
        if writer.execute("SELECT currentUser()").strip() != PRINCIPAL or _grant_set(writer) != _GRANTS:
            raise RuntimeError("Existing V7 producer does not have its exact narrow authority")
    root = runtime_root / "strategy43" / "v7-preparation" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    started, completed, failed, shown = monotonic(), [], [], 0.
    with closing(open_workstation_keeper_session()) as session_owner:
        owner = session_owner.client
        lock = f"/trading/ownership/v1/strategy43_v7/{build}/{session}"
        owner.create(lock, str(root.name).encode(), ephemeral=True, makepath=True)
        stat = owner.exists(lock)
        if stat is None or not stat.ephemeralOwner:
            raise RuntimeError("Strategy 43 derivative campaign lacks ephemeral ownership")
        def guard():
            if not owner.connected:
                raise RuntimeError("Strategy 43 V7 campaign lost Keeper ownership")
            current = owner.exists(lock)
            if current is None or (current.ephemeralOwner, current.czxid) != (stat.ephemeralOwner, stat.czxid):
                raise RuntimeError("Strategy 43 V7 campaign lost its exact ownership epoch")
        def unit(ticker):
            guard()
            with closing(readonly_clickhouse_client(market_stream=True, v3_read_principal=True)) as reader, closing(_writer(password)) as writer:
                item = derive_ticker_day(market=market, seeds=seeds,
                    session_date=session, ticker=ticker, reader=reader)
                guard()
                class GuardedWriter:
                    def execute(self, statement):
                        guard()
                        return writer.execute(statement)
                outcome = publish_unit(GuardedWriter(), reader, item)
                guard()
                return dict(ticker=ticker, outcome=outcome,
                    clocks=len(item.valid_seconds), intervals=len(item.intervals))
        try:
            # No per-ticker agents or unbounded market-data buffers. Each
            # worker owns at most one source unit and its native derivative.
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {pool.submit(unit, ticker): ticker for ticker in missing}
                for future in as_completed(futures):
                    ticker = futures[future]
                    try:
                        completed.append(future.result())
                    except Exception as error:
                        failed.append(dict(ticker=ticker, error_type=type(error).__name__))
                    done = len(completed) + len(failed)
                    now = monotonic()
                    if now - shown >= 1 or done == len(missing):
                        pending = len(missing) - done
                        print(f"V7 {done}/{len(missing)} | active <= {min(workers, pending)} | queued >= {max(0, pending-workers)} | published {len(completed)} | failed {len(failed)} | existing {len(covered)} | elapsed {now-started:.1f}s", flush=True)
                        shown = now
                    receipt = dict(session=session, build=build, source_token=market.token,
                        seed_token=seeds.token, existing=list(covered), completed=completed,
                        failed=failed, pending=sorted(set(missing)-{row['ticker'] for row in completed+failed}),
                        updated_at=datetime.now(timezone.utc).isoformat())
                    temporary = root / "progress.tmp"
                    temporary.write_text(json.dumps(receipt, sort_keys=True, indent=2), encoding="utf-8")
                    temporary.replace(root / "progress.json")
            finally:
                # Finish at most the active units and cancel queued units on
                # interruption. Published seals remain independently resumable.
                pool.shutdown(wait=True, cancel_futures=True)
            if failed:
                raise RuntimeError(f"V7 source campaign failed {len(failed)} units; retained exact successful seals and failure receipt")
            guard()
            with closing(readonly_clickhouse_client(market_stream=True, v3_read_principal=True)) as reader:
                certificate = certify_v7_interval_plan(market, seeds, session_date=session,
                    candidate_tickers=tickers, client=reader)
            guard()
            (root / "certificate.json").write_text(json.dumps(dict(token=certificate.token,
                ticker_count=len(certificate.coverage), elapsed_seconds=monotonic()-started), indent=2), encoding="utf-8")
            print(f"Certified all {len(tickers)} V7 units; receipt {root}", flush=True)
        finally:
            if owner.connected:
                current = owner.exists(lock)
                if current is not None and (current.ephemeralOwner, current.czxid) == (stat.ephemeralOwner, stat.czxid):
                    owner.delete(lock, version=current.version)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default="2026-09-03")
    parser.add_argument("--build", default=BUILD)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("Workers must be between 1 and 16")
    try:
        run(session=args.session, build=args.build, apply=args.apply, workers=args.workers)
    finally:
        MARKET_CERTIFICATE_KEEPER_POOL.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Strategy 43 V7 FAILED: {type(error).__name__}: {error}" if type(error) is RuntimeError
              else f"Strategy 43 V7 FAILED: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
