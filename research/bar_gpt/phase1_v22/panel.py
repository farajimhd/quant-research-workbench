"""Read-only bounded campaign view; no terminal escape codes in redirected output."""
import argparse,json,time
from pathlib import Path
from rich.console import Console,Group
from rich.panel import Panel
from rich.progress import Progress,TextColumn,BarColumn
from rich.table import Table


def read(path,default=None):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {} if default is None else default


def render(root):
    p=read(root/'progress.json');rows=read(root/'comparison.json',[])
    stage=p.get('stage','preparing');age=time.time()-p.get('updated',time.time())
    progress=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.percentage:>3.0f}%'))
    progress.add_task(f"Experiments {len(rows)}/8",total=8,completed=len(rows))
    if p.get('total'):progress.add_task('Training windows',total=p['total'],completed=p.get('seen',0))
    table=Table('Model','MAE ratio','Direction error ratio','Windows/s',expand=True)
    for r in rows:
        table.add_row(r['name'],f"{r['ranking']['raw_mae_geomean_ratio']:.3f}",f"{r['ranking']['direction_error_mean_ratio']:.3f}",f"{r['cached_windows_per_second']:,.0f}")
    subtitle=f"{p.get('name','Data preparation')} | {stage} | update {age:.0f}s ago"
    if age>120 and stage!='complete':subtitle+=' | STALE: inspect training log'
    stats=f"Elapsed {p.get('elapsed',0)/60:.1f}/58.3 min | {p.get('speed',0):,.0f} cached windows/s | training ETA {p.get('eta_seconds',0)/60:.1f} min"
    return Panel(Group(subtitle,progress,stats,table,'Ratios vs trained parent; lower is better. Equal timeframe/channel groups.\nMasked and all-visible details: W&B. Checkpoints remain local.'),title='BarGPT V22 capacity campaign')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();c=Console()
    if a.once or not c.is_terminal:c.print(render(a.output));return
    from rich.live import Live
    with Live(console=c,refresh_per_second=1) as live:
        while True:
            live.update(render(a.output))
            if read(a.output/'progress.json').get('stage') in ('complete','failed','stopped'):break
            time.sleep(1)


if __name__=='__main__':main()
