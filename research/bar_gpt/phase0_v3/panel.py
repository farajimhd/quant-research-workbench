"""Read-only Rich panel for bounded Phase0 input comparisons."""
import argparse,time
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress,BarColumn,TextColumn
from ..phase1_v17.panel import read


def main():
    from pathlib import Path
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            manifest=read(a.output/'manifest.json');arms=manifest.get('arms',[]);finished=0
            table=Table('Input transform','Updates','Windows/s','Elapsed','1s close MAE','Candle direction','Volume direction')
            for arm in arms:
                root=a.output/arm;s=read(root/'progress.json');q=read(root/'latest-metrics.json');result=read(root/'result.json');finished+=bool(result)
                table.add_row(arm,f"{s.get('step',0):,}/{manifest.get('steps',0):,}",f"{s.get('speed',0):,.0f}",f"{s.get('seconds',0)/60:.1f}/55min",f"${q.get('raw_mae/1s/trade_close',0):.4f}",f"{q.get('candle/1s/trade/moving_direction_pct',0):.2f}%",f"{q.get('activity/1s/trade_volume/moving_direction_pct',0):.2f}%")
            bars=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.completed:.0f}/{task.total:.0f}'))
            bars.add_task('Completed comparisons',total=max(1,len(arms)),completed=finished)
            live.update(Panel(Group(bars,table,'Same16D encoder and decoder; only fixed activity input normalization differs'),title='Phase0 V3 | activity reference investigation'))
            if arms and (finished==len(arms) or any((a.output/x/'checkpoint_partial.pt').exists() for x in arms)):break
            time.sleep(1)


if __name__=='__main__':main()
