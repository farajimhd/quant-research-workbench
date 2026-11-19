"""Read-only Rich dashboard; restarting this monitor never interrupts training."""
import argparse,json,time,os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from pathlib import Path
from rich.console import Console,Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress,TextColumn,BarColumn
from rich.table import Table
from rich.text import Text

TERMINAL={'complete','failed','stopped','learning_guard_stop','interrupted_preserved_last_checkpoint'}


def duration(seconds):
    if seconds is None:return '--'
    m,s=divmod(max(0,int(seconds)),60);h,m=divmod(m,60)
    return f'{h}h {m:02d}m' if h else f'{m}m {s:02d}s'


class Reader:
    def __init__(self,root):self.root=root;self.cache={};self.stamps={};self.training={}
    def read(self,name,default):
        try:
            p=self.root/name;stamp=(p.stat().st_mtime_ns,p.stat().st_size)
            if self.stamps.get(name)!=stamp:
                value=json.loads(p.read_text(encoding='utf-8-sig'))
                self.cache[name]=value;self.stamps[name]=stamp
        except (OSError,ValueError):pass
        return self.cache.get(name,default)
    def train(self):
        try:
            with (self.root/'metrics.jsonl').open('rb') as f:
                f.seek(0,2);start=max(0,f.tell()-65536);f.seek(start);lines=f.read().splitlines()
            for line in reversed(lines[1:] if start else lines):
                try:row=json.loads(line)
                except ValueError:continue
                if 'train_loss' in row:self.training=row;break
        except OSError:pass
        return self.training


def grid():
    t=Table.grid(padding=(0,2),expand=True)
    t.add_column(style='dim',no_wrap=True);t.add_column(ratio=1)
    return t


