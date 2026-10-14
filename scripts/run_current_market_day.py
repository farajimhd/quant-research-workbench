"""Read-only planning or explicitly approved current market-day production.

Uses canonical database source planning, native shared ClickHouse checkpoints
and Keeper ownership. Never calls the legacy disk/SQLite producer controller.
"""
import os
import sys
from pathlib import Path

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
from datetime import date, timedelta
import re
import platform
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from pipelines.market_sip.events.current_market_day_transport import MarketDayQueryLimits, CurrentMarketDaySqlClient
from pipelines.market_sip.events.current_market_day_runtime import execute_current_market_day_build
from pipelines.market_sip.events import market_day_sql
from src.trading_runtime.arte_market_day_certification import TABLES

READER = 'current_market_day_reader'
WRITER = 'current_market_day_publisher'


def principal_plan(years):
    if not years or any(type(year) is not int or not 2000 <= year <= 2100 for year in years):
        raise ValueError('Current producer grants require explicit source years')
    products = {('arte', name) for name in ('bars_v1', 'indicators_v1', 'liquidity_100ms_v1')}
    certificates = {('arte', table.name) for table in TABLES}
    sources = {('market_sip_compact', 'events_' + str(year)) for year in years}
    sources |= {('market_sip_compact', name) for name in (
        'events_source_day_stats', 'events_ordinal_continuity', 'event_condition_token_reference')}
    references = {('q_live', name) for name in ('feature_tradable_universe_snapshot_v2',
        'feature_tradable_universe_snapshot_coverage_v2', 'market_stock_split_v1')}
    system = {('system', name) for name in ('storage_policies', 'tables', 'columns', 'parts', 'data_skipping_indices')}
    reads = frozenset(products | certificates | sources | references | system)
    return ((READER, reads, frozenset()), (WRITER, reads, frozenset(products | certificates)))


def _permission(client, profile):
    principal, reads, writes = profile
    if client.execute('SELECT currentUser()').strip() != principal:
        raise ValueError('Current producer connection is not its dedicated principal')
    actual = set()
    for line in client.execute('SHOW GRANTS FINAL').splitlines():
        match = re.fullmatch(r'GRANT ([A-Z ,]+) ON ([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*) TO ([a-z][a-z0-9_]*)', line.strip())
        if match is None or match[4] != principal:
            raise ValueError('Current producer has broad, delegated or unknown grants')
        actual.update((privilege.strip(), match[2], match[3]) for privilege in match[1].split(','))
    expected = {('SELECT', *scope) for scope in reads} | {('INSERT', *scope) for scope in writes}
    if actual != expected:
        raise ValueError('Current producer grants differ from the reviewed source scope')
    if not writes and client.execute("SELECT getSetting('readonly')").strip() != '1':
        raise ValueError('Current producer reader must be SELECT-only')


def factories(profiles, limits):
    from src.backend.program_app_credentials import _open_managed_client
    def factory(profile):
        def open_client(settings=None):
            chosen = limits.settings()
            if settings is not None and settings != chosen:
                raise ValueError('Current producer connection changed reviewed resource limits')
            if not profile[2]:
                chosen['readonly'] = '1'
            client = _open_managed_client(profile[0], timeout_seconds=limits.timeout_seconds + 30, settings=chosen)
            try:
                _permission(client, profile)
                return client
            except BaseException:
                client.close()
                raise
        return open_client
    return factory(profiles[0]), factory(profiles[1])


