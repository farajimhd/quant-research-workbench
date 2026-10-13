"""Plan the frozen all30 winner's central release; publication is opt-in.

Optimizer files supply only the program and comparison reference. No research
feature banks, private tables, app installation or backtests are used here.
"""
import os
import sys
from pathlib import Path

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
from datetime import datetime
import platform
from uuid import UUID

from src.trading_runtime.optimized_strategy_reference import read_reference
from src.trading_runtime.optimized_program_specification import compile_frozen_winner, WINNER_FIELDS, STOP_FIELD
from src.trading_runtime.columnar_rule_program import InputField
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from src.market_engine.columnar_history_product import BY_NAME, product_revision, require_hash


def prepare_release(args):
    """Source-only planning; native reader verifies all30 receipts and clocks."""
    from src.backend.backtest_journal_clickhouse import backtest_code_hash
    if backtest_code_hash(Path(__file__).resolve().parents[1]) != args.code_hash:
        raise ValueError('Release code hash differs from the current native Backtest source closure')
    reference = read_reference(args.reference_root, concurrency=args.concurrency, input_root=args.clock_root)
    selected = tuple(sorted((BY_NAME[name] for name in
        ({name for name, _ in WINNER_FIELDS.values()} | {STOP_FIELD})), key=lambda field: field.field_id))
    revision = product_revision(selected, args.history_code_hash, volume_window_seconds=30)
    fields = {field.name: InputField(field.name, field.unit,
        'q_live.qmd_causal_history_value_v1', revision, 1000) for field in selected}
    # This declaration is the exact frozen compiler contract. It is validated
    # again by compile_frozen_winner; it does not select app market membership.
    universe = dict(version='v6-volume-market-blocks-v1', window=30, top_n=10,
        minimum_close=.8, maximum_close=50.)
    specification = compile_frozen_winner(reference, fields, universe_identity=universe)
    rows = project_release(specification, fields, strategy_number=args.strategy_number,
        strategy_id=args.strategy_id, release_id=args.release_id, code_hash=args.code_hash,
        universe_source_hash=args.universe_source_hash, published_at=args.published_at)
    return reference, rows, restore_release(rows)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-root', required=True)
    parser.add_argument('--clock-root', required=True)
    parser.add_argument('--strategy-number', required=True, type=int)
    parser.add_argument('--strategy-id', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--published-at', required=True, type=datetime.fromisoformat)
    parser.add_argument('--code-hash', required=True)
    parser.add_argument('--history-code-hash', required=True)
    parser.add_argument('--universe-source-hash', required=True)
    parser.add_argument('--concurrency', required=True, type=int)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--expected-release-hash')
    parser.add_argument('--max-threads', required=True, type=int)
    parser.add_argument('--max-memory-bytes', required=True, type=int)
    parser.add_argument('--query-timeout', required=True, type=int)
    parser.add_argument('--max-result-rows', required=True, type=int)
    parser.add_argument('--max-result-bytes', required=True, type=int)
    args = parser.parse_args(argv)
    UUID(args.release_id)
    for value in (args.code_hash, args.history_code_hash, args.universe_source_hash):
        require_hash(value)
    if (not 1 <= args.concurrency <= 6 or not 1 <= args.strategy_number < 2**32
            or not args.strategy_id or args.published_at.tzinfo is None
            or args.published_at.utcoffset() is None
            or any(value <= 0 for value in (args.max_threads, args.max_memory_bytes,
                args.query_timeout, args.max_result_rows, args.max_result_bytes))):
        parser.error('Release requires explicit immutable identities and bounded resources')
    if args.publish:
        if not args.expected_release_hash:
            parser.error('--publish requires the reviewed --expected-release-hash')
        require_hash(args.expected_release_hash)
    elif args.expected_release_hash:
        parser.error('--expected-release-hash is an execution gate, available only with --publish')
    return args


async def run(args):
    from src.backend.qmd_columnar_history_transport import _owned_worker
    from src.backend.backtest_program_journal_startup import _settle_owned_task
    from src.backend.program_app_credentials import program_app_reader_factory, program_app_writer_factory
    from src.trading_runtime.keeper_session import open_workstation_keeper_session
    from src.trading_runtime.columnar_strategy_publication import publish_program_release
    if args.publish and platform.node().upper() != 'DESKTOP-SAAI85T':
        raise ValueError('Release publication must run on the designated workstation')
    reference, rows, release = await _owned_worker(prepare_release, args)
    print(f"{'PUBLISH' if args.publish else 'PLAN ONLY'} | generation {reference.generation} candidate {reference.candidate} | {len(reference.sessions)} training sessions verified", flush=True)
    print(f"Release {release.header['content_hash']} | strategy {args.strategy_id} revision {args.strategy_number} | entry ${release.header['entry_cash']} add ${release.header['add_cash']}", flush=True)
    if not args.publish:
        print('No database connections, release publication, app installation or backtest started.', flush=True)
        return 0
    if release.header['content_hash'] != args.expected_release_hash:
        raise ValueError('Frozen release differs from the reviewed publication hash')
    limits = dict(max_threads=args.max_threads, max_memory_usage=args.max_memory_bytes,
        max_execution_time=args.query_timeout, max_result_rows=args.max_result_rows,
        max_result_bytes=args.max_result_bytes)
    opening = asyncio.create_task(asyncio.to_thread(open_workstation_keeper_session))
    session = None
    try:
        session = await asyncio.shield(opening)
        restored = await publish_program_release(
            program_app_reader_factory('release_reader', query_limits=limits),
            program_app_writer_factory('release_publisher', query_limits=limits), session.client, rows)
        if restored.header['content_hash'] != args.expected_release_hash:
            raise RuntimeError('Published native release readback differs from approved identity')
    finally:
        if session is None:
            session = await _settle_owned_task(opening)
        await _settle_owned_task(asyncio.create_task(asyncio.to_thread(session.close)))
    print('Published central scalar release; native exact readback and Keeper proof verified. No app installation or run started.', flush=True)
    return 0


def main(argv=None):
    return asyncio.run(run(parse_args(argv)))


if __name__ == '__main__':
    raise SystemExit(main())
