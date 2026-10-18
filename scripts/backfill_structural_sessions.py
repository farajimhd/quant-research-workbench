"""Resume V7 history and persist PM/regular streaming states in normalized ARTE.

Requires a completed bar-funnel runtime. Defaults to 2026-01-01 through the
latest certified canonical day. Plan-only performs no schema or data writes.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from hashlib import sha256

from scripts import build_market_day as builder
from research.level_book.v7.campaign import RULE_SQL
from research.level_book.v7 import campaign_source as source
from research.level_book.v7.clickhouse_persistence import (
    LEVELS_TABLE, OBSERVATIONS_TABLE, COVERAGE_TABLE, compact_checkpoints,
    datetime64_ns, epoch_ns,
)
from pipelines.market_sip.events.structural_session_checkpoints import VERSION, calculate, source_rows
from pipelines.market_sip.events import structural_checkpoint_storage as storage
from pipelines.market_sip.events.causal_v7_product import input_bar
from src.backend.structural_v7_seed import load_seed, split_evidence
from src.market_engine.historical_level_checkpoint import digest


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bars-run-dir', type=Path, required=True)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--start-date', type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument('--end-date', type=date.fromisoformat)
    p.add_argument('--env-file', type=Path, default=Path('D:/TradingML/secrets/.env'))
    p.add_argument('--endpoint', default='http://192.168.1.218:18123')
    p.add_argument('--plan-only', action='store_true')
    p.add_argument('--profile-only', action='store_true', help='Read-only real streaming profile for three certified ticker-days')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--migrate-schema', action='store_true', help='Apply additive boundary keys before writing')
    a = p.parse_args(argv)
    if a.end_date and a.end_date < a.start_date:
        p.error('End date precedes start date')
    return a


def profile(client, a, report, selected, closes):
    """Bounded real source/stream/serialize measurements before the campaign."""
    from time import perf_counter
    from types import SimpleNamespace
    from pipelines.market_sip.events.structural_session_checkpoints import normalized_boundary
    from src.backend.structural_v7_seed import preceding_coverage
    candidates = [name for name in ('AAPL', 'MSFT', 'IBM') if any(t == name for _, t in selected)]
    if not candidates:
        candidates = sorted({ticker for _, ticker in selected})[:3]
    results = []
    with closing(sqlite3.connect((a.bars_run_dir/'build-ledger-v2.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        for ticker in candidates:
            day = min(d for d,t in selected if t==ticker)
            receipt = db.execute("SELECT * FROM units WHERE build_id=? AND session_date=? AND ticker=? "
                "AND stage='bars' AND status='complete'", (report['build_id'],day,ticker)).fetchone()
            if receipt is None:raise ValueError('Profile requires a completed bar receipt')
            unit = SimpleNamespace(**dict(receipt)); started=perf_counter()
            proof=builder.evidence(client,'arte','bars',unit.build_id,day,ticker,unit.attempt_id)
            if int(proof['n'])!=unit.output_rows or str(proof['hash'])!=unit.output_hash:
                raise ValueError('Profile source differs from certified bars')
            seed=preceding_coverage(client.http,ticker=ticker,session=date.fromisoformat(day))
            prior=load_seed(client.http,ticker=ticker,session=date.fromisoformat(day),coverage=seed)
            actions=split_evidence(client.http,ticker=ticker,seed_session=date.fromisoformat(prior['session']),session=date.fromisoformat(day))
            bars=[input_bar(date.fromisoformat(day), row) for row in source_rows(client,unit)]
            fetched=perf_counter()
            values=calculate(prior=prior,ticker=ticker,day=day,bars=bars,actions=actions,
                regular_close=closes[day],existing_ah=True)
            calculated=perf_counter();sizes={}
            for boundary,value in values.items():
                levels,observations,coverage=normalized_boundary(value,'a'*64)
                sizes[boundary]=dict(levels=len(levels),observations=len(observations),
                    payload_bytes=len(coverage['streaming_state'].encode()))
            results.append(dict(ticker=ticker,day=day,bars=len(bars),fetch_seconds=fetched-started,
                stream_seconds=calculated-fetched,serialize_seconds=perf_counter()-calculated,boundaries=sizes))
    builder.save(a.run_dir/'structural-profile.json',dict(status='complete',read_only=True,units=results))
    print(json.dumps(results),flush=True)
    return 0


def ah_coverage(client, ticker, day):
    rows = client.query(f"SELECT * FROM {COVERAGE_TABLE} FINAL WHERE ticker={source.literal(ticker)} "
        f"AND session_date=toDate({source.literal(day)}) AND checkpoint_boundary=''")
    if len(rows)>1 or any(r['state'] not in ('complete','empty') for r in rows):
        raise ValueError('Ambiguous or incomplete existing AH checkpoint')
    return rows


def publish_ah(client, book, plan_hash):
    """Append a daily snapshot, closing original open AH intervals in place.

    Original source hashes and checkpoint lineage are retained in closed rows.
    A restart may repeat these deterministic writes before coverage is exposed.
    """
    ticker = book['ticker']; stamp = datetime64_ns(epoch_ns(book['available_at']))
    for table in (LEVELS_TABLE, OBSERVATIONS_TABLE):
        opened = client.query(f"SELECT * FROM {table} FINAL WHERE ticker={source.literal(ticker)} "
            "AND checkpoint_boundary='' AND isNull(valid_to)")
        for row in opened:
            row.update(valid_to=stamp, row_revision=epoch_ns(book['available_at']))
        storage.insert(client, table, opened)
    levels, observations, coverage = compact_checkpoints([book], plan_hash)
    storage.insert(client, LEVELS_TABLE, levels)
    storage.insert(client, OBSERVATIONS_TABLE, observations)
    for table, expected in ((LEVELS_TABLE, len(levels)), (OBSERVATIONS_TABLE, len(observations))):
        rows = client.query(f"SELECT count() n FROM {table} FINAL WHERE ticker={source.literal(ticker)} "
            f"AND checkpoint_boundary='' AND valid_from=toDateTime64({source.literal(stamp)},9,'UTC')")
        if int(rows[0]['n']) != expected:
            raise ValueError('AH normalized snapshot read-back count differs')
    coverage[0]['published_at'] = datetime64_ns(epoch_ns(datetime.now(timezone.utc).timestamp()))
    storage.insert(client, COVERAGE_TABLE, coverage)
    storage.storage_preflight(client)


def run(a):
    import pandas_market_calendars as mcal
    from pipelines.certified_research.source import inventory
    from types import SimpleNamespace
    os.environ['QMD_CLICKHOUSE_URL'] = a.endpoint
    runtime = Path('D:/TradingML/runtimes').resolve()
    if (not runtime.is_dir() or not a.run_dir.parent.is_dir()
            or not a.run_dir.resolve().is_relative_to(runtime)):
        raise ValueError('Required runtime parent is unavailable')
    if not (a.plan_only or a.profile_only) and os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':
        raise ValueError('Execute structural writers on DESKTOP-SAAI85T')
    options = builder.parse_args(['--date', str(a.start_date), '--env-file', str(a.env_file),
                                  '--max-threads', '1', '--max-memory-gb', '2'])
    with closing(builder.Client(options, readonly=a.plan_only or a.profile_only)) as client:
        latest = client.query('SELECT max(source_date) day FROM market_sip_compact.events_source_day_stats FINAL')[0]['day']
        end = a.end_date or date.fromisoformat(latest)
        inv = inventory(client.query, a.start_date, end)
        if inv['missing'] or inv['incomplete']:
            raise ValueError('Structural target range has uncertified canonical source days')
        report = json.loads((a.bars_run_dir / 'build' / 'latest.json').read_text())
        if report['status'] != 'core_complete' and not a.profile_only:
            raise ValueError('Structural backfill waits for completed bar-funnel certification')
        units = report['definition']['plan']['units']
        selected = {(r['source_date'], r['ticker']) for r in units if str(a.start_date) <= r['source_date'] <= str(end)}
        expected_days = {str(day) for day in inv['expected']}
        if {day for day, _ in selected} != expected_days:
            raise ValueError('Completed bars do not cover the structural target range')
        history_start = min(date(2025, 1, 1), a.start_date)
        history_inventory = inventory(client.query, history_start, end)
        reporting = client.query(source.reporting_coverage_sql(str(history_start), str(end)))
        import src.market_engine.streaming_level_book as kernel
        import research.level_book.v7.campaign as retrospective
        import research.level_book.v7.clickhouse_persistence as normalized
        import src.backend.structural_v7_seed as seeds
        request = dict(version=VERSION, start=str(a.start_date), end=str(end),
            build_id=report['build_id'], bar_definition=digest(report['definition']),
            population_hash=digest(sorted(selected)), canonical=dict(content_hash=inv['content_hash'],
                source_hashes=history_inventory['source_hashes'], import_statuses=inv['import_statuses']),
            reporting=reporting, rules_hash=digest(client.query(RULE_SQL)),
            source_hashes={str(Path(module.__file__).relative_to(Path(__file__).resolve().parents[1])):
                sha256(Path(module.__file__).read_bytes()).hexdigest()
                for module in (storage, sys.modules[calculate.__module__], kernel, retrospective,
                               normalized, seeds, source, sys.modules[__name__])})
        # Canonical inventory contains dates; freeze its JSON representation.
        request = json.loads(json.dumps(request, default=str))
        plan_hash = digest(request)
        producer_hash = digest(request['source_hashes'])
        by_ticker = {}
        for day, ticker in selected:
            by_ticker.setdefault(ticker, []).append(day)
        a.run_dir.mkdir(exist_ok=True)
        builder.save(a.run_dir / 'structural-plan.json', request)
        if a.plan_only:
            print(json.dumps(dict(start=str(a.start_date), end=str(end), ticker_days=len(selected),
                                  status='planned', build_id=report['build_id'])), flush=True)
            return 0
        if a.profile_only:
            schedule=mcal.get_calendar('XNYS').schedule(start_date=a.start_date,end_date=end)
            closes={str(i.date()):row.market_close.to_pydatetime() for i,row in schedule.iterrows()}
            return profile(client,a,report,selected,closes)
        with builder.build_lock(builder.RUNTIME / 'structural-session-arte.lock'):
            path = a.run_dir / 'structural-request.json'
            if path.exists():
                if not a.resume or json.loads(path.read_text()) != request:
                    raise ValueError('Structural resume requires identical frozen request')
            elif a.resume:
                raise ValueError('No structural request exists to resume')
            else:
                builder.save(path, request)
            if a.migrate_schema:
                storage.migrate(client)
            storage.require_schema(client)
            rules = client.query(RULE_SQL)
            schedule = mcal.get_calendar('XNYS').schedule(start_date=history_start, end_date=end)
            closes = {str(i.date()): row.market_close.to_pydatetime() for i, row in schedule.iterrows()}
            tickers = sorted({ticker for _, ticker in selected})
            ledger_path = a.bars_run_dir / 'build-ledger-v2.sqlite3'
            db = sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True)
            db.row_factory = sqlite3.Row
            done = reused = 0
            try:
                for ticker in tickers:
                    first_target, last_target = min(by_ticker[ticker]), max(by_ticker[ticker])
                    known_ah = client.query(f"SELECT * FROM {COVERAGE_TABLE} FINAL WHERE ticker={source.literal(ticker)} "
                        f"AND checkpoint_boundary='' AND session_date<={source.literal(last_target)} ORDER BY session_date")
                    ah_by_day = {str(row['session_date']): row for row in known_ah}
                    if len(ah_by_day) != len(known_ah) or any(row['state'] not in ('complete','empty') for row in known_ah):
                        raise ValueError('Existing AH coverage is ambiguous or incomplete')
                    preceding = [day for day in ah_by_day if day < first_target]
                    replay_start = max(preceding) if preceding else str(history_start-timedelta(days=1))
                    metadata = client.query('SELECT * FROM market_sip_compact.events_ordinal_continuity FINAL '
                        f'WHERE ticker={source.literal(ticker)} AND source_date>{source.literal(replay_start)} '
                        f'AND source_date<={source.literal(last_target)} ORDER BY source_date')
                    if client.query(source.reporting_coverage_sql(str(history_start), str(end))) != reporting:
                        raise ValueError('Reporting certificates changed during structural calculation')
                    if digest(client.query(RULE_SQL)) != request['rules_hash']:
                        raise ValueError('Canonical condition rules changed during structural calculation')
                    for meta in metadata:
                        day = str(meta['source_date'])
                        target = (day, ticker) in selected
                        exists = ah_by_day.get(day)
                        if exists and not target:
                            continue
                        source.require_reporting_coverage([day], reporting)
                        certified_day = inventory(client.query, date.fromisoformat(day), date.fromisoformat(day))
                        if certified_day['missing'] or certified_day['incomplete']:
                            raise ValueError('Required historical replay day is not canonical-certified')
                        if certified_day['source_hashes'].get(day) != history_inventory['source_hashes'].get(day):
                            raise ValueError('Canonical replay source changed after the frozen plan')
                        if (a.run_dir / 'STOP').exists():
                            raise KeyboardInterrupt
                        prior_days = [previous for previous in ah_by_day if previous < day]
                        prior_rows = [ah_by_day[max(prior_days)]] if prior_days else []
                        prior = load_seed(client.http, ticker=ticker, session=date.fromisoformat(day),
                                          coverage=prior_rows[0]) if prior_rows else None
                        actions = split_evidence(client.http, ticker=ticker,
                            seed_session=date.fromisoformat(prior['session']) if prior else history_start-timedelta(days=1),
                            session=date.fromisoformat(day))
                        current = client.query(f"SELECT * FROM {COVERAGE_TABLE} FINAL WHERE ticker={source.literal(ticker)} "
                            f"AND session_date=toDate({source.literal(day)}) AND checkpoint_boundary IN ('PM','REGULAR')") if target else []
                        unit = None
                        source_ref = None
                        if target:
                            receipt = db.execute("SELECT * FROM units WHERE build_id=? AND session_date=? AND ticker=? "
                                "AND stage='bars' AND status='complete'", (report['build_id'], day, ticker)).fetchone()
                            if receipt is None:
                                raise ValueError(f'Missing certified bar receipt: {day} {ticker}')
                            unit = SimpleNamespace(**dict(receipt))
                            source_ref = dict(build_id=unit.build_id, attempt_id=unit.attempt_id,
                                source_hash=unit.source_hash, output_hash=unit.output_hash,
                                canonical_hash=history_inventory['source_hashes'][day],
                                producer_hash=producer_hash)
                        if exists and {r['checkpoint_boundary'] for r in current} == {'PM', 'REGULAR'}:
                            from pipelines.market_sip.events.structural_session_checkpoints import restore_boundary
                            for row in current:
                                _, value = restore_boundary(row)
                                if value.get('source') != source_ref or value['prior_hash'] != prior['checkpoint_hash']:
                                    raise ValueError('Existing boundary source or previous AH differs')
                                storage.publish_boundary(client, value, row['source_plan_hash'])
                            reused += 1
                            continue
                        bars = []
                        if target:
                            proof = builder.evidence(client, 'arte', 'bars', unit.build_id, day, ticker, unit.attempt_id)
                            if int(proof['n']) != unit.output_rows or str(proof['hash']) != unit.output_hash:
                                raise ValueError('Persisted bars differ from certified output receipt')
                            bars = [input_bar(date.fromisoformat(day), row) for row in source_rows(client, unit)]
                        else:
                            bars, _ = source.decode(client.query(source.bars_sql(ticker, day, rules, meta)))
                        historical_bars = None
                        if target and not exists:
                            historical_bars, _ = source.decode(client.query(source.bars_sql(ticker, day, rules, meta)))
                        values = calculate(prior=prior, ticker=ticker, day=day, bars=bars,
                            actions=actions, regular_close=closes[day], existing_ah=bool(exists),
                            persist_intraday=target, historical_bars=historical_bars)
                        for boundary in ('PM', 'REGULAR'):
                            if boundary in values:
                                values[boundary]['source'] = source_ref
                                storage.publish_boundary(client, values[boundary], plan_hash)
                        if 'AH' in values:
                            publish_ah(client, values['AH'], plan_hash)
                            ah_by_day[day] = ah_coverage(client, ticker, day)[0]
                        done += int(target)
                        builder.save(a.run_dir / 'status.json', dict(status='running', active=f'{day} {ticker}',
                            completed=done, reused=reused, total=len(selected), failed=0,
                            queued=len(selected)-done-reused))
                        print(f'{done+reused}/{len(selected)} | completed {done} | reused {reused} | failed 0 | {day} {ticker}', flush=True)
                if done + reused != len(selected):
                    raise ValueError('Structural completion count differs from frozen population')
            except BaseException as error:
                interrupted = isinstance(error, KeyboardInterrupt)
                builder.save(a.run_dir / 'status.json', dict(status='interrupted' if interrupted else 'failed',
                    completed=done, reused=reused, total=len(selected), failed=0 if interrupted else 1,
                    error=client.clean_error(error)))
                raise
            finally:
                db.close()
            builder.save(a.run_dir / 'structural-completion.json', dict(request=request,
                completed=done, reused=reused, failed=0, utc=datetime.now(timezone.utc).isoformat()))
            builder.save(a.run_dir / 'status.json', dict(status='complete', completed=done,
                reused=reused, total=len(selected), failed=0, queued=0))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run(arguments()))
    except KeyboardInterrupt:
        raise SystemExit(130)