def canonical_storage_preflight(source, *, years, policy, disks):
    names = tuple('events_' + str(year) for year in years)
    quoted = ','.join(market_day_sql.literal(name) for name in names)
    rows = source.query("SELECT name,storage_policy FROM system.tables WHERE database='market_sip_compact' "
        f"AND name IN ({quoted})", 'canonical_table_policy')
    if (len(rows) != len(names) or {row['name'] for row in rows} != set(names)
            or any(row['storage_policy'] != policy for row in rows)):
        raise ValueError('Canonical event tables differ from the explicit designated storage policy')
    policies = source.query('SELECT disks FROM system.storage_policies WHERE policy_name=' +
        market_day_sql.literal(policy), 'canonical_policy_disks')
    if not policies or any(set(row['disks']) != set(disks) for row in policies):
        raise ValueError('Canonical storage policy differs from its explicit permitted disks')
    parts = source.query("SELECT DISTINCT disk_name FROM system.parts WHERE active "
        "AND database='market_sip_compact' " + f"AND table IN ({quoted})", 'canonical_part_placement')
    if any(row['disk_name'] not in disks for row in parts):
        raise ValueError('Canonical event parts are outside the designated source disks')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-date', required=True, type=date.fromisoformat)
    parser.add_argument('--end-date', required=True, type=date.fromisoformat)
    parser.add_argument('--parent', action='append', default=[], metavar='BUILD:DAY,DAY',
        help='Explicit attested predecessor build and every session in that build')
    parser.add_argument('--allow-empty-predecessors', action='store_true',
        help='Require native absent-receipt evidence before existing first-bar bootstrap')
    parser.add_argument('--allow-carried-forward-universe', action='store_true')
    parser.add_argument('--show-grants', action='store_true', help='Print review plan only; opens no connections')
    parser.add_argument('--execute', action='store_true', help='Requires separate user approval and reviewed definition hash')
    parser.add_argument('--expected-definition-hash')
    parser.add_argument('--workers', required=True, type=int)
    parser.add_argument('--max-threads', required=True, type=int)
    parser.add_argument('--max-memory-bytes', required=True, type=int)
    parser.add_argument('--query-timeout', required=True, type=int)
    parser.add_argument('--max-result-rows', required=True, type=int)
    parser.add_argument('--max-result-bytes', required=True, type=int)
    parser.add_argument('--max-plan-units', required=True, type=int)
    parser.add_argument('--canonical-storage-policy', required=True)
    parser.add_argument('--canonical-disk', action='append', required=True)
    args = parser.parse_args(argv)
    if (args.end_date < args.start_date or not 1 <= args.workers <= 6 or args.max_plan_units < 1
            or any(re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]*', value) is None
                or value.lower() in ('default', 'hdd') for value in
                (args.canonical_storage_policy, *args.canonical_disk))
            or (args.execute and (args.show_grants or not args.expected_definition_hash
                or re.fullmatch('[0-9a-f]{64}', args.expected_definition_hash) is None))):
        parser.error('Invalid range, capacity or execution approval hash')
    args.parents = []
    for value in args.parent:
        build, separator, days = value.partition(':')
        if not separator or re.fullmatch('[0-9a-f]{64}(?:-[0-9a-f]{12})?', build) is None:
            parser.error('Parent must be an explicit native build and sessions')
        sessions = tuple(date.fromisoformat(day).isoformat() for day in days.split(','))
        if len(set(sessions)) != len(sessions):
            parser.error('Parent sessions must be unique')
        args.parents.append((build, sessions))
    if len(args.parents) > 256 or len({p[0] for p in args.parents}) != len(args.parents):
        parser.error('Parent builds must be unique and bounded')
    args.limits = MarketDayQueryLimits(args.max_threads, args.max_memory_bytes,
        args.query_timeout, args.max_result_rows, args.max_result_bytes)
    earliest = min([args.start_date - timedelta(days=14)] +
        [date.fromisoformat(day) for _, sessions in args.parents for day in sessions])
    args.profiles = principal_plan(tuple(range(earliest.year, args.end_date.year + 1)))
    return args


