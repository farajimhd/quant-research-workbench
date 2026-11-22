"""One read-only workstation panel for the joint single-head comparisons."""
import argparse,time
from pathlib import Path
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress,TextColumn,BarColumn
from rich.table import Table
from ..phase1_v17.panel import read


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            manifest=read(a.output/'manifest.json');arms=manifest.get('arms',[]);finished=0
            progress=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.completed:,.0f}/{task.total:,.0f}'),TextColumn('{task.fields[rate]}'))
            quality=Table('Arm','1s close MAE','Candle direction','Volume direction','Daily bid-count MAE','Bar probe close')
            interrupted=False
            for arm in arms:
                root=a.output/arm;s=read(root/'progress.json');q=read(root/'latest-metrics.json');done=(root/'result.json').exists();partial=(root/'checkpoint_partial.pt').exists()
                finished+=done;interrupted|=partial;state='complete' if done else 'stopped' if partial else 'running' if s else 'queued'
                progress.add_task(f'{arm} Â· {state}',total=manifest.get('steps',1),completed=s.get('step',0),rate=f"{s.get('speed',0):,.0f}/s | {s.get('seconds',0)/60:.1f}/55min")
                if q:quality.add_row(arm,f"${q['all_visible/raw_mae/1s/trade_close']:.4f}",f"{q['all_visible/candle/1s/trade/moving_direction_pct']:.2f}%",f"{q['activity/all_visible/1s/trade_volume/moving_direction_pct']:.2f}%",f"{q['activity/all_visible/daily/bid_count/raw_mae']:,.0f}",f"${q['bar_probe/raw_mae/1s/trade_close']:.4f}")
            caption='Feature attention â†’16D/bar â†’ temporal attention â†’512D â†’ ONE linear64Ã—18 head\n16 random visible bars in3/4 updates;64 in1/4. Displayed quality: all-visible development; bar probe: fixed legacy readout, diagnostic only.'
            live.update(Panel(Group(progress,quality,caption if arms else 'Preparing verified data and profiling batches'),title='BarGPT V21 | protected calibration trials'))
            if arms and (finished==len(arms) or interrupted):break
            time.sleep(1)


if __name__=='__main__':main()
