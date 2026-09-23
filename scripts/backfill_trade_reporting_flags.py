#!/usr/bin/env python3
"""Ingestion-owned, explicitly authorized v1 canonical reporting-flags migration.

Inclusive --start-date/--end-date; --plan-only writes nothing. Ctrl+C preserves
the map and coverage; rerun the same command to resume. No raw event is deleted.
"""
from __future__ import annotations
import argparse
import copy
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
from types import SimpleNamespace

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_market_day import Client, DEFAULT_ENV, Progress, digest
from pipelines.market_sip.events.market_day_sql import literal as lit
from pipelines.market_sip.events.trade_reporting_flags import REVISION
from pipelines.market_sip.flatfiles import download_update_events as ingest
from pipelines.market_sip.flatfiles.download_massive_sip_flatfiles import DownloadJob

DB = "q_live"
COVERAGE = DB + ".historical_trade_reporting_coverage_v1"
RUNTIME = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\trade-reporting-flags")
ARCHIVE = Path(r"\\DESKTOP-SAAI85T\Workstation-G\market-data\flatfiles\us_stocks_sip")
CACHE = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data\flatfiles\us_stocks_sip")
FIELDS = ["sip_timestamp_us", "price_primary_int", "price_secondary_int", "size_primary", "size_secondary", "exchange_primary", "exchange_secondary"] + [f"condition_token_{n}" for n in range(1,6)] + ["event_date"]


class ReportingProgress(Progress):
    def render(self):
        active=int(self.current not in ('finished','staged','failed','interrupted'))
        queued=max(0,self.total-self.completed-self.skipped-self.failed-active)
        body=(f"Reporting flags | {self.current}\nCompleted {self.completed}/{self.total} | "
              f"skipped {self.skipped} | active {active} | queued {queued} | "
              f"retried {self.retried} | failed {self.failed} | elapsed {time.monotonic()-self.started:.0f}s")
        if self.live:
            from rich.panel import Panel
            from rich.text import Text
            return Panel(Text(body),title='ClickHouse · live_market_ssd / sip_raw_ssd')
        return body

    def update(self, current):
        if current != self.current:
            super().update(current)

    def __exit__(self, error_type, *rest):
        if error_type is None:
            self.current = 'staged' if getattr(self,'stage_only',False) else 'finished'
        return super().__exit__(error_type,*rest)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-date", required=True, type=date.fromisoformat)
    p.add_argument("--end-date", required=True, type=date.fromisoformat)
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.add_argument("--runtime", type=Path, default=RUNTIME)
    p.add_argument("--flatfiles-root-ch", default="/mnt/d/market-data/flatfiles/us_stocks_sip")
    p.add_argument("--flatfiles-root-win", default="D:/market-data/flatfiles/us_stocks_sip")
    p.add_argument("--archive-flatfiles-root-win", type=Path, default=ARCHIVE)
    p.add_argument("--cache-flatfiles-root-win", type=Path, default=CACHE)
    p.add_argument("--hydrate-missing-trades", action="store_true",
                   help="Copy certified missing trade gzip files from archive to ClickHouse's D cache")
    p.add_argument("--max-threads", type=int, default=4)
    p.add_argument("--tickers-per-batch", type=int, default=200)
    p.add_argument("--max-memory-gb", type=float, default=8)
    p.add_argument("--query-timeout", type=int, default=3600)
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("--stage-only", action="store_true", help="Reconcile and persist evidence without altering canonical rows")
    p.add_argument("--progress", choices=("auto", "text"), default="auto")
    a = p.parse_args(argv)
    if a.end_date < a.start_date or a.max_threads < 1 or a.tickers_per_batch < 1 or not 0 < a.max_memory_gb <= 32 or a.query_timeout < 1:
        p.error("Invalid range or resource limit")
    return a


def coverage_ddl():
    return f"""CREATE TABLE IF NOT EXISTS {COVERAGE} (
      source_date Date, revision String, source_digest String, status String,
      raw_table String, map_table String, details String, updated_at DateTime64(6,'UTC')
    ) ENGINE=ReplacingMergeTree(updated_at) ORDER BY (source_date,revision)
    SETTINGS storage_policy='live_market_ssd'"""


