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

from .contracts import SOURCE_STORAGE_POLICY

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


def updater_command(paths: IngestionPaths,start: date,end: date,*,workers=4,threads=2,memory_gib=2):
    if start>end or not 1<=workers<=8 or not 1<=threads<=8 or not 1<=memory_gib<=32:
        raise ValueError('Invalid bounded acquisition limits')
    return (sys.executable,'-B',str(ROOT/'pipelines/market_sip/flatfiles/download_update_events.py'),
            '--start-date',str(start),'--end-date',str(end),'--env-file',str(paths.env_file),
            '--flatfiles-root-win',str(paths.cache),'--flatfiles-root-ch',paths.clickhouse_cache,
            '--output-root-win',str(paths.runtime/'acquisition'),'--storage-policy',SOURCE_STORAGE_POLICY,
            '--download-workers',str(workers),'--max-threads',str(threads),'--max-memory-usage',f'{memory_gib}G',
            '--skip-bars','--progress-layout','text','--no-progress-screen')


def reporting_command(paths: IngestionPaths,start: date,end: date,*,hydrate=False,threads=2,memory_gib=2):
    if hydrate:raise ValueError('Historical reporting repair cannot hydrate imported flatfiles; certified sidecars are required')
    if start>end or not 1<=threads<=8 or not 1<=memory_gib<=32:
        raise ValueError('Invalid reporting migration limits')
    command=(sys.executable,'-B',str(ROOT/'scripts/backfill_trade_reporting_flags.py'),
             '--start-date',str(start),'--end-date',str(end),'--env-file',str(paths.env_file),
             '--runtime',str(paths.runtime/'reporting'),'--max-threads',str(threads),
             '--max-memory-gb',str(memory_gib),'--progress','text')
    return command


def run_ingestion(command,paths: IngestionPaths,stop,*,emit=print):
    """Own and drain one child. A stopped mutation is reconciled by source gate."""
    paths.validate()
    allowed={str(ROOT/'pipelines/market_sip/flatfiles/download_update_events.py'),
             str(ROOT/'scripts/backfill_trade_reporting_flags.py')}
    if len(command)<3 or command[0]!=sys.executable or command[1]!='-B' or command[2] not in allowed:
        raise ValueError('Unrecognized ingestion command')
    if any(flag in command for flag in ('--force-day-delete','--force-day-rebuild','--rebuild-existing','--force-rebuild')):
        raise ValueError('Destructive source rebuild is not an automatic acquisition operation')
    if stop.is_set():raise InterruptedError('Acquisition stopped before launch')
    # Existing tools already report stage progress. Inherit their terminal to
    # avoid pipe deadlock or retaining bulk output in memory/history.
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
    child=subprocess.Popen(command,cwd=ROOT,env=env,creationflags=flags)
    interrupted=False
    try:
        while child.poll() is None:
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
