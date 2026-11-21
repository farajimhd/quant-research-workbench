"""Read-only bounded-campaign progress, automatically exits after all arms finish."""
import argparse
import json
import time
from pathlib import Path
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, TextColumn, BarColumn
from rich.table import Table


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            manifest=read(a.output/'manifest.json');arms=manifest.get('arms',[])
            table=Table('Experiment','State','Updates','Windows/s','Elapsed')
            quality=Table('Latest development (1s)','Candle direction','Volume direction','Volume MAE')
            complete=0
            for arm in arms:
                root=a.output/arm;s=read(root/'progress.json');r=read(root/'result.json')
                state='complete' if r else 'interrupted' if (root/'checkpoint_partial.pt').exists() else 'active' if s else 'queued'
                complete+=bool(r)
                table.add_row(arm,state,f"{s.get('step',0):,}/{manifest.get('steps',0):,}",f"{s.get('speed',0):,.0f}",f"{s.get('seconds',0)/60:.1f} min / 55 min")
                q=read(root/'latest-metrics.json')
                if q:
                    quality.add_row(arm,f"{q['all_visible/candle/1s/trade/moving_direction_pct']:.1f}%",f"{q['activity/all_visible/1s/trade_volume/moving_direction_pct']:.1f}%",f"{q['activity/all_visible/1s/trade_volume/raw_mae']:,.0f}")
            progress=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.completed:.0f}/{task.total:.0f}'))
            progress.add_task('Completed experiments',total=max(1,len(arms)),completed=complete)
            live.update(Panel(Group(progress,table,quality,'Directions: moving targets, all visible | full training disabled'),title='Phase 1 · Activity retention'))
            if arms and complete==len(arms):break
            if any((a.output/arm/'checkpoint_partial.pt').exists() for arm in arms):break
            time.sleep(1)


if __name__=='__main__':main()