def placement(c, table, policy):
    db, name = table.split(".")
    rows = c.query(f"SELECT storage_policy FROM system.tables WHERE database={lit(db)} AND name={lit(name)}")
    if rows != [{"storage_policy":policy}]:
        raise ValueError(f"{table}: required policy {policy} absent or incorrect")
    if c.query(f"SELECT disk_name FROM system.parts WHERE active AND database={lit(db)} AND table={lit(name)} AND disk_name!={lit(policy)} LIMIT 1"):
        raise ValueError(f"{table}: parts are outside {policy}")


def preflight(c, years):
    for policy in ("live_market_ssd", "sip_raw_ssd"):
        rows = c.query(f"SELECT disks FROM system.storage_policies WHERE policy_name={lit(policy)}")
        if not rows or any(r["disks"] != [policy] for r in rows):
            raise ValueError("Required SSD-only policy unavailable: " + policy)
    for year in years:
        placement(c, f"market_sip_compact.events_{year}", "sip_raw_ssd")
    existing = c.query("SELECT name FROM system.tables WHERE database='q_live' AND (startsWith(name,'trade_reporting_') OR name='historical_trade_reporting_coverage_v1')")
    for row in existing:
        placement(c, DB+"."+row["name"], "live_market_ssd")


@contextmanager
def exclusive(runtime):
    # Shared workstation runtime: all laptop/workstation invocations lock the same file.
    runtime.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (runtime / "migration.lock").open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def record(c, day, source_digest, status, raw, mapping, details):
    values = [day, REVISION, source_digest, status, raw, mapping, json.dumps(details, sort_keys=True)]
    c.query(f"INSERT INTO {COVERAGE} VALUES (" + ",".join(map(lit,values)) + ",now64(6))", read=False)


def latest(c, day):
    rows = c.query(f"SELECT * FROM {COVERAGE} FINAL WHERE source_date={lit(day)} AND revision={lit(REVISION)}")
    return rows[0] if rows else None


def source_plan(c,a):
    rows = c.query(f"SELECT * FROM market_sip_compact.events_source_day_stats FINAL WHERE source_date BETWEEN {lit(a.start_date)} AND {lit(a.end_date)} ORDER BY source_date")
    if len({r['source_date'] for r in rows}) != len(rows):
        raise ValueError("Ambiguous source-day certificates; resolve before migration")
    expected = "flatfile_direct_events_v1|drop_trade_correction_codes=07,08,10,11|condition_slots=5"
    for r in rows:
        if r['source_filter_key'] != expected:
            raise ValueError("Unsupported canonical filter contract: " + r['source_date'])
    if any(int(left['last_sip_timestamp_us']) >= int(right['first_sip_timestamp_us'])
           for left,right in zip(rows,rows[1:])):
        raise ValueError('Source-day SIP bounds overlap; timestamp selection is ambiguous')
    import pandas_market_calendars as mcal
    days = {str(d.date()) for d in mcal.get_calendar("XNYS").schedule(start_date=a.start_date,end_date=a.end_date).index}
    missing = sorted(days - {r['source_date'] for r in rows})
    return rows, missing


def hydrate_trade(a,source):
    """Restore a certified gzip into CH's allowed file root, atomically."""
    day=source['source_date']
    relative=Path('trades_v1')/day[:4]/day[5:7]/(day+'.csv.gz')
    target=a.cache_flatfiles_root_win/relative
    expected_size=int(source['trade_file_size'])
    expected_mtime=int(source['trade_file_mtime_ns'])
    if target.is_file():
        stat=target.stat()
        if stat.st_size!=expected_size or stat.st_mtime_ns!=expected_mtime:
            raise ValueError(f'{day}: existing trade cache differs from source certificate')
        return
    if not a.hydrate_missing_trades:
        raise ValueError(f'{day}: trade flatfile absent from ClickHouse D cache; pass --hydrate-missing-trades after checking archive')
    archived=a.archive_flatfiles_root_win/relative
    if not archived.is_file():
        raise ValueError(f'{day}: trade flatfile absent from archive')
    stat=archived.stat()
    if stat.st_size!=expected_size or stat.st_mtime_ns!=expected_mtime:
        raise ValueError(f'{day}: archive trade file differs from source certificate')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.reporting-flags.part')
    temporary.unlink(missing_ok=True)
    source_hash=hashlib.sha256()
    try:
        with archived.open('rb') as source_file, temporary.open('wb') as dest:
            while chunk:=source_file.read(8*1024*1024):
                source_hash.update(chunk)
                dest.write(chunk)
            dest.flush();os.fsync(dest.fileno())
        shutil.copystat(archived,temporary)
        copied_hash=hashlib.sha256()
        with temporary.open('rb') as copy:
            while chunk:=copy.read(8*1024*1024):
                copied_hash.update(chunk)
        stat=temporary.stat()
        if (stat.st_size!=expected_size or stat.st_mtime_ns!=expected_mtime or
                copied_hash.digest()!=source_hash.digest()):
            raise ValueError(f'{day}: copied trade file failed size/time/checksum verification')
        if target.exists():
            raise ValueError(f'{day}: trade cache appeared during copy; refusing replacement')
        temporary.replace(target)
        print(f'{day} | restored certified trade gzip to ClickHouse cache ({expected_size:,} bytes)',flush=True)
    finally:
        temporary.unlink(missing_ok=True)