def render(root,reader,width=100,height=30):
    s=reader.read('progress.json',{})
    if not s:return Panel('Waiting for trainer progress.json',title='BarGPT / Phase 2'),False
    state=s.get('state','unknown');stage=s.get('stage','initializing');done=state in TERMINAL
    age=max(0,time.time()-s.get('updated',0));stale=age>30 and not done
    compact=width<85 or height<29
    train=reader.train();history=reader.read('results.json',[])
    last=history[-1] if history else {};val=last.get('validation',{})
    step=s.get('step',0);validating=stage=='validation'
    rate=s.get('validation_samples_per_second' if validating else 'samples_per_second',0)
    active=not stale and not done and stage in ('training','validation')
    status='STALE' if stale else state.upper()
    style='bold red' if stale or state=='failed' else 'bold cyan' if not done else 'bold green'
    header=Text(f'{status} / {stage}   |   update {step:,}',style=style)
    meta=Text(f'{root.name}   |   pass {s.get("data_pass",1)}/{s.get("epochs",1)}',style='dim',overflow='ellipsis',no_wrap=True)
    bars=Progress(TextColumn('{task.description}',style='bold'),BarColumn(bar_width=None),
                  TextColumn('{task.fields[label]}'),expand=True,auto_refresh=False)
    fraction=1. if state=='complete' else s.get('pass_fraction',0)
    bars.add_task('Full pass (blocks)',total=1,completed=fraction,label=f'{100*fraction:.3f}%')
    cadence=s.get('validation_every_updates',0);interval=min(cadence,s.get('updates_since_validation',0))
    bars.add_task('Next validation',total=cadence or None,completed=interval,
                  label=f'{interval:,}/{cadence:,}' if cadence else 'pending')
    # Only a previous complete evaluation establishes the exact eligible count.
    total=val.get('origins');count=s.get('done',0) if validating else 0
    bars.add_task('Validation',total=total if validating else 1,completed=count if validating else int(bool(val)),start=True,
                  label=(f'{count:,}/{total:,}' if total else f'{count:,} examples') if validating else (f'{total:,} done' if total else 'pending'))
    args=reader.read('manifest.json',{}).get('arguments',{});eta=None
    if active and rate:
        remaining=max(0,total-count) if validating and total else None
        if stage=='training' and cadence and args.get('batch_size'):remaining=(cadence-interval)*args['batch_size']
        if remaining is not None:eta=remaining/rate
    speed=f'{rate:,.0f} examples/s' if active and rate else '--'
    eta_label='validation finish' if validating else 'next validation'
    activity=Text(f'{speed}   |   Est. {eta_label}: {duration(eta)}',style='cyan')
    if validating and not total:activity.append(' (first total unknown)',style='dim')
    quality=grid();loss=train.get('train_loss')
    quality.add_row('Train batch loss',f'{loss:.6f} @ {train.get("step",0):,}' if loss is not None else 'Waiting for first update')
    if val:
        best=min(h['validation']['loss'] for h in history)
        quality.add_row('Val loss / best',f'{val["loss"]:.6f} / {best:.6f} @ {last["step"]:,}')
        if len(history)>1:
            delta=val['loss']-history[-2]['validation']['loss']
            quality.add_row('Val loss change',Text(f'{delta:+.6f} (lower is better)',style='green' if delta<0 else 'yellow'))
        quality.add_row('Price MAPE',f'{val["balanced_price_mape_pct"]:.3f}%')
        quality.add_row('Activity log MAE',f'{val["activity_log_mae"]:.4f}')
        skill=val.get('skill/last/price')
        if skill is not None:quality.add_row('Vs last-bar price',Text(f'{100*skill:+.2f}% (positive is better)',style='green' if skill>0 else 'yellow'))
    else:quality.add_row('Validation','Pending first complete evaluation')
    lr=s.get('learning_rate');quality.add_row('Learning rate',f'{lr:.2g}' if lr is not None else '--')
    resources=grid();memory=s.get('gpu_memory_mib',0)/1024
    capacity=reader.read('hardware.json',{}).get('memory_bytes',0)/2**30
    resources.add_row('GPU / memory',f'{s.get("gpu_util",0):.0f}% / {memory:.1f}'+(f'/{capacity:.1f} GiB' if capacity else ' GiB'))
    timing=s.get('pipeline_timing',{}).get('ms_per_update',{})
    resources.add_row('Train wait/compute' if validating else 'Wait / compute',f'{timing.get("source_wait",0):.1f} / {timing.get("compute_guard",0):.1f} ms')
    if not compact:
        created=reader.read('process.json',{}).get('created')
        resources.add_row('Elapsed / update',f'{duration(time.time()-created) if created else "--"} / {age:.0f}s ago')
    try:saved=duration(time.time()-(root/'checkpoint_last.pt').stat().st_mtime)+' ago'
    except OSError:saved='not saved yet'
    resources.add_row('Checkpoint saved',saved)
    metrics=Table.grid(expand=True,padding=(0,1))
    if compact:
        metrics.add_column();metrics.add_row(Text('LEARNING',style='bold blue'));metrics.add_row(quality)
        if height>=24:
            metrics.add_row(Text('PERFORMANCE',style='bold blue'));metrics.add_row(resources)
    else:
        metrics.add_column(ratio=3);metrics.add_column(ratio=2)
        metrics.add_row(Panel(quality,title='Learning',border_style='blue'),Panel(resources,title='Performance',border_style='dim'))
    parts=[header,meta,Text(''),bars,activity,Text(''),metrics]
    if s.get('error'):parts.append(Text(str(s['error']),style='bold red'))
    if stale:parts.append(Text(f'Heartbeat {age:.0f}s old. Rates and ETA withheld.',style='bold yellow'))
    if not compact:parts.append(Text(s.get('wandb_url',''),style='dim',overflow='ellipsis',no_wrap=True))
    return Panel(Group(*parts),title='BarGPT / Phase 2 forecasting',border_style='cyan',
                 subtitle='Ctrl+C closes monitor only; trainer continues'),done


def view(root):return render(root,Reader(root))


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--once',action='store_true')
    a=p.parse_args();console=Console();reader=Reader(a.output)
    def frame():return render(a.output,reader,console.width,console.height)
    if a.once or not console.is_terminal:console.print(frame()[0]);return
    try:
        with Live(console=console,refresh_per_second=1,screen=True) as live:
            while True:
                panel,done=frame();live.update(panel)
                if done:break
                time.sleep(1)
        if done:console.print(panel)
    except KeyboardInterrupt:console.print('Monitor closed. Training continues.',style='dim')


if __name__=='__main__':main()
