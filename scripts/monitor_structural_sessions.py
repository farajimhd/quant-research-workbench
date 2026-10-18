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
from datetime import datetime,timezone

from rich.console import Console,Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def snapshot(root):
    def read(name):
        path=root/name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    controller=read('controller-state.json')
    status=read('status.json')
    return dict(controller=controller,status=status,stop=(root/'STOP').exists())


def render(value,width=100,height=30,rate=None):
    controller=value.get('controller',{});s=value.get('status',{})
    phase=controller.get('phase','waiting for controller')
    state=s.get('status',phase) if phase=='backfill' else phase
    color='red' if state in ('failed','interrupted') else 'green' if state=='complete' else 'cyan'
    lines=Text(f'Structural levels  |  {state}',style=color)
    if phase=='waiting_for_bars':
        lines.append('\nWaiting for certified bars and released bar ownership.')
    done=s.get('completed',0);reuse=s.get('reused',0);total=s.get('total')
    lines.append(f'\nTicker-days: {done:,} new  |  {reuse:,} reused  |  {s.get("failed",0):,} failed')
    if total is not None:
        lines.append(f'\nCoverage: {done+reuse:,} / {total:,}  |  {max(0,total-done-reuse):,} remaining')
    if rate is not None:lines.append(f'\nRecent coverage: {rate:,.0f} ticker-days/hour (includes reuse)')
    lines.append(f'\nConcurrency: {s.get("worker_limit",8)} lanes  |  1 query thread / 2 GiB per lane')
    if value.get('stop'):lines.append('\nSTOP present: diagnosis and owner drain required before resume.',style='red')
    error=controller.get('error') or s.get('error')
    if error:lines.append('\n'+str(error),style='red')
    workers=s.get('workers',[])
    table=Table(expand=True,box=None)
    table.add_column('Active ticker / latest certified day',overflow='ellipsis',no_wrap=True)
    table.add_column('New',justify='right');table.add_column('Reused',justify='right')
    for worker in workers[:max(0,height-14)]:
        table.add_row(str(worker.get('active',worker.get('ticker','starting'))),
                      str(worker.get('completed',0)),str(worker.get('reused',0)))
    if not workers:table.add_row('Worker details appear after planning.','—','—')
    footer=Text('AH retrospective · PM/REGULAR causal streaming · Ctrl+C closes monitor only',style='dim')
    return Panel(Group(lines,Text(''),table,footer),title='ARTE structural backfill',width=width)


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
