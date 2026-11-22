"""Read-only bounded Rich panel for full-population V21 training."""
import argparse
import time
from pathlib import Path
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.table import Table
from ..phase1_v17.panel import read


def duration(seconds):
    if seconds is None:return '--'
    minutes=int(max(0,seconds)//60)
    return f'{minutes//60}h {minutes%60:02}m'


def render(s):
    age=time.time()-s.get('updated',time.time())
    state=s.get('state','starting');stage=s.get('stage','waiting for trainer')
    if age>30 and state=='running':stage=f'STALE ({int(age)}s): {stage}'
    progress=Progress(TextColumn('{task.description}'),BarColumn(),TextColumn('{task.percentage:>5.1f}%'),TextColumn('{task.fields[detail]}'))
    progress.add_task(f"Epoch {s.get('epoch',0)}/{s.get('epochs',20)}",total=max(1,s.get('epoch_total',1)),
                      completed=s.get('epoch_done',0),detail=f"{s.get('windows_per_second',0):,.0f} windows/s | ETA {duration(s.get('epoch_eta_seconds'))}")
    progress.add_task('Validation',total=max(1,s.get('validation_total',1)),completed=s.get('validation_windows',0),
                      detail=f"{s.get('validation_windows_per_second',0):,.0f}/s | ETA {duration(s.get('validation_eta_seconds'))}")
    health=Table.grid(padding=(0,2));health.add_column();health.add_column()
    health.add_row(f"Step {s.get('step',0):,} | checkpoint {s.get('checkpoint_step',0):,}",
                   f"LR {s.get('learning_rate',0):.2g} | loss {s.get('loss',0):.4f}")
    health.add_row(f"Loader wait {s.get('loader_wait_seconds',0):.2f}s / recent25 steps",
                   f"GPU {s.get('gpu_util',0):.0f}% | {s.get('gpu_memory_mib',0)/1024:.1f} GiB")
    q=s.get('latest_evaluation',{});quality=Table('Latest quality','Value')
    quality.add_row('Scope / step',f"{q.get('scope','not evaluated')} / {q.get('step',0):,}")
    for label,key,fmt in [('1s close MAE','close_mae_1s','${:.4f}'),('Masked 1s close MAE','masked_close_mae_1s','${:.4f}'),
                          ('1s candle direction','candle_direction_1s','{:.2f}%'),('1s volume direction','volume_direction_1s','{:.2f}%'),
                          ('Daily bid range bins','daily_bid_range','{:.2f}%')]:
        quality.add_row(label,fmt.format(q[key]) if key in q else '--')
    return Panel(Group(f'{state.upper()} | {stage}',progress,health,quality,s.get('error') or
                       'One512D bottleneck, one decoder. Train:16 visible in3/4 updates;64 in1/4.'),title='BarGPT V21 | Full-data20 epochs')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            state=read(a.output/'progress.json');live.update(render(state))
            if state.get('state') in ('complete','failed','stopped','quality_attention','qualification_complete'):break
            time.sleep(1)


if __name__=='__main__':main()
