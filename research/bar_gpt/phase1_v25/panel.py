"""Read-only Rich view of the authoritative campaign status."""
import argparse,time
from pathlib import Path
from rich.console import Console,Group
from rich.panel import Panel
from rich.progress import Progress,TextColumn,BarColumn
from rich.table import Table
from ..phase1_v24.panel import read


def render(root):
    p=read(root/'progress.json');rows=read(root/'comparison.json',[]);stage=p.get('stage','preparing');age=time.time()-p.get('updated',time.time())
    bar=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.percentage:>3.0f}%'))
    bar.add_task(f'Experiments {len(rows)}/8',total=8,completed=len(rows))
    if p.get('total'):bar.add_task(stage,total=p['total'],completed=p.get('done',0))
    table=Table('Experiment','Close $','Range %','Activity %','Windows/s',expand=True)
    def f(v):return '—' if v is None else f'{v:.3f}'
    for r in rows:table.add_row(r['name'],f(r['summary']['close_mae']),f(r['summary']['range']),f(None if r['kind']=='price' else r['summary']['activity']),f"{r.get('speed',0):,.0f}")
    status=f"{p.get('name','Campaign' if stage=='complete' else 'Preparing')} | {stage} | update {age:.0f}s ago"
    if age>120 and stage not in ('complete','failed','stopped'):status+=' | STALE'
    speed=f"{p['speed']:,.0f}" if 'speed' in p else 'not measured'
    eta=f"{p['eta']/60:.1f} min" if 'eta' in p else 'not estimated'
    stats=f"Experiment {p.get('elapsed',0)/60:.1f}/60 min | {speed} windows/s | ETA {eta}"
    if stage=='complete':stats=f"All {len(rows)} experiments complete | summed experiment time {sum(r['seconds'] for r in rows)/60:.1f} min"
    if p.get('loss') is not None:stats+=f"\nLoss {p['loss']:.4g} | LR {p.get('lr',0):.2g}"
    return Panel(Group(status,bar,stats,table,p.get('message','All metrics use source-valid masks. Weights stay local.')),title='BarGPT V25 · price / activity / masking')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();console=Console()
    if a.once or not console.is_terminal:console.print(render(a.output));return
    from rich.live import Live
    with Live(console=console,refresh_per_second=1) as live:
        while True:
            live.update(render(a.output))
            if read(a.output/'progress.json').get('stage') in ('complete','failed','stopped'):break
            time.sleep(1)
if __name__=='__main__':main()
