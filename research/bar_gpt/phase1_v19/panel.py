"""Read-only Rich progress for the finite latent-target Phase1 run."""
import argparse,json,time
from pathlib import Path
from rich.console import Console,Group
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress,TextColumn,BarColumn
from rich.live import Live


def render(s,now=None):
    now=time.time() if now is None else now;age=now-s.get('updated',now)
    stage=s.get('stage','waiting for trainer status');state=s.get('state','not started')
    table=Table.grid(padding=(0,2));table.add_column();table.add_column(justify='right')
    table.add_row('Stage',str(stage));table.add_row('Epoch / update',f"{s.get('epoch','-')}/20 | {s.get('step',0):,}")
    if age>15:table.add_row('Status freshness',f'STALE | {age:.0f}s')
    if 'loss' in s:table.add_row('Training joint loss',f"{s['loss']:.6f}")
    if 'learning_rate' in s:table.add_row('Learning rate',f"{s['learning_rate']:.2e}")
    if 'windows_per_second' in s:table.add_row('Training windows / sec',f"{s['windows_per_second']:,.0f}")
    if 'gpu_util' in s:table.add_row('GPU compute / memory',f"{s['gpu_util']:.0f}% | {s.get('gpu_memory_mib',0)/1024:.1f} GiB")
    if 'loader_wait_seconds' in s:table.add_row('Loader wait / last25 updates',f"{s['loader_wait_seconds']:.3f}s")
    progress=Progress(TextColumn('{task.description}'),BarColumn(bar_width=None),TextColumn('{task.percentage:5.1f}%'),expand=True)
    active=False
    if stage in ('development','full validation') and s.get('validation_total'):
        progress.add_task('Validation',total=s['validation_total'],completed=s.get('validation_windows',0));active=True
        table.add_row('Validation windows / sec',f"{s.get('validation_windows_per_second',0):,.0f}")
        table.add_row('Validation ETA',f"{s.get('validation_eta_seconds',0)/60:.1f} min")
    elif stage=='training' and s.get('epoch_total'):
        progress.add_task('Epoch',total=s['epoch_total'],completed=s.get('epoch_done',0));active=True
        table.add_row('Epoch training ETA',f"{s.get('epoch_eta_seconds',0)/60:.1f} min")
    items=[table]+([progress] if active else [])
    e=s.get('latest_evaluation')
    if e:
        latest=Table.grid(padding=(0,2));latest.add_column();latest.add_column(justify='right')
        latest.add_row(f"Latest {e['scope']}",f"update {e['step']:,}")
        latest.add_row('Latent masked / visible',f"{e['masked_loss']:.5f} / {e['all_visible_loss']:.5f}")
        latest.add_row('Constant / context loss',f"{e['constant_loss']:.5f} / {e['visible_mean_loss']:.5f}")
        if e.get('close_mae_1s') is not None:
            latest.add_row('1s close MAE / teacher',f"${e['close_mae_1s']:.4f} / ${e['teacher_close_mae_1s']:.4f}")
        if e.get('candle_direction_1s') is not None:latest.add_row('1s candle / volume direction',f"{e['candle_direction_1s']:.1f}% / {e['volume_direction_1s']:.1f}%")
        items.append(latest)
    if s.get('error'):items.append(str(s['error']))
    return Panel(Group(*items),title=f'Phase 1 V19 | frozen16D MAE | {state}',subtitle='16 random bars in3/4 batches;64 in1/4 | 256D embedding')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--width',type=int);a=p.parse_args()
    console=Console(width=a.width);state={}
    def read():
        nonlocal state
        try:state=json.loads((a.output/'progress.json').read_text())
        except (FileNotFoundError,json.JSONDecodeError,PermissionError):pass
        return render(state)
    if a.once or not console.is_terminal:console.print(read());return
    try:
        with Live(read(),console=console,refresh_per_second=1,screen=False) as live:
            while True:
                live.update(read())
                if state.get('state') in ('complete','failed','stopped','qualification_complete'):break
                time.sleep(1)
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
