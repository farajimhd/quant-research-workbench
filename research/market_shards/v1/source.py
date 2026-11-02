"""Frozen ARTE producer receipts and SELECT-only bounded transport."""
from pathlib import Path
from datetime import datetime, timezone
import json
import math
import os
import sqlite3
from .contract import CONTRACT, digest, sha, write_json, origin_us
from .sql import literal
from research.mlops.clickhouse import ClickHouseHttpClient


def implementation():
    return {name:sha(Path(__file__).parent/name) for name in ('contract.py','source.py','sql.py','build.py','transport.py','data.py')}


def client(endpoint, env_file, threads=1, memory_gib=2):
    values = {}
    for line in Path(env_file).read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    def pick(keys):
        return next((os.environ.get(k) or values.get(k) for k in keys if os.environ.get(k) or values.get(k)), '')
    return ClickHouseHttpClient(endpoint,
        pick(('QMD_CLICKHOUSE_USER', 'REAL_LIVE_CLICKHOUSE_WRITE_USER', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_USER')),
        pick(('QMD_CLICKHOUSE_PASSWORD', 'REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD', 'CLICKHOUSE_WORKSTATION_PASSWORD', 'CLICKHOUSE_PASSWORD')),
        timeout_seconds=330, persistent=True, max_persistent_idle_seconds=2,
        default_query_params=dict(readonly=1, max_threads=threads, max_execution_time=300,
            max_memory_usage=int(memory_gib*2**30), max_block_size=65536,
            max_query_size=2**20,max_ast_elements=100000,
            output_format_arrow_low_cardinality_as_dictionary=0))


def query(c, sql):
    return [json.loads(line) for line in c.execute(sql+' FORMAT JSONEachRow').splitlines()]


def price_factor(splits, ticker, previous, day):
    cutoff = origin_us(day)+14400_000000
    ratios = {}
    for r in splits:
        if r['provider_ticker'] != ticker or not previous < r['execution_date'] <= day:
            continue
        known = datetime.fromisoformat(r['inserted_at'].replace('Z', '+00:00'))
        if known.tzinfo is None:
            known = known.replace(tzinfo=timezone.utc)
        if known.timestamp()*1e6 > cutoff:
            continue
        a, b = float(r['split_from']), float(r['split_to'])
        if not (math.isfinite(a) and math.isfinite(b) and a > 0 and b > 0):
            raise ValueError('Invalid source split ratio')
        factor = a/b
        if r['execution_date'] in ratios and ratios[r['execution_date']] != factor:
            raise ValueError('Conflicting opening-known split ratios')
        ratios[r['execution_date']] = factor
    return math.prod(ratios.values())


def freeze(c, root, bars_root, quotes_root, start, end=None):
    """Import metadata only. Market rows are exclusively read from ARTE."""
    seal = root/'source.json'
    if seal.exists():
        saved = json.loads(seal.read_text())
        if saved.get('hash') != digest({k:v for k,v in saved.items() if k!='hash'}):
            raise ValueError('Frozen source hash differs')
        if saved.get('implementation') != implementation():
            raise ValueError('Generator implementation changed; use a new dataset root')
        if saved['contract'] != json.loads(json.dumps(CONTRACT)) or saved['requested_start'] != start or saved['requested_end'] != end:
            raise ValueError('Frozen source/configuration differs; use a new dataset root')
        for day, checksum in saved['unit_files'].items():
            if sha(root/'units'/f'{day}.json') != checksum:
                raise ValueError('Frozen source receipt changed')
        return saved
    storage = query(c, "SELECT table,groupUniqArray(disk_name) disks FROM system.parts WHERE active AND database='arte' AND table IN ('bars_v1','quote_bars_v1','liquidity_100ms_v1','daily_session_bars_by_symbol_time_v1') GROUP BY table")
    if len(storage) != 4 or any(r['disks'] != ['live_market_ssd'] for r in storage):
        raise ValueError('Required ARTE tables absent or misplaced')
    path = Path(bars_root)/'build/latest.json'
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if raw['status'] != 'core_complete':
        raise ValueError('Trade source campaign is incomplete')
    build = raw['build_id']; plan = raw['definition']['plan']
    if plan['population_evidence']['historical_membership_certified']:
        raise ValueError('Unexpected source identity policy')
    days = sorted(plan['sessions'])
    import pandas_market_calendars as mcal
    calendar=mcal.get_calendar('XNYS').schedule(start_date=days[0],end_date=days[-1])
    regular={str(index.date()):[(int(row[col].timestamp()*1e6)-origin_us(str(index.date())))//1000000 for col in ('market_open','market_close')] for index,row in calendar.iterrows()}
    if not set(days)<=set(regular):raise ValueError('Source sessions disagree with exchange calendar')
    expected = {}
    for unit in plan['units']:
        expected[unit['source_date']] = expected.get(unit['source_date'], 0)+1
    population = {r['session_date']: r for r in plan['population']}
    splits, previous = plan['splits'], plan['predecessors']
    del plan['units']
    source_hash = sha(path)
    quote_request = json.loads((Path(quotes_root)/'quote-request.json').read_text())
    if quote_request['parent_build'] != build:
        raise ValueError('Quote/trade source build mismatch')
    quote_build = quote_request['policy_hash']
    qcounts = {r['day']: int(r['n']) for r in query(c,
        f"SELECT toString(session_date) day,count() n FROM arte.quote_bars_coverage_v1 FINAL WHERE build_id={literal(quote_build)} GROUP BY session_date")}
    daily_receipts = query(c, "SELECT toString(chunk_start) day,unit_id,build_version,feature_version,output_row_count,status FROM arte.daily_session_bars_manifest_v1 FINAL WHERE artifact_name='daily_session_bars_by_symbol_time_v1'")
    daily = {}
    for row in daily_receipts:
        if row['day'] in daily:
            raise ValueError('Ambiguous daily source manifest')
        daily[row['day']] = row
    requested = [d for d in days if d >= start and (end is None or d <= end)]
    if not requested:
        raise ValueError('No source sessions in requested interval')
    unit_files = {}; blocked = {}
    ledger = Path(bars_root)/'build-ledger-v2.sqlite3'
    uri = 'file:'+('//' if ledger.as_posix().startswith('//') else '')+ledger.as_posix()+'?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        for day in days:
            if day > requested[-1]:
                break
            rows = db.execute("SELECT ticker,stage,attempt_id,source_hash,output_rows,output_hash FROM units WHERE build_id=? AND session_date=? AND stage IN ('bars','broker_100ms') AND status='complete' ORDER BY ticker,stage", (build, day)).fetchall()
            units = {}
            for ticker, stage, attempt, source, count, content in rows:
                units.setdefault(ticker, {})[stage] = dict(attempt_id=attempt, source_hash=source, rows=count, content_hash=content)
            if len(units) != expected[day] or any(set(u) != {'bars', 'broker_100ms'} for u in units.values()):
                raise ValueError(f'Incomplete trade/execution coverage: {day}')
            if qcounts.get(day) != expected[day] or daily.get(day, {}).get('status') != 'complete':
                raise ValueError(f'Incomplete quote/daily coverage: {day}')
            quote_rows=query(c,f"SELECT ticker,toString(attempt_id) attempt_id,source_hash,content_hash,row_count FROM arte.quote_bars_coverage_v1 FINAL WHERE build_id={literal(quote_build)} AND session_date=toDate({literal(day)}) ORDER BY ticker")
            if {r['ticker'] for r in quote_rows} != set(units):
                raise ValueError('Trade/quote membership mismatch: '+day)
            for row in quote_rows:
                units[row['ticker']]['quote']=dict(attempt_id=row['attempt_id'],source_hash=row['source_hash'],
                    content_hash=row['content_hash'],rows=int(row['row_count']))
            population_day = population[day]
            if population_day['certificate']['status'] != 'approved_retrospective':
                raise ValueError('Unsupported membership certificate')
            unit_path = root/'units'/f'{day}.json'
            write_json(unit_path, dict(day=day, units=units, population=population_day, daily=daily[day]))
            unit_files[day] = sha(unit_path)
    for day in requested:
        if previous.get(day) not in unit_files:
            blocked[day] = 'Previous regular-session ARTE close unavailable'
    record = dict(contract=CONTRACT, implementation=implementation(), requested_start=start, requested_end=end,
        source_manifest_sha256=source_hash, source_build=build, quote_build=quote_build,
        quote_request_sha256=sha(Path(quotes_root)/'quote-request.json'),
        sessions=requested, context_sessions=sorted(unit_files), predecessors=previous,
        splits=splits, regular=regular, blocked=blocked, unit_files=unit_files, storage=storage)
    record['hash'] = digest(record)
    write_json(seal, record)
    return json.loads(seal.read_text())


def units(root, day):
    return json.loads((root/'units'/f'{day}.json').read_text())['units']


def attempts(root, day, tickers, stage='bars'):
    available = units(root, day)
    missing = sorted(set(tickers)-available.keys())
    if missing:
        raise ValueError(f'Missing source membership {day}: {missing[:5]}')
    return {t: available[t][stage]['attempt_id'] for t in sorted(tickers)}
