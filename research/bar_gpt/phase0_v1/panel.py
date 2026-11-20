"""Read-only low-rate progress surface; no trainer ownership or control."""
import argparse,json,time
from pathlib import Path
from rich.console import Console,Group
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress,BarColumn,TextColumn
from rich.live import Live


def render(s,now=None):
    now=time.time() if now is None else now;age=now-s.get('updated',now)
    state=s.get('state','waiting for status');stage=s.get('stage','initializing')
    title=f"Phase 0 · 16D single-bar · {state}"
    table=Table.grid(padding=(0,2));table.add_column();table.add_column(justify='right')
    table.add_row('Stage',str(stage));table.add_row('Epoch / optimizer step',f"{s.get('epoch','—')}/{s.get('epochs',20)} · {s.get('step',0):,}")
    table.add_row('Bars / sec',f"{s.get('bars_per_second',0):,.0f}");table.add_row('Windows / sec',f"{s.get('windows_per_second',0):,.0f}")
    table.add_row('Training loss',f"{s['loss']:.6f}" if 'loss' in s else 'pending')
    table.add_row('Loader wait (cumulative)',f"{s.get('loader_wait_seconds',0):.1f}s")
    if age>15:table.add_row('Status freshness',f'STALE · {age:.0f}s old')
    elif 'gpu_util' in s:table.add_row('GPU compute / memory',f"{s['gpu_util']:.0f}% · {s.get('gpu_memory_mib',0)/1024:.1f} GiB")
    progress=Progress(TextColumn('{task.description}'),BarColumn(bar_width=None),TextColumn('{task.percentage:5.1f}%'),expand=True)
    if stage in ('development','full validation'):
        progress.add_task('Evaluation',total=s.get('validation_total') or 1,completed=s.get('validation_windows',0))
        table.add_row('Validation bars / sec',f"{s.get('validation_bars_per_second',0):,.0f}")
    else:progress.add_task('Epoch',total=s.get('epoch_total') or 1,completed=s.get('epoch_done',0))
    if stage=='training' and 'epoch_eta_seconds' in s:table.add_row('Epoch training ETA',f"{s['epoch_eta_seconds']/60:.1f} min")
    e=s.get('latest_evaluation');items=[table,progress]
    if e:
        latest=Table.grid(padding=(0,2));latest.add_column();latest.add_column(justify='right')
        latest.add_row(f"Latest {e['scope']}",f"step {e['step']:,}")
        latest.add_row('1s trade close MAE',f"${e['trade_close_mae_1s']:.6f}" if e.get('trade_close_mae_1s') is not None else 'not present')
        latest.add_row('1s moving candle direction',f"{e['candle_direction_1s']:.2f}%" if e.get('candle_direction_1s') is not None else 'not present')
        latest.add_row('1s range-bin accuracy',f"{e['range_accuracy_1s']:.2f}%" if e.get('range_accuracy_1s') is not None else 'not present')
        items.append(latest)
    if s.get('error'):items.append(str(s['error']))
    return Panel(Group(*items),title=title,subtitle='Reconstruction of input bars · not forecasting')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--width',type=int);a=p.parse_args()
    console=Console(width=a.width);s={}
    def read():
        nonlocal s
        try:s=json.loads((a.output/'progress.json').read_text())
        except (FileNotFoundError,json.JSONDecodeError,PermissionError):pass
        return render(s)
    if a.once or not console.is_terminal:console.print(read());return
    try:
        with Live(read(),console=console,refresh_per_second=1,screen=False) as live:
            while True:
                live.update(read())
                if s.get('state') in ('complete','failed','stopped','qualification_complete'):break
                time.sleep(1)
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
