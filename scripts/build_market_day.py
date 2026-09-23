#!/usr/bin/env python3
"""Build compact market bars and core indicators inside ClickHouse.

--date is one New York date; --start-date/--end-date are inclusive. Only
calendar sessions are built. Seven calendar days of bar dependencies are
automatically included. Never starts a Backtest or changes its consumers.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.market_sip.events import market_day_sql as sql
from research.mlops.clickhouse import ClickHouseHttpClient

RUNTIME = Path("D:/TradingML/runtimes")
DEFAULT_ENV = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets\.env")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def date_range(args):
    if args.date:
        if args.start_date or args.end_date:
            raise ValueError("Use --date OR both --start-date and --end-date")
        start = end = date.fromisoformat(args.date)
    else:
        if not args.start_date or not args.end_date:
            raise ValueError("Provide --date or both inclusive range endpoints")
        start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    return start, end


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date")
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--tickers", default="", help="Comma-separated symbols; default is dated-tradable tickers with certified events")
    p.add_argument("--database", default="q_market_history")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.add_argument("--runtime", type=Path, default=RUNTIME / "market-day")
    p.add_argument("--max-threads", type=int, default=4)
    p.add_argument("--workers", type=int, default=4, help="Concurrent ticker workers (1-8); each uses its own bounded ClickHouse client")
    p.add_argument("--max-memory-gb", type=float, default=2.)
    p.add_argument("--query-timeout", type=int, default=600)
    p.add_argument("--max-plan-units", type=int, default=100000,
        help="Bound in-memory ticker-day metadata; split larger ranges or raise explicitly")
    p.add_argument("--plan-only", action="store_true", help="Read-only coverage/storage preflight; no table creation")
    p.add_argument("--rebuild", action="store_true", help="New immutable build ID; preserve previous builds")
    p.add_argument("--build-id", help="Resume an explicit build ID printed by an earlier --rebuild")
    p.add_argument("--progress", choices=("auto", "text"), default="auto")
    args = p.parse_args(argv)
    try:
        args.start, args.end = date_range(args)
        sql.identifier(args.database)
        if args.rebuild and args.build_id:
            raise ValueError("Use --rebuild OR --build-id")
        if args.build_id and (len(args.build_id)>100 or any(c not in '0123456789abcdef-' for c in args.build_id)):
            raise ValueError("Invalid build ID")
        if args.max_threads < 1 or not 1 <= args.workers <= 8 or not 0 < args.max_memory_gb <= 64 or args.query_timeout < 1 or args.max_plan_units<1:
            raise ValueError("Invalid query resource limits")
        args.symbols = sorted(set(x.strip().upper() for x in args.tickers.split(",") if x.strip()))
        if any(len(x) > 32 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in x) for x in args.symbols):
            raise ValueError("Invalid ticker")
    except ValueError as error:
        p.error(str(error))
    return args


class Client:
    """Bounded HTTP results, no write retries, explicit cancellation identity."""
    def __init__(self, args):
        values = {}
        if args.env_file.is_file():
            for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
        def pick(keys, default=""):
            return next((os.environ.get(k) or values.get(k) for k in keys if os.environ.get(k) or values.get(k)), default)
        endpoint = pick(("QMD_CLICKHOUSE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "CLICKHOUSE_URL", "CLICKHOUSE_ENDPOINT"))
        if not endpoint:
            raise ValueError("Missing ClickHouse endpoint; supply --env-file or QMD_CLICKHOUSE_URL")
        user = pick(("QMD_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "CLICKHOUSE_WORKSTATION_USER", "CLICKHOUSE_USER"), "default")
        password = pick(("QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD", "CLICKHOUSE_PASSWORD"))
        self.secrets = (endpoint, password)
        self.http = ClickHouseHttpClient(endpoint, user, password, timeout_seconds=args.query_timeout + 30,
            default_query_params=dict(max_threads=args.max_threads, max_insert_threads=1,
                max_memory_usage=int(args.max_memory_gb * 1024**3), max_execution_time=args.query_timeout,
                max_result_rows=100000, max_result_bytes=16000000, result_overflow_mode="throw"))
        self.active = None
        self.profiles = []
        self.profile_totals = {}

    def query(self, query, label="query", read=True):
        query_id = "market-day-" + uuid.uuid4().hex
        self.active = query_id
        start = time.monotonic()
        try:
            result = self.http.execute(query + (" FORMAT JSONEachRow" if read else ""), query_id=query_id)
            return [json.loads(line) for line in result.splitlines()] if read else []
        except BaseException:
            # Stop our query before permitting a retry with a fresh attempt ID.
            self.cancel()
            raise
        finally:
            self.active = None
            elapsed = time.monotonic()-start
            total = self.profile_totals.setdefault(label,dict(count=0,seconds=0.,max_seconds=0.))
            total['count'] += 1
            total['seconds'] += elapsed
            total['max_seconds'] = max(total['max_seconds'],elapsed)
            self.profiles.append(dict(query_id=query_id,label=label,seconds=elapsed))
            if len(self.profiles)>500:
                del self.profiles[:len(self.profiles)-500]

    def cancel(self):
        if self.active:
            self.http.execute("KILL QUERY WHERE query_id=" + sql.literal(self.active) + " SYNC")

    def clean_error(self, error):
        message = str(error)
        for secret in self.secrets:
            if secret:
                message = message.replace(secret, "[redacted]")
        return message[:3000]


class Progress:
    """One durable unit is one published ticker-day stage, not one SQL query."""
    def __init__(self, bars_total, technical_total, workers, mode="auto"):
        self.bars_total, self.technical_total = bars_total, technical_total
        self.workers = workers
        self.completed = self.skipped = self.failed = self.retried = 0
        self.bars_done = self.technical_done = 0
        self.active = {}
        self.current = "building"
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.last_text = 0.
        self.stop = threading.Event()
        self.thread = None
        self.live = None
        if mode == "auto" and sys.stdout.isatty():
            try:
                from rich.live import Live
            except ImportError:
                pass
            else:
                self.live = Live(self.render(), refresh_per_second=2)

    def render(self):
        with self.lock:
            total = self.bars_total + self.technical_total
            done = self.completed + self.skipped
            active = list(self.active.values())
            queued = max(0, total-done-self.failed-len(active))
            lines = [f"Market day {self.current}  |  workers {self.workers}  |  live_market_ssd",
                f"Bars {self.bars_done}/{self.bars_total}  |  Technical {self.technical_done}/{self.technical_total}",
                f"Done {done}/{total}  active {len(active)}  queued {queued}  "
                f"failed {self.failed}  elapsed {time.monotonic()-self.started:.0f}s"]
            lines.extend(f"  {item}" for item in active[:self.workers])
            if not active and self.current == 'building':
                lines.append('  Waiting for next ticker or final certification')
        if self.live:
            from rich.panel import Panel
            return Panel('\n'.join(lines), title='ClickHouse market-day build', expand=False)
        return '\n'.join(lines)

    def __enter__(self):
        if self.live:
            self.live.start()
            def refresh():
                while not self.stop.wait(.5):
                    self.live.update(self.render())
            self.thread = threading.Thread(target=refresh, daemon=True)
            self.thread.start()
        return self

    def update(self, ticker, day, stage):
        with self.lock:
            self.active[ticker] = f"{day}  {ticker}  {stage}"
        self._text_snapshot()

    def finish(self, ticker, kind, skipped=False):
        with self.lock:
            self.active.pop(ticker, None)
            self.skipped += int(skipped)
            self.completed += int(not skipped)
            if kind == 'bars': self.bars_done += 1
            else: self.technical_done += 1
        self._text_snapshot(force=True)

    def _text_snapshot(self, force=False):
        if self.live: return
        now = time.monotonic()
        if force and now-self.last_text < 10: return
        if not force and now-self.last_text < 10: return
        self.last_text = now
        print(self.render(), flush=True)

    def __exit__(self, error_type, *_):
        if error_type:
            self.current = 'interrupted' if issubclass(error_type,KeyboardInterrupt) else 'failed'
            self.failed += int(self.current=='failed')
        self.stop.set()
        if self.thread: self.thread.join()
        if self.live:
            self.live.update(self.render())
            self.live.stop()
        else:
            print(self.render(),flush=True)


def storage_preflight(client, db, require_tables=False):
    policies = client.query("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'", "storage_policy")
    if not policies or any(row["disks"] != ["live_market_ssd"] for row in policies):
        raise ValueError("Required SSD-only live_market_ssd policy is unavailable")
    universe = client.query("SELECT storage_policy FROM system.tables WHERE database='q_live' AND name='feature_tradable_universe_v1'", "population_policy")
    if universe != [{'storage_policy':sql.POLICY}]:
        raise ValueError("Dated tradable universe is absent or not on live_market_ssd")
    misplaced_universe = client.query("SELECT disk_name FROM system.parts WHERE active AND database='q_live' AND table='feature_tradable_universe_v1' AND disk_name!='live_market_ssd' LIMIT 1", "population_parts")
    if misplaced_universe:
        raise ValueError("Dated tradable universe has parts outside live_market_ssd")
    rows = client.query(f"SELECT name,storage_policy FROM system.tables WHERE database={sql.literal(db)} AND startsWith(name,'market_day_')", "table_policies")
    if any(row["storage_policy"] != sql.POLICY for row in rows):
        raise ValueError("Existing market-day table has an incorrect storage policy; explicit migration required")
    if require_tables and len(rows) != 5:
        raise ValueError("Market-day table schema is incomplete")
    if rows:
        columns=client.query(f"SELECT table,name,type FROM system.columns WHERE database={sql.literal(db)} AND startsWith(table,'market_day_') ORDER BY table,position",'schema_contract')
        actual={r['name']:[] for r in rows}
        for column in columns:
            actual[column['table']].append((column['name'],column['type'].replace(' ','')))
        for statement in sql.ddl(db):
            name=statement.split(' (',1)[0].split('.')[-1]
            body=statement.split(' (',1)[1].split('ENGINE=',1)[0]
            expected=re.findall(r"(?:^|,)\s*(\w+)\s+(LowCardinality\(String\)|DateTime64\(6,'UTC'\)|[A-Za-z]+[0-9]*)",body)
            if name in actual and actual[name]!=expected:
                raise ValueError('Existing table does not match the versioned schema: '+name)
    wrong = client.query(f"SELECT table,disk_name,count() AS parts FROM system.parts WHERE active AND database={sql.literal(db)} AND startsWith(table,'market_day_') AND disk_name!='live_market_ssd' GROUP BY table,disk_name", "part_placement")
    if wrong:
        raise ValueError("Market-day parts are not on live_market_ssd; explicit migration required")


def source_plan(client, args):
    import pandas_market_calendars as mcal
    first = args.start - timedelta(days=sql.WARMUP_DAYS)
    sessions = [stamp.date() for stamp in mcal.get_calendar("XNYS").schedule(start_date=first, end_date=args.end).index]
    requested = [day for day in sessions if day >= args.start]
    if not requested:
        raise ValueError("Requested range contains no market sessions")
    restriction = " AND ticker IN (" + ','.join(map(sql.literal,args.symbols)) + ")" if args.symbols else ""
    span = f"source_date BETWEEN {sql.literal(first)} AND {sql.literal(args.end)}"
    stats = client.query(f"SELECT source_date,stats_version,source_filter_key,total_event_rows_after_filters,updated_at FROM market_sip_compact.events_source_day_stats FINAL WHERE {span} ORDER BY source_date", "source_certificates")
    by_day = {row['source_date']: row for row in stats}
    if len(by_day) != len(stats) or any(str(day) not in by_day for day in sessions):
        raise ValueError("Missing or ambiguous canonical day coverage, including seven-day warm-up")
    totals = client.query(f"SELECT source_date,sum(event_count) AS n FROM market_sip_compact.events_ordinal_continuity FINAL WHERE {span} GROUP BY source_date ORDER BY source_date", "day_coverage_totals")
    counts = {r['source_date']:int(r['n']) for r in totals}
    if any(counts.get(str(day))!=int(by_day[str(day)]['total_event_rows_after_filters']) for day in sessions):
        raise ValueError("Canonical day statistics and ticker continuity totals disagree")
    populations = []
    tradable_by_day = {}
    for day in sessions:
        members = client.query(f"SELECT ticker,symbol_id,listing_id,security_id,source_run_id,inserted_at "
            f"FROM q_live.feature_tradable_universe_v1 FINAL WHERE universe_date={sql.literal(day)} "
            "AND is_tradable=1 ORDER BY ticker,symbol_id,listing_id", "dated_tradable_universe")
        if not members or any(not row['ticker'] for row in members):
            raise ValueError(f"Missing or invalid dated tradable universe for {day}; current membership is not a historical substitute")
        tickers = {row['ticker'] for row in members}
        tradable_by_day[str(day)] = tickers
        populations.append(dict(session_date=str(day),authority='q_live.feature_tradable_universe_v1',
            tradable_tickers=len(tickers),snapshot_rows=len(members),snapshot_hash=digest(members)))
    coverage = []
    for day, population in zip(sessions, populations):
        batch=client.query(f"SELECT source_date,ticker,event_count,next_ordinal,last_ordinal,first_sip_timestamp_us,last_sip_timestamp_us,build_step,updated_at FROM market_sip_compact.events_ordinal_continuity FINAL WHERE source_date={sql.literal(day)}{restriction} ORDER BY ticker", "ticker_coverage")
        selected=[row for row in batch if row['ticker'] in tradable_by_day[str(day)]]
        if args.symbols and day in requested:
            absent=set(args.symbols)-{row['ticker'] for row in selected}
            if absent:
                raise ValueError(f"Requested tickers lack dated tradability or canonical events on {day}: "+','.join(sorted(absent)))
        if not args.symbols and day in requested and not selected:
            raise ValueError(f"No tradable tickers with certified events on {day}")
        population['selected_ticker_days']=len(selected)
        population['excluded_canonical_tickers']=len(batch)-len(selected) if not args.symbols else None
        population['tradable_without_canonical_events']=(
            len(tradable_by_day[str(day)]-{row['ticker'] for row in batch}) if not args.symbols else None)
        if len(coverage)+len(selected)>args.max_plan_units:
            raise ValueError('Plan exceeds --max-plan-units; split the date range or explicitly raise its metadata limit')
        coverage.extend(selected)
    if not coverage:
        raise ValueError("No canonical ticker coverage")
    rules = client.query("SELECT token_id,modifier_int,update_high_low,update_last,update_volume FROM market_sip_compact.event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 ORDER BY token_id", "trade_rules")
    if not rules or len({r['token_id'] for r in rules}) != len(rules):
        raise ValueError("Missing or ambiguous trade condition rules")
    present = {row['ticker'] for row in coverage}
    units = [row for row in coverage if date.fromisoformat(row['source_date']) in sessions]
    if len({(r['source_date'],r['ticker']) for r in units}) != len(units):
        raise ValueError("Ambiguous ticker-day continuity")
    splits=client.query(f"SELECT provider_ticker,execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE execution_date BETWEEN {sql.literal(first)} AND {sql.literal(args.end)} ORDER BY provider_ticker,execution_date,inserted_at",'split_references')
    actions={}
    for row in splits:
        if row['provider_ticker'] not in present:
            continue
        key=(row['provider_ticker'],row['execution_date'])
        ratio=(float(row['split_from']),float(row['split_to']))
        if not all(math.isfinite(v) and v>0 for v in ratio):
            raise ValueError('Invalid corporate-action split ratio')
        if key in actions and ratio!=(float(actions[key]['split_from']),float(actions[key]['split_to'])):
            raise ValueError('Conflicting corporate-action split ratios')
        actions[key]=row
    return dict(sessions=list(map(str,sessions)), requested=list(map(str,requested)), stats=stats,
        population=populations,units=units,rules=rules,
        splits=list(actions.values()),
        excluded_calendar_dates=[str(first+timedelta(days=i)) for i in range((args.end-first).days+1) if first+timedelta(days=i) not in sessions])


def source_evidence(client, row):
    day = date.fromisoformat(row['source_date'])
    years = '|'.join(map(str,sorted({day.year,(day+timedelta(days=1)).year})))
    query = f"""SELECT count() AS n,uniqExact(ordinal) AS unique_ordinals,min(ordinal) AS first_ordinal,
      max(ordinal) AS last_ordinal,min(sip_timestamp_us) AS first_us,max(sip_timestamp_us) AS last_us,
      countIf(sip_timestamp_us>={sql.bounds(day,'04:00:00')} AND sip_timestamp_us<{sql.bounds(day,'20:00:00')}) AS session_events,
      countIf(bitAnd(event_meta,1)=1 AND bitAnd(event_meta,{sql.DELAYED})!=0
        AND sip_timestamp_us>={sql.bounds(day,'04:00:00')}
        AND sip_timestamp_us<{sql.bounds(day,'20:00:00')}) AS reporting_delayed_trades,
      sum(cityHash64(tuple(*))) AS hash
      FROM merge('market_sip_compact','^events_({years})$')
      WHERE ticker={sql.literal(row['ticker'])} AND event_date BETWEEN toDate({sql.literal(day)}) AND toDate({sql.literal(day+timedelta(days=1))})
      AND sip_timestamp_us>={sql.bounds(day)} AND sip_timestamp_us<{sql.bounds(day+timedelta(days=1))}"""
    result = client.query(query, "source_integrity")[0]
    n = int(row['event_count'])
    if (int(result['n']) != n or int(result['unique_ordinals']) != n or
        (n and (int(result['last_ordinal']) != int(row['last_ordinal']) or
          int(result['first_ordinal']) != int(row['next_ordinal'])-n or
          int(result['first_us']) != int(row['first_sip_timestamp_us']) or
          int(result['last_us']) != int(row['last_sip_timestamp_us'])))):
        raise ValueError(f"Canonical continuity mismatch for {day} {row['ticker']}")
    return result


def evidence(client, db, kind, build, day, ticker, attempt):
    key = "(sip_timestamp_us,ordinal)" if kind == 'events' else "(resolution_ms,bucket_index)"
    return client.query(f"SELECT count() AS n,uniqExact({key}) AS unique_keys,sum(cityHash64(tuple(*))) AS hash FROM {sql.table(db,kind)} WHERE {sql.selection(build,day,ticker,attempt)}", kind+"_integrity")[0]


def publish(client, db, build, day, ticker, stage, attempt, source_hash, result):
    client.query(f"INSERT INTO {sql.table(db,'units')} VALUES ({sql.literal(build)},toDate({sql.literal(day)}),{sql.literal(ticker)},{sql.literal(stage)},toUUID({sql.literal(attempt)}),{sql.literal(source_hash)},{int(result['n'])},{int(result['hash'])},'complete',now64(6))", "publish_"+stage, False)


def completed(client, db, build, day, ticker, stage, source_hash):
    rows = client.query(f"SELECT attempt_id,source_hash,output_rows,output_hash FROM {sql.table(db,'units')} FINAL WHERE {sql.selection(build,day,ticker)} AND stage={sql.literal(stage)} AND status='complete'", "resume")
    if not rows:
        return None
    row = rows[0]
    if row['source_hash'] != source_hash:
        raise ValueError("Published dependency changed; use --rebuild")
    result = evidence(client,db,stage,build,day,ticker,row['attempt_id'])
    if int(result['n']) != int(row['output_rows']) or int(result['hash']) != int(row['output_hash']) or result['n'] != result['unique_keys']:
        raise ValueError("Published output integrity failed; explicit rebuild required")
    return row


def validate_bars(client, db, build, day, ticker, attempt):
    where = sql.selection(build,day,ticker,attempt)
    rows = client.query(f"SELECT resolution_ms,sum(volume) AS volume,sum(trade_count) AS trades,sum(notional) AS notional,sum(execution_volume) AS execution_volume,sum(execution_notional) AS execution_notional FROM {sql.table(db,'bars')} WHERE {where} GROUP BY resolution_ms ORDER BY resolution_ms", "rollup_conservation")
    if rows:
        if tuple(int(r['resolution_ms']) for r in rows) != sql.FRAMES:
            raise ValueError("Missing bar resolutions")
        for row in rows[1:]:
            for key in ('volume','trades','notional','execution_volume','execution_notional'):
                if abs(float(row[key])-float(rows[0][key])) > 1e-9*max(1.,abs(float(rows[0][key]))):
                    raise ValueError("Rollup conservation failed: "+key)
    bad = client.query(f"SELECT count() AS n FROM {sql.table(db,'bars')} WHERE {where} AND ((price_valid AND (open_int=0 OR close_int=0)) OR (extremes_valid AND (low_int=0 OR high_int<low_int)) OR NOT isFinite(volume) OR volume<0)", "bar_validity")[0]
    if int(bad['n']):
        raise ValueError("Invalid bar values")
    # Compare hierarchical outputs to direct 100ms reductions, including OHLC.
    mismatch = client.query(f"""SELECT count() AS n FROM (
      SELECT toUInt32(target) AS resolution_ms,toUInt32(intDiv(toUInt64(bucket_index)*100,target)) AS bucket,
        argMinIf(open_int,bucket_index,price_valid) AS o,maxIf(high_int,extremes_valid) AS h,
        minIf(low_int,extremes_valid) AS l,argMaxIf(close_int,bucket_index,price_valid) AS c
      FROM {sql.table(db,'bars')} AS base ARRAY JOIN {list(sql.FRAMES[1:])} AS target
      WHERE {where} AND base.resolution_ms=100 GROUP BY target,bucket
    ) a FULL OUTER JOIN (SELECT resolution_ms,bucket_index,open_int,high_int,low_int,close_int
      FROM {sql.table(db,'bars')} WHERE {where} AND resolution_ms!=100) b
      ON a.resolution_ms=b.resolution_ms AND a.bucket=b.bucket_index
      WHERE a.resolution_ms!=b.resolution_ms OR a.bucket!=b.bucket_index OR (a.o,a.h,a.l,a.c)!=(b.open_int,b.high_int,b.low_int,b.close_int)""", "direct_rollup_parity")[0]
    if int(mismatch['n']):
        raise ValueError("Direct/hierarchical rollup parity failed")


def validate_events(client,db,build,day,ticker,attempt,source):
    where=sql.selection(build,day,ticker,attempt)
    metrics=client.query(f"""SELECT count() AS events,countIf(kind=1) AS trades,
      countIf(kind=1 AND NOT volume_valid) AS volume_ineligible_trades,
      countIf(kind=1 AND NOT price_valid) AS price_ineligible_trades,
      countIf(kind=1 AND volume_valid AND NOT execution_valid) AS execution_ineligible_trades,
      countIf(kind=1 AND sip_timestamp_us<{sql.bounds(day,'04:05:00')}) AS pre_0405_trades,
      countIf(kind=1 AND sip_timestamp_us<{sql.bounds(day,'04:05:00')} AND volume_valid=1) AS pre_0405_volume_eligible_trades,
      countIf(kind=1 AND (price_int=0 OR size<=0 OR NOT isFinite(size))) AS invalid_trade_values,
      sumIf(size,volume_valid) AS volume,sumIf(price_int/10000.*size,volume_valid) AS notional,
      countIf(volume_valid) AS trade_count,
      sumIf(size,execution_valid) AS execution_volume,
      sumIf(price_int/10000.*size,execution_valid) AS execution_notional
      FROM {sql.table(db,'events')} WHERE {where}""",'event_conservation')[0]
    if int(metrics['events'])!=int(source['session_events']):
        raise ValueError('Event stage lost or duplicated canonical session events')
    bars=client.query(f"SELECT sum(volume) AS volume,sum(notional) AS notional,sum(trade_count) AS trade_count,sum(execution_volume) AS execution_volume,sum(execution_notional) AS execution_notional FROM {sql.table(db,'bars')} WHERE {where} AND resolution_ms=100",'base_conservation')[0]
    for key,value in bars.items():
        if abs(float(value)-float(metrics[key]))>1e-9*max(1.,abs(float(metrics[key]))):
            raise ValueError('Event/base-bar conservation failed: '+key)
    metrics['outside_session_events']=int(source['n'])-int(source['session_events'])
    metrics['reporting_delayed_trades']=int(source['reporting_delayed_trades'])
    return metrics


def validate_technical(client,db,build,day,ticker,attempt):
    where=sql.selection(build,day,ticker,attempt)
    expressions=[f'NOT isFinite({name})' for name in [*(f'ema_{p}' for p in sql.EMAS),'macd_line','macd_signal','macd_histogram','rsi_14','atr_14']]
    invalid=client.query(f"SELECT count() AS n FROM {sql.table(db,'technical')} WHERE {where} AND ({' OR '.join(expressions)} OR rsi_14<0 OR rsi_14>100 OR atr_14<0)",'technical_validity')[0]
    if int(invalid['n']):
        raise ValueError('Invalid technical indicator values')
    expected=client.query(f"SELECT count() AS n,countIf(price_valid AND NOT extremes_valid) AS unsupported FROM {sql.table(db,'bars')} WHERE {sql.selection(build,day,ticker)} AND attempt_id=(SELECT attempt_id FROM {sql.table(db,'units')} FINAL WHERE {sql.selection(build,day,ticker)} AND stage='bars' AND status='complete') AND price_valid=1",'technical_coverage')[0]
    actual=evidence(client,db,'technical',build,day,ticker,attempt)
    if int(expected['unsupported']):
        raise ValueError('Price-bearing bars with unavailable extremes require an explicit indicator validity contract')
    if int(actual['n'])!=int(expected['n']):
        raise ValueError('Technical indicator coverage differs from eligible bars')
    return actual


@contextmanager
def build_lock(path):
    # OS byte lock is released after crashes; the persistent lock file is safe.
    import msvcrt
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise ValueError("Another controller owns this build runtime") from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def build_ticker(args, build, plan, ticker, rows, requested, report, report_path, unit_log,
                 checkpoint_clock,
                 state_lock, progress, stop, clients):
    """Own one ticker's chronological bars and then its requested technical days."""
    client = Client(args)
    with state_lock:
        clients.append(client)

    def mark(day, attempt, stage):
        with state_lock:
            report['active'][ticker] = dict(day=str(day),ticker=ticker,attempt=attempt,stage=stage)
            if time.monotonic()-checkpoint_clock[0]>=30:
                save(report_path,report)
                checkpoint_clock[0]=time.monotonic()
        progress.update(ticker,day,stage)

    try:
        for row in rows:
            if stop.is_set(): return client
            day = date.fromisoformat(row['source_date'])
            mark(day,'','source verification')
            source = source_evidence(client,row)
            source_hash = digest([source,plan['rules'],sql.VERSION])
            if completed(client,args.database,build,day,ticker,'bars',source_hash):
                if not completed(client,args.database,build,day,ticker,'events',source_hash):
                    raise ValueError(f'{day} {ticker}: published bars lack certified event indicators')
                progress.finish(ticker,'bars',skipped=True)
                continue
            attempt = str(uuid.uuid4())
            mark(day,attempt,'events / VWAP / NBBO')
            client.query(sql.events_sql(args.database,build,day,ticker,attempt,plan['rules']),"events",False)
            mark(day,attempt,'100ms bars')
            client.query(sql.base_sql(args.database,build,day,ticker,attempt),"100ms",False)
            mark(day,attempt,'fine / higher rollups')
            client.query(sql.rollup_sql(args.database,build,day,ticker,attempt,100,(1000,5000,10000,30000)),"fine_rollup",False)
            client.query(sql.rollup_sql(args.database,build,day,ticker,attempt,30000,(60000,300000,3600000)),"higher_rollup",False)
            validate_bars(client,args.database,build,day,ticker,attempt)
            metrics=validate_events(client,args.database,build,day,ticker,attempt,source)
            if source_evidence(client,row) != source:
                raise ValueError(f'{day} {ticker}: canonical source changed during build')
            for stage in ('events','bars'):
                result=evidence(client,args.database,stage,build,day,ticker,attempt)
                if result['n'] != result['unique_keys']:
                    raise ValueError(f'{day} {ticker}: duplicate {stage} output keys')
                publish(client,args.database,build,day,ticker,stage,attempt,source_hash,result)
            with state_lock:
                with unit_log.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(day=str(day),ticker=ticker,metrics=metrics),sort_keys=True)+'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
            progress.finish(ticker,'bars')

        for row in rows:
            if row['source_date'] not in requested or stop.is_set(): continue
            day = date.fromisoformat(row['source_date'])
            start = day-timedelta(days=sql.WARMUP_DAYS)
            mark(day,'','indicator dependencies')
            dependencies = client.query(f"SELECT session_date,attempt_id,source_hash,output_hash FROM {sql.table(args.database,'units')} FINAL WHERE build_id={sql.literal(build)} AND ticker={sql.literal(ticker)} AND stage='bars' AND status='complete' AND session_date BETWEEN {sql.literal(start)} AND {sql.literal(day)} ORDER BY session_date", "indicator_dependencies")
            dependency_hash = digest(dependencies)
            if completed(client,args.database,build,day,ticker,'technical',dependency_hash):
                progress.finish(ticker,'technical',skipped=True)
                continue
            attempt = str(uuid.uuid4())
            mark(day,attempt,'EMA / MACD / RSI / ATR')
            client.query(sql.technical_sql(args.database,build,day,ticker,attempt,start,plan['splits']),"technical",False)
            result = validate_technical(client,args.database,build,day,ticker,attempt)
            if result['n'] != result['unique_keys']:
                raise ValueError(f'{day} {ticker}: duplicate indicator keys')
            publish(client,args.database,build,day,ticker,'technical',attempt,dependency_hash,result)
            progress.finish(ticker,'technical')
        return client
    finally:
        with state_lock:
            report['active'].pop(ticker,None)