def where_day(day):
    if isinstance(day, dict):
        first=datetime.fromtimestamp(int(day['first_sip_timestamp_us'])/1_000_000,timezone.utc).date()
        last=datetime.fromtimestamp(int(day['last_sip_timestamp_us'])/1_000_000,timezone.utc).date()
        return (f"event_date BETWEEN {lit(first)} AND {lit(last)} AND "
                f"sip_timestamp_us BETWEEN {int(day['first_sip_timestamp_us'])} "
                f"AND {int(day['last_sip_timestamp_us'])} AND bitAnd(event_meta,1)=1")
    return f"event_date={lit(day)} AND bitAnd(event_meta,1)=1"


def raw_sql(a, source):
    day = source['source_date']
    args = SimpleNamespace(database='market_sip_compact', condition_token_reference_table='event_condition_token_reference',
        flatfiles_root_win=a.flatfiles_root_win,flatfiles_root_ch=a.flatfiles_root_ch,
        drop_trade_correction_codes='07,08,10,11',tickers='')
    files = ingest.DayFiles(day,DownloadJob('quotes',day,'',source['quote_file_path']),DownloadJob('trades',day,'',source['trade_file_path']))
    return ingest.raw_event_union_sql(args,files,trade_only=True,include_reporting_reason=True)


def evidence_tuple(alias):
    return "tuple(" + ",".join([f"bitAnd({alias}.event_meta,63)"] + [f"{alias}.{f}" for f in FIELDS]) + ")"


def map_select(raw, target, day, tickers=None):
    # Independent trade ranks reconstruct original ordering without scanning quote flatfiles.
    # Full equality below makes this fail closed if any tie or ordering differs.
    ticker_filter = " AND ticker IN ("+','.join(map(lit,tickers))+")" if tickers else ""
    return f"""SELECT coalesce(e.ticker,r.ticker) AS ticker, e.ordinal AS ordinal,
       r.event_meta AS expected_meta, r.reporting_reason AS reason,
       toUInt8(e.present!=1 OR r.present!=1 OR {evidence_tuple('e')}!={evidence_tuple('r')}) AS mismatch
    FROM (SELECT *,toUInt8(1) AS present,row_number() OVER (PARTITION BY ticker ORDER BY ordinal) AS trade_rank
          FROM {target} WHERE {where_day(day)}{ticker_filter}) e
    FULL OUTER JOIN (SELECT *,toUInt8(1) AS present,row_number() OVER (PARTITION BY ticker ORDER BY sip_timestamp_us,sequence_number) AS trade_rank
                    FROM {raw} WHERE 1=1{ticker_filter}) r ON e.ticker=r.ticker AND e.trade_rank=r.trade_rank
    SETTINGS join_algorithm='full_sorting_merge', join_use_nulls=0"""


def mutation_sql(target, mapping, day):
    desired = f"toUInt8(bitOr(bitAnd(event_meta,63),multiIf((ticker,ordinal) IN (SELECT ticker,ordinal FROM {mapping} WHERE bitAnd(expected_meta,192)=192),192,(ticker,ordinal) IN (SELECT ticker,ordinal FROM {mapping} WHERE bitAnd(expected_meta,192)=0),0,64)))"
    # Mapping remains immutable until every mutation finishes and validation completes.
    return f"ALTER TABLE {target} UPDATE event_meta={desired} WHERE {where_day(day)} SETTINGS mutations_sync=0,allow_nondeterministic_mutations=1"


