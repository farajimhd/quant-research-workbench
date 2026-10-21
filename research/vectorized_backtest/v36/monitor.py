"""Compact terminal view of durable generation evidence and current activity."""
import json,time
from pathlib import Path
import psutil
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json

def read(path):
    try:return json.loads(Path(path).read_bytes())
    except (OSError,ValueError):return {}

def render(output,days,generations,started):
    table=Table(expand=True);table.add_column('Session');table.add_column('Sealed');table.add_column('Current activity');table.add_column('Replay clock',justify='right')
    details=[]
    for day in days:
        folder=Path(output)/'local'/day;s=read(folder/'status.json');walls=sorted(folder.glob('generation-*/wall.json'));done=len(walls)
        table.add_row(day,f'{done}/{generations}',s.get('stage',s.get('status','Preparing fresh networks')),f"{s.get('completed_seconds',0):,}/{s.get('total_seconds',0):,}")
        if walls:
            w=read(walls[-1]);scores=read(walls[-1].parent/'scores.json');valid=[x for x,e in zip(scores.get('scores',[]),scores.get('eligible',[])) if e]
            details.append(f"Last sealed generation: {w.get('full_generation_seconds',0):.2f}s | best training score ${max(valid):,.2f}" if valid else 'No eligible score in last sealed generation')
    return Panel(table,title='V36 | Independent action networks | Phase 1',subtitle=f"Elapsed {(time.monotonic()-started)/60:.1f} min | RAM free {psutil.virtual_memory().available/2**30:.1f} GiB"),details

def monitor(output,days,generations,stop):
    console=Console();started=time.monotonic();last=None;last_print=0
    with Live(console=console,auto_refresh=False) as live:
        while not stop.wait(2):
            publish_snapshot(output,days,generations)
            panel,details=render(output,days,generations,started)
            if console.is_terminal:
                from rich.console import Group
                live.update(Group(panel,*details),refresh=True)
            elif time.monotonic()-last_print>=15:
                states=[read(Path(output)/'local'/d/'status.json') for d in days]
                key=[(s.get('generation'),s.get('stage'),s.get('candidate_offset')) for s in states]
                if key!=last:
                    for day,s in zip(days,states):print(f"V36 {day} | sealed {s.get('completed_generations',0)}/{generations} | {s.get('stage',s.get('status','preparing'))} | replay {s.get('completed_seconds',0)}/{s.get('total_seconds','?')}",flush=True)
                    last=key;last_print=time.monotonic()

def publish_snapshot(output,days,generations):
    """Shared Phase-1 observer contract; sealed sessions alone count complete."""
    states={d:read(Path(output)/'local'/d/'status.json') for d in days}
    complete=[d for d in days if (Path(output)/'local'/d/'complete.json').exists()]
    failed=[d for d,s in states.items() if s.get('status')=='failed']
    active={d:s for d,s in states.items() if s.get('status')=='running' and d not in complete}
    snapshot=dict(status='failed' if failed else 'running',stage='Phase 1: local training',
        completed=len(complete),active=len(active),queued=len(days)-len(complete)-len(active)-len(failed),failed=len(failed),
        workers=active,total_sessions=len(days),local_generations=generations,updated_epoch=time.time())
    write_json(Path(output)/'status.json',snapshot)
    return snapshot
