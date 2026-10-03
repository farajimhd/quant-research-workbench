"""Prepare and certify Strategy 43 historical facts; default is check only."""
import argparse
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sys
from time import monotonic
from uuid import uuid4, uuid5, NAMESPACE_URL

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.backend.backtest_market_data import readonly_clickhouse_client
from src.backend.backtest_strategy_forty_three_plan import certify_source_plan
from src.backend.backtest_strategy_forty_three_source import certify_history
from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from src.trading_runtime.strategy_forty_three_fact_schema import PRODUCT_DIGEST, verify_tables
from pipelines.strategy_one.strategy_forty_three_publication import PublicationSource, publish_unit
from pipelines.strategy_one.strategy_forty_three_prepare import completed_seconds
from research.mlops.clickhouse import ClickHouseHttpClient
from scripts.clickhouse.provision_strategy_forty_three_facts import (
    credential, grant_set, GRANTS, PRINCIPAL, URL,
)
from scripts.clickhouse.publish_strategy_forty_three_v7 import BUILD


def publication_sources(plan):
    result = []
    for ticker in plan.tickers:
        units = {row.stage: row for row in plan.market.units if row.ticker == ticker}
        listing = plan.listings[ticker]
        values = dict(build_id=plan.market.build_id, session_date=plan.market.sessions[0], ticker=ticker,
            bars_attempt_id=units["bars"].attempt_id, liquidity_attempt_id=units["broker_100ms"].attempt_id,
            source_market_token=plan.market.token, source_v7_token=plan.structure.token,
            source_snapshot_hash=plan.snapshot_hash, source_signal_query_hash=plan.signal_query_hash,
            listing_id=listing["listing_id"], symbol_id=listing["symbol_id"], security_id=listing["security_id"],
            conid=plan.identity.conid_for(ticker), admission_ms=plan.admissions[ticker],
            session_end_ms=plan.session_end_ms)
        digest = sha256(json.dumps(dict(product=PRODUCT_DIGEST, **values), sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()
        source = PublicationSource(attempt_id=str(uuid5(NAMESPACE_URL, "strategy43-history:" + digest)), **values)
        source.validate()
        result.append(source)
    return tuple(result)


def run(*, session, build, apply):
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Strategy 43 history preparation requires the managed workstation")
    runtime_root = Path("D:/TradingML/runtimes")
    credential_path = Path("D:/TradingML/secrets/backtest_v3_read.env")
    if not runtime_root.is_dir() or not credential_path.is_file():
        raise RuntimeError("Required workstation runtime or reader credential is unavailable")
    os.environ.setdefault("BACKTEST_V3_READ_CREDENTIAL_FILE", str(credential_path))
    print(f"Strategy 43 history | {session} premarket | {'APPLY' if apply else 'CHECK only'}", flush=True)
    started = monotonic()
    with closing(readonly_clickhouse_client(market_stream=True, v3_read_principal=True)) as reader:
        plan = certify_source_plan(session=session, build=build, reader=reader)
        sources = publication_sources(plan)
        verify_tables(reader)
        print(f"Certified {len(plan.market.tickers)} tradables, {len(sources)} squeeze candidates and causal V7 parents.", flush=True)
        if not apply:
            print("No writes. Use --apply to publish exact history units and certify the complete source.", flush=True)
            return
        password = credential(account_exists=True)
        with closing(ClickHouseHttpClient(URL, PRINCIPAL, password, persistent=True)) as writer:
            if writer.execute("SELECT currentUser()").strip() != PRINCIPAL or grant_set(writer) != GRANTS:
                raise RuntimeError("Strategy 43 history producer lacks its exact narrow grants")
            root = runtime_root / "strategy43" / "history-preparation" / uuid4().hex
            root.mkdir(parents=True, exist_ok=False)
            completed = []
            with closing(open_workstation_keeper_session()) as owner:
                for source in sources:
                    print(f"History {len(completed)}/{len(sources)} | active 1 ({source.ticker}) | queued {len(sources)-len(completed)-1} | failed 0 | elapsed {monotonic()-started:.1f}s", flush=True)
                    try:
                        seconds = completed_seconds(source=source, market=plan.market,
                            identity=plan.identity, structure=plan.structure, reader=reader)
                        receipt = publish_unit(writer, owner.client, source, seconds, catalog_reader=reader)
                    except BaseException as error:
                        (root / "failure.json").write_text(json.dumps(dict(ticker=source.ticker,
                            error_type=type(error).__name__, completed=len(completed),
                            remaining=len(sources)-len(completed)), indent=2), encoding="utf-8")
                        raise
                    completed.append(dict(ticker=source.ticker, attempt=source.attempt_id, receipt=receipt))
                    temporary = root / "progress.tmp"
                    temporary.write_text(json.dumps(dict(session=session, build=build,
                        completed=completed, pending=len(sources)-len(completed)), indent=2), encoding="utf-8")
                    temporary.replace(root / "progress.json")
            print("History published; verifying every immutable child and full source certificate.", flush=True)
            certificate = certify_history(market=plan.market, identity=plan.identity, structure=plan.structure,
                candidate_tickers=plan.tickers, snapshot_hash=plan.snapshot_hash,
                signal_query_hash=plan.signal_query_hash, session_end_ms=plan.session_end_ms, reader=reader)
            (root / "certificate.json").write_text(json.dumps(dict(token=certificate.token,
                ticker_count=len(certificate.coverage), elapsed_seconds=monotonic()-started), indent=2), encoding="utf-8")
            print(f"Certified all {len(sources)} history units; receipt {root}", flush=True)


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
        print(f"Strategy 43 history FAILED: {type(error).__name__}: {error}" if type(error) is RuntimeError
              else f"Strategy 43 history FAILED: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
