"""Compact stage-specific panel; progress remains explicit during preparation."""
import argparse,json,time
from pathlib import Path
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


def view(root):
    try:s=json.loads((root/'progress.json').read_text())
    except (OSError,ValueError):return Panel('Waiting for trainer progress',title='BarGPT Phase2 V3'),False
    state=s.get('state','unknown');stage=s.get('stage','initializing');stale=time.time()-s.get('updated',0)>30
    t=Table.grid(padding=(0,2));t.add_column();t.add_column()
    t.add_row('Run',root.name);t.add_row('State',('STALE / ' if stale else '')+state+' / '+stage)
    t.add_row('Full pass / updates',f"{s.get('data_pass',s.get('epoch',1))} / {s.get('epochs',1)} / {s.get('step',0):,}")
    if s.get('pass_fraction') is not None:t.add_row('Training block progress',f"{100*s['pass_fraction']:.4f}%")
    t.add_row('Origins in current interval',f"{s.get('done',0):,}")
    rate=s.get('validation_samples_per_second',0) if stage=='validation' else s.get('samples_per_second',0)
    t.add_row('Validation speed' if stage=='validation' else 'Training speed',f'{rate:,.0f} origins/s' if rate and not stale else '--')
    try:
        with (root/'metrics.jsonl').open('rb') as f:
            f.seek(0,2);f.seek(max(0,f.tell()-65536));lines=f.read().splitlines()
        for line in reversed(lines[1:]):
            row=json.loads(line)
            if 'train_loss' in row:
                t.add_row('Latest training loss',f"{row['train_loss']:.6f} at step {row['step']:,}");break
    except (OSError,ValueError):pass
    if s.get('learning_rate') is not None:t.add_row('Learning rate',f"{s['learning_rate']:.3g}")
    if s.get('latest_validation_loss') is not None:t.add_row('Latest validation loss',f"{s['latest_validation_loss']:.6f} at step{s['latest_validation_step']:,}")
    timing=s.get('pipeline_timing',{}).get('ms_per_update',{})
    if timing:t.add_row('Input wait / compute',f"{timing.get('source_wait',0):.1f} / {timing.get('compute_guard',0):.1f} ms/update")
    t.add_row('GPU',f"{s.get('gpu_util',0):.0f}% / {s.get('gpu_memory_mib',0)/1024:.1f} GiB")
    t.add_row('Protocol','16 historical embeddings ->256 context ->64x18 future bars')
    t.add_row('Control','STOP checkpoints the exact publication/block/origin cursor')
    if s.get('error'):t.add_row('Error',str(s['error']))
    return Panel(t,title='BarGPT Phase2 V3 / forecasting pretraining'),state in ('complete','failed','stopped','learning_guard_stop')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();console=Console()
    if a.once or not console.is_terminal:console.print(view(a.output)[0]);return
    with Live(console=console,refresh_per_second=1) as live:
        while True:
            panel,done=view(a.output);live.update(panel)
            if done:break
            time.sleep(1)


if __name__=='__main__':main()
