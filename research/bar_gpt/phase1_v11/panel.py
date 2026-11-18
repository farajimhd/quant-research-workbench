"""One compact finite-campaign Rich panel; exits when the experiment ends."""
import argparse,json,time
from pathlib import Path
from rich.live import Live
from rich.table import Table
from rich.panel import Panel


def full_view(root):
    """Authoritative full-run status; keep train and validation speeds distinct."""
    try:s=json.loads((root/'status.json').read_text())
    except (OSError,ValueError):return Panel('Waiting for readable full-run status',title='BarGPT V11'),False
    try:args=json.loads((root/'manifest.json').read_text())['arguments']
    except (OSError,ValueError,KeyError):args={}
    state=s.get('state','unknown');stage=s.get('stage','starting');active=state=='running';stale=active and time.time()-s.get('updated',0)>30
    t=Table.grid(padding=(0,2));t.add_column();t.add_column()
    t.add_row('Run',root.name);t.add_row('State',('STALE · ' if stale else '')+state+' · '+stage)
    if s.get('qualification_limit'):t.add_row('Bounded qualification',f"Stop at {s['qualification_limit']:,} updates; no complete epoch claim")
    if s.get('error'):t.add_row('Failure',s['error'])
    t.add_row('Epoch / step',f"{s.get('epoch',0)} / {s.get('epochs',args.get('epochs','—'))} · {s.get('step',0):,} updates")
    t.add_row('Training windows',f"{s.get('epoch_done',0):,} / {s.get('epoch_total',0):,}")
    t.add_row('Validation windows',f"{s.get('validation_done',0):,} / {s.get('validation_total',0):,}")
    validating='validation' in stage;development='evaluation' in stage
    rate=s.get('validation_windows_per_second' if validating else 'windows_per_second',0) if active and not stale else 0
    remaining=max(0,s.get('total',0)-s.get('done',0)) if development else max(0,s.get('epoch_total',0)-s.get('epoch_done',0))
    eta=s.get('validation_eta_seconds') if validating else remaining/rate if rate else None
    if development:t.add_row('Development windows',f"{s.get('done',0):,} / {s.get('total',0):,}")
    t.add_row('Validation speed' if validating else 'Development speed' if development else 'Training speed',f'{rate:,.0f} windows/s' if rate else '—')
    t.add_row('Current-phase ETA',f'{eta/60:.1f} min' if eta is not None and active and not stale else '—')
    t.add_row('Batch / workers / prefetch',f"train {args.get('batch_size','—')} / {args.get('workers','—')} / {args.get('prefetch','—')} · validation {args.get('validation_batch_size','—')} / {args.get('validation_workers','—')}")
    for label,key in (('MAE objective · hidden + visible','loss'),('Price / activity','price_loss'),('Reference price','reference_price_loss'),('Learning rate','learning_rate')):
        if key in s:t.add_row(label,f"{s[key]:.6g}"+(f" / {s.get('activity_loss',0):.6g}" if key=='price_loss' else ''))
    if 'phase_loader_wait_seconds' in s:t.add_row('Loader wait',f"{s['phase_loader_wait_seconds']:.2f} s · {100*s.get('phase_loader_wait_fraction',0):.1f}% of phase")
    if 'gpu_reserved_mib' in s:t.add_row('GPU allocated / reserved',f"{s.get('gpu_mib',0)/1024:.1f} / {s['gpu_reserved_mib']/1024:.1f} GiB")
    try:
        dev=json.loads((root/'development-latest.json').read_text())
        t.add_row('Frozen dev price · masked / full',f"{100*dev['masked']['balanced_price_relative_mean']:.3f}% / {100*dev['all_visible']['balanced_price_relative_mean']:.3f}%")
    except (OSError,ValueError,KeyError):pass
    if s.get('latest_validation'):
        v=s['latest_validation'];t.add_row('Completed full validation',f"epoch {v['epoch']} · price {v['price_mape_pct']:.3f}% · activity {v['activity_log_mae']:.4f}")
    t.add_row('Protocol','16 random visible / 48 hidden · all references predicted')
    t.add_row('Control','STOP requests a graceful checkpoint; ETA excludes later phases')
    return Panel(t,title='BarGPT V11 · phase 1 full training',border_style='yellow' if stale else 'red' if state=='failed' else 'cyan'),state in ('complete','failed','stopped','interrupted','qualification_complete')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--full',action='store_true');p.add_argument('--once',action='store_true');a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            if a.full:
                view,finished=full_view(a.output);live.update(view)
                if finished or a.once:break
                time.sleep(1);continue
            try:value=json.loads((a.output/'progress.json').read_text())
            except (OSError,ValueError):value=dict(stage='waiting for owned trainer')
            table=Table.grid(padding=(0,2));table.add_column();table.add_column()
            table.add_row('Experiment',a.output.name);table.add_row('Stage',value['stage'])
            elapsed=value.get('elapsed_seconds',0);budget=value.get('deadline_seconds',0)
            table.add_row('Elapsed / deadline',f'{elapsed/60:.1f} / {budget/60:.1f} min')
            if 'step' in value:table.add_row('Optimizer step',str(value['step']))
            if 'loss' in value:table.add_row('Loss',f"{value['loss']:.5f}")
            if 'done' in value:table.add_row('Evaluated',f"{value['done']:,} / {value['total']:,}")
            if 'windows_per_second' in value:
                speed=value['windows_per_second'];table.add_row('Speed',f'{speed:,.0f} windows/s')
                if 'done' in value:table.add_row('Evaluation ETA',f"{(value['total']-value['done'])/max(speed,1):.0f} s")
            try:
                curve=json.loads((a.output/'learning-curves.json').read_text())[-1]
                table.add_row('Latest dev · masked / full',f"{100*curve['masked']['balanced_price_relative_mean']:.2f}% / {100*curve['all_visible']['balanced_price_relative_mean']:.2f}% price error")
                table.add_row('Reference · masked / full',f"{100*curve['masked']['reference_price_relative']['mean']:.2f}% / {100*curve['all_visible']['reference_price_relative']['mean']:.2f}% error")
            except (OSError,ValueError,IndexError,KeyError):pass
            live.update(Panel(table,title='BarGPT V11 · median-centered MAE',border_style='cyan'))
            if a.once or value['stage']=='complete' or (a.output/'failure.json').exists():break
            time.sleep(1)


if __name__=='__main__':main()
