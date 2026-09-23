"""Build certified causal intraday V7 views from persisted ARTE 1s bars.

Plan mode is read-only.  ``--execute`` publishes one immutable ticker-day
attempt at a time; coverage is written only after state and level rows pass
read-back integrity.  Retrospective structural_levels_v7 is never read.
"""
from __future__ import annotations

import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import sys
sys.dont_write_bytecode = True

import argparse
from datetime import date, datetime
from hashlib import sha256
from math import prod
from pathlib import Path
import time as wall_time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_market_day import Client, DEFAULT_ENV, build_lock, parse_args as market_args
from src.backend.backtest_market_data import MarketDayLedger
from pipelines.market_sip.events.causal_v7_product import (
    input_hash, materialize,
    product_storage_preflight, publish, published_coverage, sql_literal,
)
from src.market_engine.causal_v7_contract import VERSION
from src.market_engine.streaming_level_book import StreamingLevelBook
from src.market_engine.v7_qmd import Service, session_bounds
from research.level_book.v7.campaign import literal
from scripts.build_structure_book_clickhouse import canonical_splits


def args_from(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--tickers", default="", help="Comma-separated subset; default is the full certified day")
    parser.add_argument("--build-id", default="", help="Required with --execute; pins the market-day build")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--runtime-root", type=Path, default=Path("D:/TradingML/runtimes"))
    parser.add_argument("--execute", action="store_true", help="Publish to the current arte database")
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args(argv)
    if args.progress_every < 1:
        parser.error("--progress-every must be positive")
    if args.execute and not args.build_id:
        parser.error("--execute requires --build-id from a reviewed read-only plan")
    return args


def producer_hash() -> str:
    import pipelines.market_sip.events.causal_v7_product as product
    import src.market_engine.streaming_level_book as kernel
    import src.market_engine.v7_qmd as projection

    checksum = sha256()
    import src.market_engine.causal_v7_contract as contract
    for module in (product, kernel, projection, contract):
        path = Path(module.__file__)
        checksum.update(path.name.encode("utf-8"))
        checksum.update(path.read_bytes())
    return checksum.hexdigest()


def source_rows(client: Client, unit) -> list[dict]:
    day = unit.session_date
    rows = client.query(
        "SELECT bucket_index,open_int,high_int,low_int,close_int,volume,price_valid "
        f"FROM arte.bars_v1 WHERE build_id={sql_literal(unit.build_id)} "
        f"AND session_date=toDate({sql_literal(day)}) AND ticker={sql_literal(unit.ticker)} "
        f"AND attempt_id=toUUID({sql_literal(unit.attempt_id)}) "
        "AND resolution_ms=1000 AND price_valid=1 AND bucket_index>=14700 "
        "AND bucket_index<72000 ORDER BY bucket_index",
        "causal_v7_input",
    )
    if len({int(row["bucket_index"]) for row in rows}) != len(rows):
        raise ValueError(f"{day} {unit.ticker}: duplicate persisted 1s input")
    return rows


def split_evidence(client: Client, ticker: str, prior_day: str, day: date, at: datetime) -> list[dict]:
    rows = client.query(
        "SELECT provider_ticker,execution_date,split_from,split_to,inserted_at "
        "FROM q_live.market_stock_split_v1 FINAL "
        f"WHERE provider_ticker={literal(ticker)} AND execution_date>{literal(prior_day)} "
        f"AND execution_date<={literal(day.isoformat())} "
        f"AND inserted_at<=parseDateTime64BestEffort({literal(at.isoformat())}) "
        "ORDER BY execution_date",
        "causal_v7_splits",
    )
    return canonical_splits(rows)


def run(argv: list[str] | None = None) -> int:
    args = args_from(argv)
    tickers = tuple(sorted({part.strip().upper() for part in args.tickers.split(",") if part.strip()}))
    plan = MarketDayLedger().certified_plan(
        sessions=[args.date], tickers=tickers,
        configuration={"strategy": {"execution_interval": "1s"}},
    )
    if args.build_id and plan.build_id != args.build_id:
        raise ValueError("Reviewed market-day build changed; rerun plan with the new build ID")
    selected = [unit for unit in plan.units if unit.stage == "bars"]
    root = args.runtime_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Required workstation runtime root is unavailable: {root}")
    runtime = root / "causal-v7-market-day"
    if args.execute:
        from src.backend.backtest_market_data import verify_market_day_plan
        # Producer credentials are confined to this offline builder.  Backtest
        # uses a separate SELECT-only account and never imports this script.
        client = Client(market_args(["--date", args.date.isoformat(), "--env-file", str(args.env_file)]))
    else:
        print(f"Plan: {args.date} | build {plan.build_id} | ticker-days {len(selected)} "
              f"| source {VERSION} | no writes", flush=True)
        return 0

    service = None
    began = wall_time.monotonic()
    completed = skipped = failed = 0
    failures: list[tuple[str, str]] = []
    runtime.mkdir(exist_ok=True)
    lock = runtime / f"{plan.build_id}-{args.date}.lock"
    by_ticker = {unit.ticker: unit for unit in selected}
    try:
        service = Service()
        producer = producer_hash()
        with build_lock(lock):
            verify_market_day_plan(plan, client.http)
            product_storage_preflight(client, create=True)
            print(f"Building {args.date} | build {plan.build_id} | {len(selected)} tickers "
                  f"| policy live_market_ssd | output arte causal V7", flush=True)
            begin, end = session_bounds(args.date.isoformat())
            for index, ticker in enumerate(sorted(by_ticker), start=1):
                unit = by_ticker[ticker]
                try:
                    bars = source_rows(client, unit)
                    bar_hash = input_hash(args.date, bars)
                    prior, provenance = service._seed(ticker, args.date.isoformat(),
                                                      begin.timestamp(), "history")
                    if prior["available_at"] > begin.timestamp():
                        raise ValueError("Preceding V7 seed is not available at 04:00 ET")
                    old = published_coverage(client, plan.build_id, args.date, ticker)
                    if old is not None:
                        status, _ = publish(
                            client, build_id=plan.build_id, day=args.date, ticker=ticker,
                            bars_attempt=unit.attempt_id, source_hash=unit.source_hash,
                            seed_checkpoint_hash=prior["checkpoint_hash"],
                            catalog_hash=service.catalog.fingerprint,
                            producer_hash=producer, input_hash=bar_hash,
                            states=[], levels=[],
                        )
                    else:
                        splits = split_evidence(client, ticker, prior["session"], args.date, begin)
                        engine = StreamingLevelBook(
                            prior, ticker=ticker, session=args.date.isoformat(),
                            start=begin.timestamp(), end=end.timestamp(),
                            split_factor=prod(float(row["split_from"]) / float(row["split_to"])
                                              for row in splits),
                            split_evidence=splits,
                        )
                        states, levels, observed_hash = materialize(
                            day=args.date, ticker=ticker, engine=engine, bars=bars,
                            provenance={key: value for key, value in provenance.items()
                                        if key != "source_plan"},
                            source_hash=unit.source_hash,
                        )
                        if observed_hash != bar_hash:
                            raise ValueError("V7 source bars changed during materialization")
                        status, _ = publish(
                            client, build_id=plan.build_id, day=args.date, ticker=ticker,
                            bars_attempt=unit.attempt_id, source_hash=unit.source_hash,
                            seed_checkpoint_hash=prior["checkpoint_hash"],
                            catalog_hash=service.catalog.fingerprint,
                            producer_hash=producer, input_hash=bar_hash,
                            states=states, levels=levels,
                        )
                    if status == "completed":
                        completed += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    failed += 1
                    failures.append((ticker, f"{type(exc).__name__}: {exc}"))
                    print(f"FAILED {ticker}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                if index % args.progress_every == 0 or index == len(selected):
                    elapsed = wall_time.monotonic() - began
                    print(f"{index}/{len(selected)} | completed {completed} | reused {skipped} "
                          f"| failed {failed} | active {ticker} | elapsed {elapsed:.1f}s", flush=True)
    finally:
        if service is not None:
            service.close()
        client.close()
    print(f"Finished | completed {completed} | reused {skipped} | failed {failed} "
          f"| elapsed {wall_time.monotonic() - began:.1f}s", flush=True)
    if failures:
        print("Review the failed ticker reasons above; rerun the same pinned build after repair.",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
