"""Read certified market-day products. Never reconstruct events or call QMD."""
from datetime import datetime, time, timezone
from contextlib import closing
from io import StringIO
import json
from pathlib import Path
import sqlite3
from collections import Counter
from uuid import UUID

import polars as pl

from . import arte_sql as sql
from .common import NY, digest
from .arte_sql import ArteReader, query

TABLES = {'bars': 'bars_v1', 'technical': 'indicators_v1'}


def load_build(manifest, ledger, days, tickers=None):
    report = json.loads(Path(manifest).read_text(encoding='utf-8'))
    definition = report['definition']
    build = report['build_id']
    if (report['status'] != 'core_complete' or definition['version'] != 'market-day-core-v5'
            or definition['database'] != 'arte' or definition['storage_policy'] != 'live_market_ssd'
            or definition['trade_eligibility']['excluded_reporting_flag'] != sql.DELAYED):
        raise ValueError('Require a completed, certified market-day-core-v5 arte build')
    if set(map(str, days)) - set(definition['plan']['requested']):
        raise ValueError('Requested sessions are not in this market-day build')
    path = Path(ledger).resolve()
    if not path.is_file():
        raise ValueError('Required market-day certification ledger is unavailable: ' + str(path))
    uri = path.as_uri()
    # SQLite's Windows UNC URI uses an empty authority and a double-slash path.
    if str(path).startswith('\\\\'):
        uri = 'file:////' + path.as_posix().lstrip('/')
    with closing(sqlite3.connect(uri + '?mode=ro', uri=True, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        proof = connection.execute('SELECT * FROM builds WHERE build_id=?', (build,)).fetchone()
        if (not proof or proof['definition_hash'] != digest(definition)
                or proof['status'] != 'core_complete' or proof['database_name'] != 'arte'):
            raise ValueError('Manifest does not match the completed build certificate')
        units = {}
        for day in days:
            selected = [r['ticker'] for r in definition['plan']['units'] if r['source_date'] == str(day)]
            if tickers:
                if set(tickers) - set(selected):
                    raise ValueError(f'{day}: requested ticker absent from certified build population')
                selected = sorted(set(tickers))
            if not selected or len(selected) != len(set(selected)):
                raise ValueError('Empty or duplicate build population')
            rows = connection.execute('SELECT * FROM units WHERE build_id=? AND session_date=?',
                                      (build, str(day))).fetchall()
            by_key = {(r['ticker'], r['stage']): dict(r) for r in rows}
            units[str(day)] = {}
            for ticker in sorted(selected):
                stages = {}
                for stage in TABLES:
                    row = by_key.get((ticker, stage))
                    if not row or row['status'] != 'complete':
                        raise ValueError(f'{day} {ticker}: missing certified {stage}')
                    UUID(row['attempt_id'])
                    stages[stage] = {k: row[k] for k in ('attempt_id', 'source_hash', 'output_rows', 'output_hash')}
                units[str(day)][ticker] = stages
    return dict(build_id=build, definition_hash=digest(definition), definition=definition, units=units)


def storage_check(c):
    names = ','.join(sql.literal(t) for t in TABLES.values())
    tables = query(c, f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({names})")
    if {r['name'] for r in tables} != set(TABLES.values()) or any(r['storage_policy'] != sql.POLICY for r in tables):
        raise ValueError('Required arte tables must use live_market_ssd')
    parts = query(c, f"SELECT table,disk_name,sum(rows) AS rows FROM system.parts WHERE database='arte' AND active AND table IN ({names}) GROUP BY table,disk_name")
    if any(r['disk_name'] != sql.POLICY for r in parts):
        raise ValueError('Operational arte parts are not on live_market_ssd')
    return dict(tables=tables, parts=parts)


def population(c, source, day, *, diagnostic_directory=None, excluded_tickers=()):
    saved = [p for p in source['definition']['plan']['population'] if p['session_date'] == str(day)]
    if len(saved) != 1:
        raise ValueError('Missing unique build population certificate')
    saved = saved[0]
    cert = saved['certificate']
    expected = {('certified', 'preopen-tradable-snapshot-v3'),
                ('carried_forward', 'preopen-tradable-carry-forward-v1')}
    if (cert['status'], cert['revision']) not in expected:
        raise ValueError('Unsupported population certificate')
    if cert['status'] == 'carried_forward' and not source['definition']['population_authority']['allow_carried_forward']:
        raise ValueError('Carried-forward population was not admitted by the source build')
    def utc(value):
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    cutoff = datetime.combine(day, time(4), NY)
    if not utc(cert['captured_at_utc']) <= utc(cert['available_at_utc']) < cutoff or utc(cert['cutoff_utc']) != cutoff:
        raise ValueError('Population was not available before the session')
    where = f"session_date={sql.literal(day)} AND snapshot_id={sql.literal(cert['snapshot_id'])}"
    proof = query(c, "SELECT count() AS n,countIf(is_tradable=1) AS tradable,"
        "sum(cityHash64(tuple(ticker,symbol_id,listing_id,security_id,is_tradable,exclusion_reason,source_run_id,captured_at_utc))) AS source_hash "
        f"FROM q_live.feature_tradable_universe_snapshot_v2 WHERE {where}")[0]
    if any(int(proof[a]) != int(cert[b]) for a, b in [('n', 'row_count'), ('tradable', 'tradable_count'), ('source_hash', 'source_hash')]):
        raise ValueError('Pinned population integrity failure')
    members = query(c, 'SELECT ticker,symbol_id,listing_id,security_id,source_run_id,captured_at_utc AS inserted_at '
        f'FROM q_live.feature_tradable_universe_snapshot_v2 WHERE {where} AND is_tradable=1 ORDER BY ticker,symbol_id,listing_id')
    if digest(members) != saved['snapshot_hash']:
        raise ValueError('Population no longer matches source build')
    planned = set(source['units'][str(day)])
    selected = planned - set(excluded_tickers)
    rows = [r for r in members if r['ticker'] in selected]
    eligibility = dict(authority='q_live.feature_tradable_universe_snapshot_v2',
        snapshot_id=cert['snapshot_id'], revision=cert['revision'], session=str(day),
        rule='pinned preopen is_tradable=1; explicit research exclusions applied after full snapshot verification',
        planned_tickers=len(planned), eligible_tickers=len(selected),
        snapshot_nontradable_rows=int(cert['row_count'])-int(cert['tradable_count']),
        requested_exclusions=sorted(excluded_tickers), excluded_tickers=sorted(planned & set(excluded_tickers)),
        excluded_identity_rows=[r for r in members if r['ticker'] in planned & set(excluded_tickers)],
        exclusion_reasons={ticker: ('operator-declared LGHL broker identity/tradability issue' if ticker == 'LGHL'
                                   else 'explicit operator research exclusion') for ticker in sorted(excluded_tickers)})
    if diagnostic_directory is not None:
        from ..runtime import require_runtime, write_json
        write_json(require_runtime(diagnostic_directory) / 'population-eligibility.json', eligibility)
    counts = Counter(r['ticker'] for r in rows)
    listings = Counter(r['listing_id'] for r in rows)
    missing = sorted(selected - set(counts))
    ambiguous = sorted(name for name, count in counts.items() if count != 1)
    invalid = [r for r in rows if not r['listing_id'] or listings[r['listing_id']] != 1]
    if missing or ambiguous or invalid:
        report = dict(session=str(day), planned_tickers=len(selected), snapshot_rows=len(rows),
                      missing_tickers=missing, ambiguous_tickers=ambiguous,
                      rejected_identity_rows=[r for r in rows if r['ticker'] in ambiguous or r in invalid],
                      excluded_rows=0)
        if diagnostic_directory is not None:
            from ..runtime import require_runtime, write_json
            write_json(require_runtime(diagnostic_directory) / 'population-identity-error.json', report)
        reasons = [f"{name} maps to {counts[name]} listing rows" for name in ambiguous[:10]]
        if missing:
            reasons.append('missing tickers: ' + ', '.join(missing[:10]))
        if invalid:
            reasons.append(f'{len(invalid)} empty or shared listing identities')
        raise ValueError('Population identity rejected: ' + '; '.join(reasons) +
                         f'. {len(selected):,} planned tickers, {len(rows):,} snapshot rows. '
                         'Repair the certified population or explicitly approve a research exclusion; no rows excluded.')
    return rows, {**saved, 'eligibility': eligibility}


def selection(source, day, ticker, stage):
    return sql.selection(source['build_id'], day, ticker, source['units'][str(day)][ticker][stage]['attempt_id'])


def verify_listing(c, source, day, ticker):
    for stage, table in TABLES.items():
        actual = query(c, f'SELECT count() AS n,uniqExact((resolution_ms,bucket_index)) AS unique_keys,'
            f'sum(cityHash64(tuple(*))) AS hash FROM arte.{table} WHERE {selection(source,day,ticker,stage)}')[0]
        saved = source['units'][str(day)][ticker][stage]
        if int(actual['n']) != saved['output_rows'] or int(actual['n']) != int(actual['unique_keys']) or str(actual['hash']) != saved['output_hash']:
            raise ValueError(f'{day} {ticker} {stage}: persisted output differs from certificate')


def frame(c, statement, schema):
    return pl.read_csv(StringIO(c.execute(statement + ' FORMAT CSVWithNames')), schema_overrides=schema, null_values='\\N')


def inputs(c, source, day, ticker):
    # Only the 100 ms extrema and 1 s features/indicators cross the wire.
    end = f"toInt64({sql.bounds(day)})+(toInt64(bucket_index)+1)*toInt64(resolution_ms)*1000"
    bars = frame(c, f'SELECT {end} AS time_us,resolution_ms,low_int/10000. AS low,high_int/10000. AS high,'
        f'close_int/10000. AS close,price_valid,volume,trade_count,extremes_valid FROM arte.bars_v1 WHERE {selection(source,day,ticker,"bars")} '
        'AND resolution_ms IN (100,1000) ORDER BY resolution_ms,bucket_index',
        {'time_us':pl.Int64, 'resolution_ms':pl.Int64, 'low':pl.Float64, 'high':pl.Float64,
         'close':pl.Float64, 'price_valid':pl.Int64,
         'volume':pl.Float64, 'trade_count':pl.Int64, 'extremes_valid':pl.Int64})
    indicators = frame(c, f'SELECT {end} AS time_us,macd_line,macd_signal FROM arte.indicators_v1 '
        f'WHERE {selection(source,day,ticker,"technical")} AND resolution_ms=1000 ORDER BY bucket_index',
        {'time_us':pl.Int64, 'macd_line':pl.Float64, 'macd_signal':pl.Float64})
    return bars, indicators


def reader(threads=2):
    # Dense 100 ms bars may exceed the legacy reader's 200,000-row limit.
    return ArteReader(threads)