def month_mutation_sql(target,sources,month_map):
    first,last=sources[0],sources[-1]
    desired=("toUInt8(bitOr(bitAnd(event_meta,63),multiIf("
             f"(ticker,ordinal) IN (SELECT ticker,ordinal FROM {month_map} WHERE bitAnd(expected_meta,192)=192),192,"
             f"(ticker,ordinal) IN (SELECT ticker,ordinal FROM {month_map} WHERE bitAnd(expected_meta,192)=0),0,64)))")
    from_date=datetime.fromtimestamp(int(first['first_sip_timestamp_us'])/1_000_000,timezone.utc).date()
    to_date=datetime.fromtimestamp(int(last['last_sip_timestamp_us'])/1_000_000,timezone.utc).date()
    scope=(f"event_date BETWEEN {lit(from_date)} AND {lit(to_date)} AND "
           f"sip_timestamp_us BETWEEN {int(first['first_sip_timestamp_us'])} "
           f"AND {int(last['last_sip_timestamp_us'])} AND bitAnd(event_meta,1)=1")
    return f"ALTER TABLE {target} UPDATE event_meta={desired} WHERE {scope} SETTINGS mutations_sync=0,allow_nondeterministic_mutations=1"


def submit_month(c,sources):
    if not sources:
        return
    if any(left['source_date'][:7]!=sources[0]['source_date'][:7] for left in sources):
        raise ValueError('Monthly mutation cannot cross a month boundary')
    year=sources[0]['source_date'][:4]
    target=f'market_sip_compact.events_{year}'
    mappings=[latest(c,s['source_date'])['map_table'] for s in sources]
    recorded={json.loads(latest(c,s['source_date'])['details']).get('monthly_map_table') for s in sources}
    recorded.discard(None)
    if len(recorded)>1:
        raise ValueError('Conflicting monthly map references in coverage')
    month_map=(next(iter(recorded)) if recorded else DB+'.trade_reporting_month_'+sources[0]['source_date'][:7].replace('-','')+'_'+digest([REVISION,[s['source_date'] for s in sources]])[:12])
    for mapping in mappings:
        placement(c,mapping,'live_market_ssd')
    existing={m['mutation_id'] for m in pending(c,target,month_map)}
    if existing:
        if len(existing)!=1:
            raise ValueError('Conflicting monthly mutations; inspect system.mutations')
        print(f"{sources[0]['source_date'][:7]} | resume mutation {next(iter(existing))}",flush=True)
    else:
        if recorded:
            raise ValueError('Monthly map recorded but mutation absent; inspect before rebuilding')
        db,table=target.split('.')
        other=c.query(f"SELECT mutation_id FROM system.mutations WHERE database={lit(db)} AND table={lit(table)} AND is_done=0 LIMIT 1")
        if other:
            raise ValueError(f'Other canonical mutation is active: {other[0]["mutation_id"]}')
        c.query(f"DROP TABLE IF EXISTS {month_map}",read=False)
        c.query(f"CREATE TABLE {month_map} (ticker LowCardinality(String),ordinal UInt64,expected_meta UInt8) ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd'",read=False)
        for mapping in mappings:
            c.query(f"INSERT INTO {month_map} SELECT ticker,ordinal,expected_meta FROM {mapping} WHERE mismatch=0 AND bitAnd(expected_meta,192)!=64",read=False)
        placement(c,month_map,'live_market_ssd')
        expected=sum(
            json.loads(latest(c,s['source_date'])['details'])['counts']['delayed']+
            json.loads(latest(c,s['source_date'])['details'])['counts']['unknown']
            for s in sources
        )
        actual=c.query(f"SELECT count() n FROM {month_map}")[0]['n']
        if actual!=expected:
            raise ValueError(f'Monthly mapping row count {actual} != {expected}; canonical mutation refused')
        print(f"{sources[0]['source_date'][:7]} | submit one mutation for {len(sources)} staged days",flush=True)
        c.query(month_mutation_sql(target,sources,month_map),read=False)
    for source in sources:
        row=latest(c,source['source_date'])
        details=json.loads(row['details'])
        details['monthly_mutation_days']=len(sources)
        details['monthly_map_table']=month_map
        record(c,source['source_date'],row['source_digest'],'mutating',row['raw_table'],row['map_table'],details)