def run(args):
    runtime = args.runtime.resolve()
    if not RUNTIME.is_dir() or not runtime.is_relative_to(RUNTIME.resolve()):
        raise ValueError("Runtime must be beneath the available D:/TradingML/runtimes")
    runtime.mkdir(parents=True, exist_ok=True)
    client = Client(args)
    with build_lock(RUNTIME / ('market-day-'+args.database+'.lock')):
        report = dict(status="preflight", started_at=datetime.now(timezone.utc).isoformat(), profiles=[],run_id=uuid.uuid4().hex)
        report_path = runtime / 'latest.json'
        database_ready=False
        try:
            storage_preflight(client,args.database)
            plan = source_plan(client,args)
            definition = dict(version=sql.VERSION, emas=sql.EMAS, frames=sql.FRAMES, warmup_days=sql.WARMUP_DAYS,
                indicator_set='core', calculation_source=digest(Path(sql.__file__).read_text()),
                controller_source=digest(Path(__file__).read_text()), plan=plan, database=args.database,
                start=str(args.start),end=str(args.end),
                population_authority=dict(table='q_live.feature_tradable_universe_v1',
                    membership='same-date is_tradable=1 and certified canonical ticker events',
                    warmup='same-date tradable ticker-days only',missing_date='fail_closed'),
                indicators=dict(ema=dict(periods=sql.EMAS,seed='first eligible close',basis='completed nonempty bars'),
                    macd=dict(fast=12,slow=26,signal=9),rsi=dict(period=14,seed='first 14 changes',reset='session'),
                    atr=dict(period=14,seed='first 14 true ranges',reset='session'),
                    event=dict(fields=['execution_vwap','cumulative_volume','cumulative_notional','execution_volume','execution_notional','bid_int','ask_int','bid_size','ask_size','spread','nbbo_valid','quote_timestamp_us'],
                        reset='session',cursor=['sip_timestamp_us','ordinal'],max_quote_age_ms=1000)),
                price_scale=10000,session_timezone='America/New_York',session_hours=['04:00','20:00'],
                trade_eligibility=dict(session_start='04:00',excluded_reporting_flag=sql.DELAYED,
                    reporting_revision=sql.REPORTING_REVISION),storage_policy=sql.POLICY,
                controller_location=digest([str(runtime),os.environ.get('COMPUTERNAME','')]))
            build = digest(definition)
            if args.rebuild:
                build += '-' + uuid.uuid4().hex[:12]
            if args.build_id:
                saved_path=runtime / (args.build_id+'.json')
                if not saved_path.is_file() or json.loads(saved_path.read_text())['definition']!=json.loads(json.dumps(definition,default=str)):
                    raise ValueError('Explicit build ID has different definitions, source coverage, or runtime owner')
                build=args.build_id
            report.update(build_id=build, definition=definition, status='planned',unit_log=str(runtime / (build+'.units.jsonl')))
            existing=runtime / (build+'.json')
            save(report_path,report)
            selected=sum(row['selected_ticker_days'] for row in plan['population'] if row['session_date'] in plan['requested'])
            warmup=len(plan['units'])-selected
            scope=(f"Requested {args.start} through {args.end} inclusive | sessions {len(plan['requested'])} | "
                f"requested ticker-days {selected} | prerequisite warm-up ticker-days {warmup} "
                f"({plan['sessions'][0]} through {plan['sessions'][-1]})")
            if not args.symbols:
                excluded=sum(row['excluded_canonical_tickers'] for row in plan['population'] if row['session_date'] in plan['requested'])
                without_source=sum(row['tradable_without_canonical_events'] for row in plan['population'] if row['session_date'] in plan['requested'])
                scope+=f" | excluded non-tradable {excluded} | tradable without canonical events {without_source}"
            print(f"{scope} | policy {sql.POLICY} | workers {args.workers}",flush=True)
            if args.plan_only:
                print(f"Read-only plan complete: {len(plan['units'])} ticker-days including warm-up. {report_path}",flush=True)
                return 0
            client.query(f"CREATE DATABASE IF NOT EXISTS {sql.identifier(args.database)}", "database",False)
            for statement in sql.ddl(args.database):
                client.query(statement,"schema",False)
            storage_preflight(client,args.database,True)
            database_ready=True
            client.query(f"INSERT INTO {sql.table(args.database,'builds')} VALUES ({sql.literal(build)},toDate({sql.literal(args.end)}),{sql.literal(json.dumps(definition,sort_keys=True,default=str))},'building',now64(6))",'build_started',False)
            save(runtime / (build+'.json'),report)
            wanted = [r for r in plan['units'] if r['source_date'] in plan['requested']]
            by_ticker = {}
            for row in plan['units']:
                by_ticker.setdefault(row['ticker'],[]).append(row)
            for rows in by_ticker.values():
                rows.sort(key=lambda row:row['source_date'])
            state_lock = threading.Lock()
            stop = threading.Event()
            clients = []
            checkpoint_clock = [time.monotonic()]
            report['active'] = {}
            with Progress(len(plan['units']),len(wanted),min(args.workers,len(by_ticker)),args.progress) as progress:
                with ThreadPoolExecutor(max_workers=min(args.workers,len(by_ticker)),thread_name_prefix='market-day') as pool:
                    pending = {}
                    ticker_iter = iter(by_ticker.items())
                    def submit_next():
                        if stop.is_set(): return False
                        try: ticker, rows = next(ticker_iter)
                        except StopIteration: return False
                        future = pool.submit(build_ticker,args,build,plan,ticker,rows,set(plan['requested']),
                            report,report_path,runtime / (build+'.units.jsonl'),checkpoint_clock,
                            state_lock,progress,stop,clients)
                        pending[future] = ticker
                        return True
                    for _ in range(min(args.workers,len(by_ticker))): submit_next()
                    try:
                        while pending:
                            ready, _ = wait(pending,timeout=.5,return_when=FIRST_COMPLETED)
                            for future in ready:
                                ticker = pending.pop(future)
                                try: future.result()
                                except Exception as error:
                                    stop.set()
                                    raise RuntimeError(f'{ticker}: {error}') from error
                                submit_next()
                    except BaseException:
                        stop.set()
                        for worker_client in clients:
                            try: worker_client.cancel()
                            except Exception: pass
                        for future in pending:
                            future.cancel()
                        raise
                storage_preflight(client,args.database,True)
                if source_plan(client,args) != plan:
                    raise ValueError("Source certificates or rules changed during the build")
                progress.current='finished'
                report.update(status='core_complete',completed=progress.completed,skipped=progress.skipped,
                    backtest_consumer_cutover=False)
            return 0
        except KeyboardInterrupt:
            report['status'] = 'interrupted'
            print("Interrupted. Published units remain resumable; rerun the same command.",flush=True)
            return 130
        except Exception as error:
            report.update(status='failed',error=client.clean_error(error))
            print("Build failed: "+report['error'],file=sys.stderr,flush=True)
            return 1
        finally:
            if database_ready:
                try:
                    client.query(f"INSERT INTO {sql.table(args.database,'builds')} VALUES ({sql.literal(build)},toDate({sql.literal(args.end)}),{sql.literal(json.dumps(definition,sort_keys=True,default=str))},{sql.literal(report['status'])},now64(6))",'build_status',False)
                except Exception as error:
                    # A failed publication must never be reported as success.
                    report.update(status='publication_failed',error=client.clean_error(error))
                    save(report_path,report)
                    raise RuntimeError('Could not publish final build status; inspect latest.json') from None
            profiles=list(client.profiles)
            totals=dict(client.profile_totals)
            for worker_client in locals().get('clients',[]):
                profiles.extend(worker_client.profiles)
                for label, values in worker_client.profile_totals.items():
                    aggregate=totals.setdefault(label,dict(count=0,seconds=0.,max_seconds=0.))
                    aggregate['count']+=values['count']
                    aggregate['seconds']+=values['seconds']
                    aggregate['max_seconds']=max(aggregate['max_seconds'],values['max_seconds'])
            profiles=profiles[-1000:]
            try:
                ids=','.join(sql.literal(p['query_id']) for p in profiles)
                measurements=client.query(f"SELECT query_id,query_duration_ms,memory_usage,read_rows,read_bytes,written_rows,written_bytes FROM system.query_log WHERE event_date>=today()-1 AND type='QueryFinish' AND query_id IN ({ids})",'query_metrics') if ids else []
                measured={m['query_id']:m for m in measurements}
                for profile in profiles:
                    profile['server_metrics']=measured.get(profile['query_id'])
                report['metrics_note']='Missing server_metrics means the asynchronous query log has not published that query yet.'
            except Exception:
                report['metrics_note']='Server query metrics unavailable; client elapsed times retained.'
            report['profiles'] = profiles
            report['query_summary'] = totals
            save(report_path,report)
            runs=runtime / 'runs'
            runs.mkdir(exist_ok=True)
            save(runs / (report['run_id']+'.json'),report)
            if report.get('build_id') and not args.plan_only:
                save(runtime / (report['build_id']+'.json'),report)
            if report['status']=='core_complete':
                print(f"Core build complete: {build}\nManifest: {report_path}",flush=True)


def main(argv=None):
    return run(parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
