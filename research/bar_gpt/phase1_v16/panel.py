"""Read-only full-population monitor for direction-retention finalists."""
import argparse,json,time
from pathlib import Path
from rich.console import Console,Group
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress,TextColumn,BarColumn
from rich.live import Live


def render(s,active,now=None):
    now=time.time() if now is None else now
    t=Table.grid(padding=(0,2));t.add_column();t.add_column(justify='right')
    t.add_row('Comparison',f"{active.get('pass','-')} / {active.get('passes',2)}")
    t.add_row('Candidate',active.get('label','Waiting for campaign'))
    t.add_row('Stage',s.get('stage','Waiting for validation status'))
    if s.get('state')=='running' and now-s.get('updated',now)>15:t.add_row('Status',f"STALE: {now-s['updated']:.0f}s")
    total=s.get('validation_total',0);done=s.get('validation_windows',0)
    t.add_row('Processed windows',f'{done:,} / {total:,}' if total else f'{done:,} / pending')
    t.add_row('Windows / second',f"{s.get('validation_windows_per_second',0):,.0f}")
    t.add_row('Estimated remaining',f"{s.get('validation_eta_seconds',0)/60:.1f} min" if total else 'pending')
    if 'gpu_util' in s:t.add_row('GPU / memory',f"{s['gpu_util']:.0f}% / {s.get('gpu_memory_mib',0)/1024:.1f} GiB")
    items=[t]
    if total:
        p=Progress(TextColumn('Validation'),BarColumn(bar_width=None),TextColumn('{task.percentage:5.1f}%'),expand=True)
        p.add_task('',total=total,completed=done);items.append(p)
    e=s.get('latest_evaluation')
    if e:
        r=Table.grid(padding=(0,2));r.add_column();r.add_column(justify='right')
        r.add_row('Latent masked / all-visible',f"{e['masked_loss']:.5f} / {e['all_visible_loss']:.5f}")
        if e.get('close_mae_1s') is not None:r.add_row('All-visible1s close MAE',f"${e['close_mae_1s']:.4f}")
        if e.get('direction_1s') is not None:r.add_row('Moving-candle direction (1s)',f"{e['direction_1s']:.2f}%")
        items.append(r)
    if s.get('error'):items.append(str(s['error']))
    return Panel(Group(*items),title=f"Phase1 V16 | {s.get('state','waiting')}",subtitle='Full original validation population | frozen checkpoint')


def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--width',type=int);a=p.parse_args()
    console=Console(width=a.width);active={};state={};last_path=None
    def read():
        nonlocal active,state,last_path
        try:active=json.loads((a.campaign/'active.json').read_text(encoding='utf-8-sig'))
        except (FileNotFoundError,json.JSONDecodeError,PermissionError):pass
        path=active.get('run')
        if path!=last_path:state={};last_path=path
        if path:
            try:state=json.loads((Path(path)/'progress.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError,PermissionError):pass
            try:
                metrics=json.loads((Path(path)/'result.json').read_text())['metrics']
                if state.get('latest_evaluation'):state['latest_evaluation']['direction_1s']=metrics.get('all_visible/candle/1s/trade/moving_direction_pct')
            except (FileNotFoundError,json.JSONDecodeError,PermissionError,KeyError):pass
        return render(state,active)
    if a.once or not console.is_terminal:console.print(read());return
    try:
        with Live(read(),console=console,refresh_per_second=1) as live:
            while True:
                live.update(read())
                if active.get('finished'):break
                time.sleep(1)
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
