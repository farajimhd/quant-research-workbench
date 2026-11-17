"""One read-only panel: full-run state, trustworthy counters, and latest validation."""
import argparse,json,time
from pathlib import Path
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


def render(root):
    path=root/'status.json'
    if not path.exists():return Panel('Waiting for trainer status',title='BarGPT V7 full training')
    s=json.loads(path.read_text());age=time.time()-s['updated'];t=Table.grid(padding=(0,3))
    t.add_column(style='bold');t.add_column()
    state=s.get('state','unknown');stage=s.get('stage','starting')
    t.add_row('State',f'{state} / {stage}'+(f' | status {age:.0f}s old' if age>30 else ''))
    t.add_row('Epoch',f"{s.get('epoch',0)} / {s.get('epochs',50)} | optimizer step {s.get('step',0):,}")
    done=s.get('epoch_done',0);total=s.get('epoch_total',0);rate=s.get('windows_per_second',0)
    t.add_row('Training',f'{done:,} / {total:,} windows | {rate:,.0f} windows/s')
    if rate and stage=='training':t.add_row('Pass remaining',f'{max(0,total-done)/rate/60:.1f} min (excludes validation)')
    t.add_row('Loss',f"price {s.get('price_loss',0):.5f} | activity {s.get('activity_loss',0):.5f}")
    t.add_row('Resources',f"GPU {s.get('gpu_mib',0)/1024:.1f} GiB | loader wait {s.get('loader_wait_seconds',0):.1f}s")
    t.add_row('Validation',f"{s.get('validation_done',0):,} / {s.get('validation_total',0):,} windows")
    v=s.get('latest_validation')
    if v:t.add_row('Last full validation',f"epoch {v['epoch']} | price MAPE {v['price_mape_pct']:.3f}% | activity log MAE {v['activity_log_mae']:.4f}")
    if s.get('error'):t.add_row('[red]Failure[/red]',s['error'])
    t.add_row('Elapsed',f"{(time.time()-s['started'])/3600:.2f} h | STOP file requests graceful checkpoint")
    return Panel(t,title='BarGPT V7 · 50 full epochs',subtitle=root.name,border_style='red' if state=='failed' else 'cyan')


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();console=Console()
    if not console.is_terminal:console.print(render(a.run));return
    with Live(render(a.run),console=console,refresh_per_second=1,screen=False) as live:
        while True:
            try:
                live.update(render(a.run))
                if (a.run/'status.json').exists() and json.loads((a.run/'status.json').read_text()).get('state') in ('complete','stopped','failed','interrupted'):break
            except (OSError,json.JSONDecodeError):pass
            time.sleep(1)


if __name__=='__main__':main()
