"""Read-only structural backfill panel; never acquires writer ownership."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import json
import time

from rich.console import Console,Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress_bar import ProgressBar

_CERTIFICATES=None
_CERTIFICATE_TIME=0.
_ACTIVITY={}
_ACTIVITY_TIME=0.


def activity_progress():
    global _ACTIVITY,_ACTIVITY_TIME
    if time.monotonic()-_ACTIVITY_TIME<5:return _ACTIVITY
    from contextlib import closing
    from scripts.build_market_day import Client,parse_args
    with closing(Client(parse_args(['--date','2026-01-02']),readonly=True)) as client:
        rows=client.query("SELECT elapsed,read_rows,read_bytes,memory_usage FROM system.processes "
            "WHERE query_id LIKE 'market-day-%' AND query NOT LIKE '%system.processes%' ORDER BY elapsed DESC LIMIT 1")
    _ACTIVITY=rows[0] if rows else {}
    _ACTIVITY_TIME=time.monotonic()
    return _ACTIVITY


def certificate_progress():
    """Read bounded certificate metadata at most once per minute, never events."""
    global _CERTIFICATES,_CERTIFICATE_TIME
    if time.monotonic()-_CERTIFICATE_TIME<60 and _CERTIFICATES is not None:return _CERTIFICATES
    from contextlib import closing
    from datetime import date,datetime,timezone
    code=Path('D:/TradingML/codes/market-data-pipeline-aad28145a')
    if code.is_dir() and str(code) not in sys.path:sys.path.insert(0,str(code))
    from scripts.build_market_day import Client,parse_args
    from pipelines.certified_research.source import inventory,reporting_inventory
    os.environ['QMD_CLICKHOUSE_URL']='http://192.168.1.218:18123'
    with closing(Client(parse_args(['--date','2026-01-02']),readonly=True)) as client:
        source=inventory(client.query,date(2026,1,1),date(2026,10,2))
        reporting=reporting_inventory(client.query,source)
    _CERTIFICATES=dict(total=len(source['expected']),completed=len(reporting['verified']),
        remaining=len(reporting['missing']),utc=datetime.now(timezone.utc).strftime('%H:%M:%S UTC'))
    _CERTIFICATE_TIME=time.monotonic()
    return _CERTIFICATES


def snapshot(root):
    def read(name):
        path=root/name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    controller=read('controller-state.json')
    status=read('status.json')
    recovery=root.parent/'reporting-evidence-2026-resume'
    evidence={};events=[]
    if (recovery/'status.json').exists():
        evidence=json.loads((recovery/'status.json').read_text())
        if evidence.get('status')=='running':
            try:
                evidence['certificates']=certificate_progress()
                evidence['activity']=activity_progress()
            except Exception as error:
                evidence['certificates']=_CERTIFICATES
                evidence['warning']='Certificate refresh unavailable: '+type(error).__name__
        if (recovery/'build.log').exists():
            with (recovery/'build.log').open('rb') as stream:
                stream.seek(max(0,stream.seek(0,2)-8192))
                events=[line for line in stream.read().decode('utf-8',errors='replace').splitlines()
                        if line.startswith(('Native reporting','Retained reporting','Source ready','Source preparation'))][-4:]
    from datetime import datetime,timezone
    return dict(controller=controller,status=status,stop=(root/'STOP').exists(),evidence=evidence,events=events,
        refreshed=datetime.now(timezone.utc).strftime('%H:%M:%S UTC'))


def render(value,width=100,height=30,rate=None):
    controller=value.get('controller',{});s=value.get('status',{})
    phase=controller.get('phase','waiting for controller')
    if phase in ('plan','profile') and s.get('status') in ('failed','interrupted'):
        # The controller has a new owner; status.json still belongs to the
        # previous attempt until the new pool publishes its first snapshot.
        s={'total':s.get('total'),'worker_limit':s.get('worker_limit',8)}
    state=s.get('status',phase) if phase=='backfill' else phase
    evidence=value.get('evidence',{})
    if phase=='failed' and controller.get('failed_phase')=='plan' and evidence.get('status')=='running':
        state='Waiting for reporting recovery'
    color='red' if state in ('failed','interrupted') else 'green' if state=='complete' else 'cyan'
    lines=Text(f'Structural levels  |  {state}',style=color)
    lines.append('\nDisplay refreshed: '+value.get('refreshed','-')+' | refresh every 2s')
    bars=[]
    recovery_active=evidence.get('status')=='running'
    if evidence.get('status')=='complete':
        lines.append('\nReporting evidence: complete',style='green')
    elif evidence:
        lines.append('\nReporting evidence recovery: '+evidence.get('status','unknown'),style='cyan')
        proof=evidence.get('certificates')
        if proof:
            lines.append(f'\nReporting sessions: {proof["completed"]} / {proof["total"]} certified; {proof["remaining"]} remaining ({proof["utc"]})')
            bars.append(ProgressBar(total=proof['total'],completed=proof['completed']))
        activity=evidence.get('activity',{})
        if activity:
            lines.append(f'\nCurrent verification: {activity["elapsed"]:.0f}s | {int(activity["read_rows"]):,} rows read | {int(activity["read_bytes"])/1024**3:.2f} GiB read')
        else:lines.append('\nCurrent verification: between queries / preparing next step')
        if evidence.get('warning'):lines.append('\n'+evidence['warning'],style='yellow')
        if evidence.get('status')=='running':
            lines.append('\nLevels wait for strict reporting certification; automatic resume enabled.')
        for event in value.get('events',[])[-2:]:lines.append('\n'+event)
    if phase=='waiting_for_bars':
        lines.append('\nWaiting for certified bars and released bar ownership.')
    done=s.get('completed',0);reuse=s.get('reused',0);total=s.get('total')
    lines.append(f'\nTicker-days: {done:,} new  |  {reuse:,} reused  |  {s.get("failed",0):,} failed')
    if total is not None:
        lines.append(f'\nCoverage: {done+reuse:,} / {total:,}  |  {max(0,total-done-reuse):,} remaining')
        bars.append(ProgressBar(total=total,completed=done+reuse))
    elif evidence:lines.append('\nLevels total: pending certified full-scope plan; not started.')
    if rate is not None:lines.append(f'\nRecent coverage: {rate:,.0f} ticker-days/hour (includes reuse)')
    if phase=='backfill':lines.append(f'\nQueued ticker-days: {s.get("queued",max(0,(total or 0)-done-reuse)):,}')
    lines.append(f'\nConcurrency: {s.get("worker_limit",8)} lanes  |  1 query thread / 2 GiB per lane')
    if value.get('stop'):lines.append('\nSTOP present: diagnosis and owner drain required before resume.',style='red')
    error=controller.get('error') or s.get('error')
    if error:lines.append('\n'+str(error),style='red')
    workers=s.get('workers',[])
    table=Table(expand=True,box=None)
    table.add_column('Active ticker / latest certified day',overflow='ellipsis',no_wrap=True)
    table.add_column('New',justify='right');table.add_column('Reused',justify='right')
    for worker in workers[:max(0,height-20 if recovery_active else height-16)]:
        table.add_row(str(worker.get('active',worker.get('ticker','starting'))),
                      str(worker.get('completed',0)),str(worker.get('reused',0)))
    if not workers:table.add_row('Worker details appear after planning.','-','-')
    footer=Text('AH retrospective | PM/REGULAR streaming | Ctrl+C closes monitor only',style='dim')
    return Panel(Group(lines,*bars,Text(''),table,footer),title='ARTE structural backfill',width=width)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,default=Path('D:/TradingML/runtimes/market-data-pipeline/structural-2026-sessions-v1'))
    p.add_argument('--once',action='store_true')
    a=p.parse_args(argv);console=Console()
    if a.once or not console.is_terminal:
        console.print(render(snapshot(a.run_dir),console.width,console.height));return 0
    from rich.live import Live
    last=None;baseline=None;rate=None
    try:
        with Live(console=console,screen=True,auto_refresh=False,vertical_overflow='crop') as live:
            while True:
                try:
                    value=snapshot(a.run_dir);last=value
                    now=time.monotonic();s=value['status'];n=s.get('completed',0)+s.get('reused',0)
                    if baseline is None:baseline=(now,n)
                    if now-baseline[0]>=30:
                        rate=max(0,n-baseline[1])*3600/(now-baseline[0]);baseline=(now,n)
                    panel=render(value,console.width,console.height,rate)
                except (OSError,ValueError) as error:
                    panel=Group(render(last or {},console.width,console.height,rate),
                                Text('Status unavailable; retaining last snapshot: '+str(error),style='yellow'))
                live.update(panel,refresh=True)
                time.sleep(2)
    except KeyboardInterrupt:pass
    return 0


if __name__=='__main__':raise SystemExit(main())