def pending(c,target,mapping):
    db,table = target.split('.')
    return c.query(f"SELECT mutation_id,is_done,latest_fail_reason,parts_to_do FROM system.mutations WHERE database={lit(db)} AND table={lit(table)} AND position(command,{lit(mapping)})>0")


def verify(c,target,mapping,day,tickers=None):
    ticker_filter = " AND ticker IN ("+','.join(map(lit,tickers))+")" if tickers else ""
    row = c.query(f"""SELECT count() AS n,countIf(e.present!=1 OR m.present!=1 OR e.event_meta!=m.expected_meta) AS bad
       FROM (SELECT ticker,ordinal,event_meta,toUInt8(1) present FROM {target} WHERE {where_day(day)}{ticker_filter}) e
       FULL OUTER JOIN (SELECT ticker,ordinal,expected_meta,toUInt8(1) present FROM {mapping} WHERE 1=1{ticker_filter}) m
       ON e.ticker=m.ticker AND e.ordinal=m.ordinal SETTINGS join_algorithm='full_sorting_merge'""")[0]
    if row['bad']:
        raise ValueError(f"Post-mutation flag verification failed: {row['bad']} rows")
    return row['n']


def unchanged_digest(c,target,day):
    selected = (f"sip_timestamp_us BETWEEN {int(day['first_sip_timestamp_us'])} AND {int(day['last_sip_timestamp_us'])}"
                if isinstance(day,dict) else f"event_date={lit(day)}")
    return c.query(f"SELECT count() n,sum(cityHash64(ticker,ordinal,{evidence_tuple('e')})) s,groupBitXor(sipHash64(ticker,ordinal,{evidence_tuple('e')})) x FROM {target} e WHERE {selected}")[0]


