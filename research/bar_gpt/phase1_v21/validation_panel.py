"""Read-only progress panel for a bounded validation run."""
import argparse,time
from pathlib import Path
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress,TextColumn,BarColumn
from rich.table import Table
from ..phase1_v17.panel import read


def render(root):
    p=read(root/'progress.json');failure=read(root/'failure.json');result=read(root/'result.json')
    state='failed' if failure else p.get('stage','preparing')
    bar=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.percentage:>5.1f}%'))
    bar.add_task(state,total=max(1,p.get('total',1)),completed=p.get('windows',0))
    table=Table('Coverage','Speed','Elapsed','ETA')
    eta=p.get('eta_seconds')
    table.add_row(f"{p.get('windows',0):,} / {p.get('total',0):,}",f"{p.get('windows_per_second',0):,.0f} windows/s",f"{p.get('seconds',0)/60:.1f} min",'measuring' if eta is None else f'{eta/60:.1f} min')
    note=failure.get('error','Masked and all-visible reconstruction; original full validation split. Profile runs are partial, never full qualification.')
    if result:
        m=result['metrics'];note=f"1s close: ${m['all_visible/raw_mae/1s/trade_close']:.4f} | daily bid-range bins: {m['all_visible/range/daily/bid/bin_accuracy_pct']:.2f}% | full population: {result['full_population']}"
    return Panel(Group(bar,table,note),title='BarGPT V21 | validation',subtitle=root.name),bool(failure or result)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            display,done=render(a.output);live.update(display)
            if done:break
            time.sleep(1)


if __name__=='__main__':main()
