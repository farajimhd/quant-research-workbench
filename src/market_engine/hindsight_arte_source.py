"""Read certified market-day products. Never reconstruct events or call QMD."""
from datetime import datetime, time, timezone
from contextlib import closing
from io import StringIO
import json
from pathlib import Path
import sqlite3
from uuid import UUID

import polars as pl

from pipelines.market_sip.events import market_day_sql as sql
from src.market_engine.hindsight_phase1 import NY, digest
from src.market_engine.hindsight_phase1_source import client, query

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


def population(c, source, day):
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
    selected = set(source['units'][str(day)])
    rows = [r for r in members if r['ticker'] in selected]
    if len(rows) != len(selected) or len({r['listing_id'] for r in rows}) != len(rows) or any(not r['listing_id'] for r in rows):
        raise ValueError('Ambiguous or missing ticker/listing identity in build population')
    return rows, saved


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
    c = client(threads)
    c.default_query_params['max_result_rows'] = 700000
    c.default_query_params['max_query_size'] = 4000000
    return c