def process_day(c,a,source,progress):
    day = source['source_date']; target = f"market_sip_compact.events_{day[:4]}"
    source_digest = digest({k:v for k,v in source.items() if k!='updated_at'})
    previous = latest(c,day)
    if previous and previous['source_digest'] != source_digest:
        raise ValueError(f"{day}: source provenance changed; explicit review required")
    if previous and previous['status']=='complete':
        progress.skipped += 1
        return
    hydrate_trade(a,source)
    raw = previous['raw_table'] if previous else DB+'.trade_reporting_raw_'+day.replace('-','')+'_'+uuid.uuid4().hex[:12]
    mapping = previous['map_table'] if previous else raw.replace('_raw_','_map_')
    details = json.loads(previous['details']) if previous else {}
    status = previous['status'] if previous else 'started'
    if previous:
        progress.retried += 1
    def checkpoint(state):
        record(c,day,source_digest,state,raw,mapping,details)
    progress.update(day+' | reconcile source')
    if status not in ('staged','mutating','complete'):
        # Interrupted inserts can be partial; rebuild staging only, never canonical data.
        checkpoint('started')
        c.query(f"DROP TABLE IF EXISTS {mapping}",read=False)
        present=c.query(f"SELECT name FROM system.tables WHERE database='q_live' AND name={lit(raw.split('.')[1])}")
        if present:
            placement(c,raw,'live_market_ssd')
            n=c.query(f"SELECT count() n FROM {raw}")[0]['n']
            if n!=int(source['trade_event_rows']):
                c.query(f"DROP TABLE {raw}",read=False)
                present=[]
        if not present:
            c.query(f"CREATE TABLE {raw} ENGINE=MergeTree ORDER BY (ticker,sip_timestamp_us,sequence_number) SETTINGS storage_policy='live_market_ssd' AS {raw_sql(a,source)}",read=False)
        placement(c,raw,'live_market_ssd')
        raw_stats=c.query(f"SELECT count() n,min(sip_timestamp_us) lo,max(sip_timestamp_us) hi,countIf(bitAnd(reporting_reason,1)>0) explicit,countIf(bitAnd(reporting_reason,2)>0) prior_date,countIf(bitAnd(reporting_reason,4)>0) lag,countIf(bitAnd(reporting_reason,8)>0) unknown_clock FROM {raw}")[0]
        if (raw_stats['n']!=int(source['trade_event_rows']) or
            raw_stats['lo']<int(source['first_sip_timestamp_us']) or raw_stats['hi']>int(source['last_sip_timestamp_us'])):
            raise ValueError(f"{day}: raw count/timestamp bounds differ from source certificate")
        duplicates = c.query(f"""SELECT ticker FROM {raw}
          GROUP BY ticker,sip_timestamp_us,sequence_number HAVING count()>1 LIMIT 1
          SETTINGS max_bytes_before_external_group_by=268435456""")
        if duplicates:
            raise ValueError(f"{day}: ambiguous raw trade ordering")
        details['source_reasons']=raw_stats
        tickers=[r['ticker'] for r in c.query(f"SELECT DISTINCT ticker FROM {raw} ORDER BY ticker")]
        for offset in range(0,len(tickers),a.tickers_per_batch):
            batch=tickers[offset:offset+a.tickers_per_batch]
            if offset==0 or offset//1000!=(offset-a.tickers_per_batch)//1000:
                progress.update(f"{day} | map tickers {offset+1}-{offset+len(batch)}/{len(tickers)}")
            selection=map_select(raw,target,source,batch)
            if offset==0:
                c.query(f"CREATE TABLE {mapping} ENGINE=MergeTree ORDER BY (ticker,ordinal) SETTINGS storage_policy='live_market_ssd' AS {selection}",read=False)
            else:
                c.query(f"INSERT INTO {mapping} {selection}",read=False)
        placement(c,mapping,'live_market_ssd')
        matched=c.query(f"SELECT count() n,sum(mismatch) bad,countIf(bitAnd(expected_meta,192)=192) delayed,countIf(bitAnd(expected_meta,192)=0) unknown FROM {mapping}")[0]
        target_count=c.query(f"SELECT count() n FROM {target} WHERE {where_day(source)}")[0]['n']
        if matched['n']!=raw_stats['n'] or target_count!=raw_stats['n'] or matched['bad']:
            raise ValueError(f"{day}: full source-to-canonical reconciliation failed: {matched}")
        details['counts']=matched
        checkpoint('staged')
    if a.stage_only:
        placement(c,raw,'live_market_ssd')
        placement(c,mapping,'live_market_ssd')
        if c.query(f'SELECT count() n FROM {mapping}')[0]['n']!=details['counts']['n']:
            raise ValueError(f'{day}: staged mapping row count changed')
        progress.completed+=1
        return
    placement(c,mapping,'live_market_ssd')
    progress.update(day+' | update compact flags')
    mutation_ref=details.get('monthly_map_table',mapping)
    mutations=pending(c,target,mutation_ref)
    if not mutations:
        checkpoint('mutating')
        c.query(mutation_sql(target,mapping,source),read=False)
    while True:
        mutations=pending(c,target,mutation_ref)
        if not mutations:
            raise ValueError('Mutation was not registered; retained staged evidence')
        failures=[m for m in mutations if m['latest_fail_reason']]
        if failures:
            raise ValueError('Mutation requires attention: '+failures[0]['latest_fail_reason'])
        if all(m['is_done'] for m in mutations):
            break
        progress.update(day+f" | mutation parts remaining {sum(m['parts_to_do'] for m in mutations)}")
        time.sleep(5)
    progress.update(day+' | verify flags and preserved fields')
    tickers=[r['ticker'] for r in c.query(f"SELECT DISTINCT ticker FROM {mapping} ORDER BY ticker")]
    verified=0
    for offset in range(0,len(tickers),a.tickers_per_batch):
        batch=tickers[offset:offset+a.tickers_per_batch]
        if offset==0 or offset//1000!=(offset-a.tickers_per_batch)//1000:
            progress.update(f"{day} | verify tickers {offset+1}-{offset+len(batch)}/{len(tickers)}")
        verified+=verify(c,target,mapping,source,batch)
    if verified!=details['counts']['n']:
        raise ValueError(f"{day}: integrity verification failed")
    placement(c,target,'sip_raw_ssd')
    checkpoint('complete')
    # Retain the compact coverage certificate; temporary evidence is safe to remove now.
    for table in (raw,mapping):
        c.query(f"DROP TABLE {table}",read=False)
    progress.completed+=1