async def run(args):
    from scripts.build_market_day import digest, source_plan, storage_preflight
    from src.backend.qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.keeper_session import open_workstation_keeper_session
    from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
    from src.trading_runtime.arte_market_day_cold_preflight import audit_attested_market_day_certificate, verify_attested_market_products
    from src.trading_runtime.arte_market_day_publisher import _read
    from pipelines.market_sip.events.market_day_predecessor_absence import certify_empty_market_day_predecessors
    if args.execute and platform.node().upper() != 'DESKTOP-SAAI85T':
        raise ValueError('Current producer execution must run on the designated workstation')
    reader_factory, writer_factory = factories(args.profiles, args.limits)
    def source_factory():
        return CurrentMarketDaySqlClient(reader_factory(), args.limits)
    def prepare():
        source = source_factory()
        try:
            years = tuple(range(args.start_date.year, args.end_date.year + 1))
            canonical_storage_preflight(source, years=years, policy=args.canonical_storage_policy,
                disks=tuple(args.canonical_disk))
            storage_preflight(source, 'arte', require_tables=True)
            plan = source_plan(source, SimpleNamespace(start=args.start_date, end=args.end_date,
                symbols=(), allow_carried_forward_universe=args.allow_carried_forward_universe,
                max_plan_units=args.max_plan_units))
        finally:
            source.close()
        # Stable calculation profile preserves existing numerical predecessor compatibility.
        definition = dict(version=market_day_sql.VERSION,
            calculation_source=digest(Path(market_day_sql.__file__).read_text()),
            rules_hash=digest(plan['rules']), plan=plan,
            producer_contract='current-shared-checkpoint-producer-v1')
        parents = []
        if args.parents:
            session = open_workstation_keeper_session()
            try:
                reader = reader_factory()
                try:
                    for build, days in args.parents:
                        audit = audit_attested_market_day_certificate(reader, MarketDayKeeperReader(session.client),
                            build, sessions=days, include_session_hashes=True)
                        audit = verify_attested_market_products(reader, audit,
                            execution_interval='1s', required_resolutions_ms=market_day_sql.FRAMES)
                        headers = _read(reader, 'market_day_build_header_v1', build)
                        if len(headers) != 1:
                            raise ValueError('Predecessor build lacks one typed header')
                        parents.append((audit, headers[0]))
                finally:
                    reader.close()
            finally:
                session.close()
        return definition, tuple(parents)
    definition, parents = await _owned_worker(prepare)
    build = digest(definition)
    scopes = tuple(sorted((row['source_date'], row['ticker']) for row in definition['plan']['units']))
    from pipelines.market_sip.events.market_day_checkpoint import load_completed_market_day_ledger
    from pipelines.market_sip.events.market_day_predecessor import MarketDayPredecessorLedger
    ledger = await load_completed_market_day_ledger(reader_factory, build, scopes)
    empty = None
    if args.allow_empty_predecessors:
        declared = {scope for audit, _ in parents for scope in audit.certificate.scopes}
        absent = tuple(sorted({(definition['plan']['predecessors'][day], ticker) for day, ticker in scopes
            if definition['plan']['predecessors'][day] is not None
            and (definition['plan']['predecessors'][day], ticker) not in set(scopes) | declared}))
        if absent:
            empty = await certify_empty_market_day_predecessors(reader_factory, build, absent,
                profile=(definition['calculation_source'], definition['rules_hash'], definition['version']))
    registry = MarketDayPredecessorLedger(ledger, definition, parents, empty_predecessors=empty)
    profile = definition['calculation_source'], definition['rules_hash'], definition['version']
    for day, ticker in scopes:
        previous = definition['plan']['predecessors'][day]
        if previous is not None and (previous, ticker) not in set(scopes):
            registry.for_target(day, ticker).candidates(previous, ticker, build, 'arte', *profile)
    print(f"{'EXECUTE' if args.execute else 'PLAN ONLY'} | build {build} | sessions {len(definition['plan']['requested'])} | ticker-sessions {len(scopes)}", flush=True)
    if not args.execute:
        print('Read-only plan complete. Publication, services and backtests have not started.', flush=True)
        return 0
    if args.expected_definition_hash != build:
        raise ValueError('Canonical source plan changed from the approved definition hash')
    last_report, last_phase = 0., None
    async def progress(row):
        nonlocal last_report, last_phase
        now = monotonic()
        if row['phase'] == last_phase and now - last_report < 1:
            return
        last_report, last_phase = now, row['phase']
        print(f"{row['phase']} | active {row['active']} queued {row['queued']} completed {row['completed']} skipped {row['skipped']} failed {row['failed']} / {row['total']}", flush=True)
    await execute_current_market_day_build(definition, owner_id='current-market-day-' + uuid4().hex,
        keeper_session_factory=open_workstation_keeper_session, checkpoint_reader_factory=reader_factory,
        writer_http_factory=writer_factory, sql_http_factory=writer_factory,
        source_client_factory=source_factory, limits=args.limits, parents=parents,
        workers=args.workers, on_progress=progress, empty_predecessors=empty)
    print('Published: native source replay, exact readback and final Keeper attestation verified.', flush=True)
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.show_grants:
        print('REVIEW ONLY | 2 dedicated principals | No connections or account changes')
        for principal, reads, writes in args.profiles:
            for privilege, scopes in (('SELECT', reads), ('INSERT', writes)):
                for database, table in sorted(scopes):
                    print(f'GRANT {privilege} ON {database}.{table} TO {principal}')
        return 0
    from research.mlops.env import discover_env_files, load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[1]), verbose=False)
    return asyncio.run(run(args))


if __name__ == '__main__':
    raise SystemExit(main())
