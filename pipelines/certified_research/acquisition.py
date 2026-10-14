"""Existing ingestion entry points behind a frozen bounded source plan.

Only ingestion-owned commands receive flatfile paths. Derivation code never
opens raw SIP files. No command launches training or strategy optimization.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date,timedelta
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

from .contracts import SOURCE_STORAGE_POLICY,digest
from . import source

ROOT=Path(__file__).resolve().parents[2]
ARCHIVE=Path(r'\\DESKTOP-SAAI85T\Workstation-G\market-data\flatfiles\us_stocks_sip')
CACHE=Path(r'\\DESKTOP-SAAI85T\Workstation-D\market-data\flatfiles\us_stocks_sip')
RUNTIME=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes')


@dataclass(frozen=True)
class IngestionPaths:
    env_file: Path
    runtime: Path=RUNTIME/'certified-research'
    cache: Path=CACHE
    archive: Path=ARCHIVE
    clickhouse_cache: str='/mnt/d/market-data/flatfiles/us_stocks_sip'

    def validate(self):
        if not self.env_file.is_file():raise ValueError('Configured ingestion env file is missing')
        if not self.runtime.parent.is_dir():raise ValueError('Required runtime root is unavailable')
        # Prevent mistakenly passing G/HDD or backup paths to ingestion.
        cache=str(self.cache).replace('\\','/').lower()
        if not (cache.startswith('d:/') or cache.startswith('//desktop-saai85t/workstation-d/')):
            raise ValueError('Ingestion cache must be on the designated D SSD')
        if not self.clickhouse_cache.startswith('/mnt/d/'):
            raise ValueError('ClickHouse ingestion mount must be D SSD')


def contiguous_ranges(days):
    """Source days include weekends only when they actually exist upstream."""
    values=sorted(set(days))
    if any(type(day) is not date for day in values):raise ValueError('Source dates must be dates')
    result=[]
    for day in values:
        if result and day==result[-1][1]+timedelta(days=1):result[-1]=(result[-1][0],day)
        else:result.append((day,day))
    return tuple(result)


def updater_command(paths: IngestionPaths,start: date,end: date,*,workers=4,threads=2,memory_gib=2,stage_archive=False,memory_bytes=None):
    if start>end or not 1<=workers<=8 or not 1<=threads<=8 or not 1<=memory_gib<=32:
        raise ValueError('Invalid bounded acquisition limits')
    if memory_bytes is not None and (type(memory_bytes) is not int or not 64*1024**2<=memory_bytes<=32*1024**3):
        raise ValueError('Invalid acquisition byte budget')
    command=(sys.executable,'-B',str(ROOT/'pipelines/market_sip/flatfiles/download_update_events.py'),
            '--start-date',str(start),'--end-date',str(end),'--env-file',str(paths.env_file),
            '--flatfiles-root-win',str(paths.cache),'--flatfiles-root-ch',paths.clickhouse_cache,
            '--output-root-win',str(paths.runtime/'acquisition'),'--storage-policy',SOURCE_STORAGE_POLICY,
            '--download-workers',str(workers),'--max-threads',str(threads),'--max-memory-usage',str(memory_bytes) if memory_bytes is not None else f'{memory_gib}G',
            '--skip-bars','--progress-layout','text','--no-progress-screen')
    return command+(('--archive-root-win',str(paths.archive)) if stage_archive else ())


def reporting_command(paths: IngestionPaths,start: date,end: date,*,hydrate=False,threads=2,memory_gib=2,memory_bytes=None):
    if hydrate:raise ValueError('Historical reporting repair cannot hydrate imported flatfiles; certified sidecars are required')
    if start>end or not 1<=threads<=8 or not 1<=memory_gib<=32:
        raise ValueError('Invalid reporting migration limits')
    if memory_bytes is not None and (type(memory_bytes) is not int or not 64*1024**2<=memory_bytes<=32*1024**3):
        raise ValueError('Invalid reporting byte budget')
    command=(sys.executable,'-B',str(ROOT/'scripts/backfill_trade_reporting_flags.py'),
             '--start-date',str(start),'--end-date',str(end),'--env-file',str(paths.env_file),
             '--runtime',str(paths.runtime/'reporting'),'--max-threads',str(threads),
             '--max-memory-gb',str(memory_bytes/1024**3 if memory_bytes is not None else memory_gib),'--progress','text')
    return command


def run_ingestion(command,paths: IngestionPaths,stop,*,emit=print,guard=None):
    """Own and drain one child. A stopped mutation is reconciled by source gate."""
    paths.validate()
    allowed={str(ROOT/'pipelines/market_sip/flatfiles/download_update_events.py'),
             str(ROOT/'scripts/backfill_trade_reporting_flags.py'),
             str(ROOT/'pipelines/sec/edgar/sec_historical_gap_fill.py')}
    if len(command)<3 or command[0]!=sys.executable or command[1]!='-B' or command[2] not in allowed:
        raise ValueError('Unrecognized ingestion command')
    if any(flag in command for flag in ('--force-day-delete','--force-day-rebuild','--rebuild-existing','--force-rebuild')):
        raise ValueError('Destructive source rebuild is not an automatic acquisition operation')
    if stop.is_set():raise InterruptedError('Acquisition stopped before launch')
    if guard:guard()
    # Existing tools already report stage progress. Inherit their terminal to
    # avoid pipe deadlock or retaining bulk output in memory/history.
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
    child=subprocess.Popen(command,cwd=ROOT,env=env,creationflags=flags)
    interrupted=False
    try:
        while child.poll() is None:
            if guard:guard()
            if stop.wait(.25):
                interrupted=True
                child.send_signal(signal.CTRL_BREAK_EVENT if os.name=='nt' else signal.SIGINT)
                try:child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.terminate();child.wait(timeout=30)
                break
        code=child.wait()
        if interrupted:raise InterruptedError('Acquisition interrupted; source receipts must be reconciled before resume')
        if code:raise RuntimeError(f'Ingestion stage exited {code}; downstream publication is blocked')
        emit('Ingestion process finished; validating canonical source receipts next.')
    except BaseException:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=30)
        raise


def prepare_sources(query,paths,config,claim,stop,*,acquire_missing=False,stage_archive=False,emit=print,
                    runner=run_ingestion,context_start=None,writer_query=None,recover_retained_reporting=False,
                    staging_reserve_bytes=256*1024**3,staging_reserve_fraction=.10,staging_chunk_bytes=256*1024**3):
    """Acquire/repair first; freeze the derived campaign only after verification.

    Canonical ordinals prohibit filling internal holes through ordinary append.
    Existing incomplete sources require an explicit migration. Child exit code
    is never certification: every completed range is reread from ClickHouse.
    """
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('Source preparation stopped')
    guard()
    range_start=context_start if context_start is not None else config.start
    if type(range_start) is not date or not date(2019,1,2)<=range_start<=config.start:
        raise ValueError('Source context must be within canonical history and no later than target start')
    for policy in ('sip_raw_ssd','live_market_ssd'):
        disks=query(f"SELECT disk_name FROM system.storage_policies ARRAY JOIN disks AS disk_name WHERE policy_name='{policy}'")
        if not disks or any(r['disk_name']!=policy for r in disks):raise ValueError('Required source preparation storage policy is unavailable')
    tables=query("SELECT name,storage_policy FROM system.tables WHERE database='market_sip_compact' AND match(name,'^events_[0-9]{4}$')")
    parts=query("SELECT disk_name FROM system.parts WHERE active AND database='market_sip_compact' AND match(table,'^events_[0-9]{4}$') GROUP BY disk_name")
    if any(r['storage_policy']!='sip_raw_ssd' for r in tables) or any(r['disk_name']!='sip_raw_ssd' for r in parts):
        raise ValueError('Canonical source tables/parts are misplaced')
    inv=source.inventory(query,range_start,config.end)
    if inv['incomplete']:raise ValueError('Incomplete imported days require explicit canonical migration')
    if inv['missing']:
        if not acquire_missing:raise ValueError('Missing source days; acquisition is not selected')
        frontier=query('SELECT max(source_date) last_day FROM market_sip_compact.events_source_day_stats FINAL')[0]['last_day']
        if frontier and frontier!='1970-01-01' and any(d<=date.fromisoformat(frontier) for d in inv['missing']):
            raise ValueError('Internal source holes require an ordinal-preserving versioned migration')
        for first,last in contiguous_ranges(inv['missing']):
            guard();emit(f'Acquire source range {first} to {last}')
            command=updater_command(paths,first,last,workers=min(config.workers,8),threads=config.query_threads,
                memory_bytes=config.query_memory_bytes,stage_archive=stage_archive)
            runner(command,paths,stop,emit=emit,guard=claim.check);guard()
        inv=source.inventory(query,range_start,config.end)
        if inv['missing'] or inv['incomplete']:raise ValueError('Updater did not certify all required source sessions')
    resume_native_repairs(query,writer_query,inv,claim,stop,emit=emit)
    reporting=source.reporting_inventory(query,inv)
    if reporting['mutating']:raise ValueError('Canonical reporting mutations must finish before preparation')
    if recover_retained_reporting and reporting['missing']:
        if writer_query is None:raise ValueError('Retained reporting recovery requires the owned writer connection')
        from pipelines.market_sip.flatfiles import retained_reporting
        retained_reporting.recover(writer_query,paths,inv,reporting['missing'],claim,stop,
            reserve_bytes=staging_reserve_bytes,reserve_fraction=staging_reserve_fraction,
            maximum_chunk_bytes=staging_chunk_bytes,emit=emit,query_threads=config.query_threads,
            query_memory_bytes=config.query_memory_bytes)
        guard();reporting=source.reporting_inventory(query,inv)
    for first,last in contiguous_ranges(reporting['missing']):
        guard();emit(f'Reconcile reporting range {first} to {last}')
        command=reporting_command(paths,first,last,threads=config.query_threads,
            memory_bytes=config.query_memory_bytes)
        runner(command,paths,stop,emit=emit,guard=claim.check);guard()
    reporting=source.reporting_inventory(query,inv);guard()
    epoch=source.require_ready(query,inv,reporting)
    return dict(source=inv,reporting=reporting,source_hash=epoch,
        preparation_hash=digest(dict(source_hash=epoch,range=(range_start,config.start,config.end))))


def resume_native_repairs(query,writer_query,inventory,claim,stop,*,emit=print):
    """Route only previously frozen native jobs; never adopt unsigned NS rows."""
    from pipelines.market_sip.events import native_reporting_repair as native
    from .contracts import literal
    exists=query(f"SELECT count() n FROM system.tables WHERE database='q_live' AND name={literal(native.HEADER)}")[0]['n']
    if not int(exists):return
    days=inventory['expected']
    rows=query(f'SELECT source_date,source_hash,evidence_hash,target FROM q_live.{native.HEADER} '
        f'WHERE source_date BETWEEN toDate({literal(min(days))}) AND toDate({literal(max(days))}) ORDER BY source_date')
    if not rows:return
    import json
    from pipelines.market_sip.events import reporting_evidence as ns
    from .contracts import REPORTING_REVISION
    has_coverage=query("SELECT count() n FROM system.tables WHERE database='q_live' AND name='historical_trade_reporting_coverage_v1'")[0]['n']
    coverage=query('SELECT source_date,source_digest,status,details FROM q_live.historical_trade_reporting_coverage_v1 FINAL '
        f'WHERE source_date BETWEEN toDate({literal(min(days))}) AND toDate({literal(max(days))}) AND revision={literal(REPORTING_REVISION)}') if int(has_coverage) else []
    by_day={str(r['source_date']):r for r in coverage}
    if len(by_day)!=len(coverage):raise ValueError('Native reporting coverage is ambiguous')
    seen=set()
    for row in rows:
        day=date.fromisoformat(str(row['source_date']))
        if day not in inventory['rows']:continue
        if day in seen:raise ValueError('Native reporting repair headers are ambiguous')
        seen.add(day)
        expected_hash=inventory['source_hashes'][str(day)]
        if row['source_hash']!=expected_hash or row['target']!=f'market_sip_compact.events_{day.year}':
            raise ValueError('Native reporting repair header differs from frozen canonical source')
        certified=by_day.get(str(day))
        if certified and certified['status']=='complete' and certified['source_digest']==expected_hash:
            details=json.loads(certified['details']);counts=details.get('counts',{})
            if (details.get('evidence_version')==ns.VERSION and details.get('evidence_hash')==row['evidence_hash']
                    and int(counts.get('n',-1))==int(inventory['rows'][day]['trade_event_rows']) and int(counts.get('bad',-1))==0):
                emit(f'Native reporting {day} | certified receipt reused')
                continue
        if writer_query is None:raise ValueError('Native repair resume requires the owned writer connection')
        def guard():
            claim.check()
            if stop.is_set():raise InterruptedError('Native repair stopped; server mutation retained for resume')
            current=source.inventory(query,day,day,sessions=(day,))
            if current['missing'] or current['incomplete'] or current['source_hashes'].get(str(day))!=expected_hash:
                raise ValueError('Canonical source changed during native reporting repair')
        while True:
            guard()
            result=native.step(writer_query,inventory['rows'][day],row['evidence_hash'],claim,guard)
            emit(f"Native reporting {day} | {result['status']} | parts remaining {result.get('parts_remaining',0)}")
            if result['status']=='complete':break
            if stop.wait(1):raise InterruptedError('Native repair stopped; server mutation retained for resume')