def main(argv=None):
    a=parse_args(argv); c=Client(a)
    # Spill sorting/join work rather than allowing unbounded query RAM.
    c.http.default_query_params.update(max_bytes_before_external_sort=int(a.max_memory_gb*1024**3/4),max_bytes_before_external_group_by=int(a.max_memory_gb*1024**3/4))
    try:
        sources,missing=source_plan(c,a)
        if sources and not a.stage_only and len(sources)>1 and any(
            sources[0]['source_date'] <= gap <= sources[-1]['source_date'] for gap in missing
        ):
            raise ValueError('Certified source-day gap lies inside the mutation range; split the range or acquire the missing day')
        preflight(c,sorted({int(s['source_date'][:4]) for s in sources}))
        print(f"Reporting flags | {a.start_date} through {a.end_date} | certified days {len(sources)} | unavailable sessions {len(missing)}",flush=True)
        if missing:
            print('Unavailable (not marked complete): '+', '.join(missing),flush=True)
        if a.plan_only:
            return 0
        # Do not create a replacement runtime root if the workstation is inaccessible.
        if not a.runtime.parent.is_dir():
            raise ValueError('Required runtime parent is unavailable: '+str(a.runtime.parent))
        with exclusive(a.runtime):
            c.query(coverage_ddl(),read=False); placement(c,COVERAGE,'live_market_ssd')
            if a.stage_only or len(sources)==1:
                with ReportingProgress(len(sources),a.progress) as progress:
                    progress.stage_only=a.stage_only
                    for source in sources:
                        process_day(c,a,source,progress)
            else:
                stage_args=copy.copy(a)
                stage_args.stage_only=True
                print('Phase 1/2 | stage and reconcile certified trades',flush=True)
                with ReportingProgress(len(sources),a.progress) as progress:
                    progress.stage_only=True
                    for source in sources:
                        process_day(c,stage_args,source,progress)
                print('Phase 2/2 | monthly mutations and per-day verification',flush=True)
                with ReportingProgress(len(sources),a.progress) as progress:
                    months=sorted({s['source_date'][:7] for s in sources})
                    for month in months:
                        group=[s for s in sources if s['source_date'].startswith(month)]
                        processed=set()
                        # Finish an already-submitted per-day mutation before
                        # submitting a monthly one against the same source part.
                        for source in group:
                            row=latest(c,source['source_date'])
                            if row and row['status']=='mutating' and 'monthly_mutation_days' not in json.loads(row['details']):
                                process_day(c,a,source,progress)
                                processed.add(source['source_date'])
                        staged=[s for s in group if latest(c,s['source_date'])['status']!='complete']
                        if staged:
                            between=[s for s in group if staged[0]['source_date']<=s['source_date']<=staged[-1]['source_date']]
                            if len(between)!=len(staged):
                                raise ValueError(f'{month}: staged days have a completed gap; split the range')
                            submit_month(c,staged)
                        for source in group:
                            if source['source_date'] in processed:
                                continue
                            row=latest(c,source['source_date'])
                            if row['status']!='complete':
                                process_day(c,a,source,progress)
                            else:
                                progress.skipped+=1
                        monthly_maps={json.loads(latest(c,s['source_date'])['details']).get('monthly_map_table') for s in group}
                        monthly_maps.discard(None)
                        for month_map in monthly_maps:
                            if any(not m['is_done'] for m in pending(c,f"market_sip_compact.events_{month[:4]}",month_map)):
                                raise ValueError(f'{month}: monthly map still needed by a running mutation')
                            c.query(f'DROP TABLE IF EXISTS {month_map}',read=False)
        if missing:
            print(f'PARTIAL | {len(missing)} requested sessions lack certified source days; rerun when available',flush=True)
            return 2
        return 0
    except KeyboardInterrupt:
        print('Interrupted. Coverage and staged evidence retained; rerun the same command. Submitted ClickHouse mutations may continue.',file=sys.stderr)
        return 130
    except Exception as exc:
        print('Reporting flags failed: '+c.clean_error(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
