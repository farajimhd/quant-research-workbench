"""V10 V5-style monitor: random16 visible, hidden48 supervision, full validation."""
import argparse,json,time,sys
from pathlib import Path
from rich.console import Console,Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich import box
from ..panels.phase2_training_v1 import BlockBar,number
from ..phase1_v5_raw.panel import duration


def read(root):
    try:s=json.loads((root/'status.json').read_text())
    except (OSError,ValueError):return dict(state='unavailable',stage='waiting for readable status')
    for name in ('hardware','resume-arguments','arguments'):
        try:
            data=json.loads((root/(name+'.json')).read_text())
            if 'memory_bytes' in data:data['memory_mib']=data['memory_bytes']/2**20
            for k in ('memory_mib','batch_size','validation_batch_size','workers','validation_workers','prefetch','epochs'):
                if k in data:s.setdefault(k,data[k])
        except (OSError,ValueError):pass
    files=sorted(root.glob('validation-[0-9][0-9][0-9].json'))
    if files:
        try:
            validation=json.loads(files[-1].read_text());s['completed_validation']=validation['metrics']['masked'];s['completed_validation_full']=validation['metrics']['all_visible'];s['completed_epoch']=int(files[-1].stem[-3:])
        except (OSError,ValueError):pass
    return s


def render(root,width=120,height=35):
    s=read(root) if isinstance(root,Path) else root
    state=s.get('state','unavailable');stage=s.get('stage','starting');active=state=='running'
    stale=active and time.time()-s.get('updated',0)>30
    validating='validation' in stage;training=stage=='training'
    rows=[Text('BarGPT V10 | PHASE 1 MAE | random16 visible /48 hidden',style='bold bright_cyan'),
          Text(('STALE · ' if stale else '')+state.upper()+' · '+stage,style='yellow' if stale else 'red' if state=='failed' else 'bold')]
    if s.get('error'):rows.append(Text(s['error'],style='red'))
    if s.get('read_warning'):rows.append(Text(s['read_warning'],style='yellow'))
    bars=Table.grid(expand=True,padding=(0,1));bars.add_column(width=15);bars.add_column(ratio=1);bars.add_column(width=24,justify='right')
    epoch=s.get('epoch',0);total=s.get('epoch_total');done=s.get('epoch_done',0);epochs=s.get('epochs',100)
    complete_epochs=epochs if state=='complete' else max(0,epoch-1)
    for label,n,t,tail in (
        ('Full epochs',complete_epochs,epochs,f'{complete_epochs}/{epochs} complete'),
        ('Train windows',done,total,f'{number(done)}/{number(total)}'),
        ('Validation',s.get('validation_done',0),s.get('validation_total'),f'{number(s.get("validation_done"))}/{number(s.get("validation_total"))}')):
        bars.add_row(label,BlockBar(n or 0,t),tail)
    rows.append(bars)
    rate=None;eta=None
    if active and not stale:
        if validating:rate=s.get('validation_windows_per_second');eta=s.get('validation_eta_seconds')
        elif training:
            rate=s.get('windows_per_second');eta=max(0,total-done)/rate if rate and total else None
    rows.append(Text(f'Step {s.get("step",0):,} · {"Validation" if validating else "Training"} {number(rate)} windows/s · phase ETA {duration(eta)}',style='bold'))
    workers=s.get('validation_workers',-1) if validating else -1
    if workers<0:workers=s.get('workers','—')
    rows.append(Text(f'Batch {s.get("batch_size","—")} · validation {s.get("validation_batch_size") or s.get("batch_size","—")} · active workers {workers} · prefetch {s.get("prefetch","—")}',style='dim'))
    metrics=Table('Training objective','Latest train batch',box=box.SIMPLE_HEAD,padding=(0,1))
    for label,key in (('Hidden-bar objective','loss'),('Price','price_loss'),('Activity','activity_loss')):metrics.add_row(label,number(s.get(key),5))
    rows.append(metrics)
    validation=s.get('completed_validation',{});raw=validation.get('raw_mae',{})
    if height>=28:
        table=Table('Hidden close MAE ($)','Trade','Bid','Ask',box=box.SIMPLE_HEAD,padding=(0,1))
        for frame in (('1s','1m','daily') if height<32 else ('100ms','1s','10s','1m','1h','daily')):
            table.add_row(frame,*(number(raw.get(frame,{}).get(k+'_close'),5) for k in ('trade','bid','ask')))
        rows.append(table)
    rows.append(Text(f'Completed full masked validation: epoch {s.get("completed_epoch","—")} · price MAPE {number(validation.get("balanced_price_mape_pct"),3)}% · activity log MAE {number(validation.get("activity_log_mae"),4)}',style='dim'))
    full=s.get('completed_validation_full',{})
    rows.append(Text(f'All-visible price MAPE {number(full.get("balanced_price_mape_pct"),3)}% | activity log MAE {number(full.get("activity_log_mae"),4)}',style='dim'))
    wait=s.get('phase_loader_wait_seconds');fraction=s.get('phase_loader_wait_fraction')
    rows.append(Text(f'Loader wait {number(wait,1)}s ({number(100*fraction if fraction is not None else None,1)}% of phase) · startup/replay {duration(s.get("phase_startup_seconds"))}',style='dim'))
    gpu=s.get('gpu_mib');capacity=s.get('memory_mib')
    rows.append(Text(f'GPU tensors {number(gpu/1024 if gpu is not None else None,1)}/{number(capacity/1024 if capacity else None,1)} GiB · LR {number(s.get("learning_rate"),7)}',style='dim'))
    if height>=28:rows.append(Text('ETA excludes later phases · STOP requests graceful checkpoint · source-valid masks retained',style='dim'))
    return Group(*rows)


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--once',action='store_true')
    p.add_argument('--width',type=int);p.add_argument('--height',type=int);p.add_argument('--capture',type=Path);a=p.parse_args()
    if a.capture and not a.capture.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):p.error('Capture must be under runtime root')
    console=Console(width=a.width,height=a.height,record=bool(a.capture))
    last={}
    def output():
        current=read(a.run)
        if current.get('state')=='unavailable' and last:
            current=dict(last,read_warning='Status temporarily unreadable; retaining last snapshot')
        else:last.clear();last.update(current)
        return render(current,console.width,console.height)
    if a.once or not console.is_terminal:
        console.print(output())
        if a.capture:
            console.save_text(str(a.capture.with_suffix('.txt')),clear=False);console.save_svg(str(a.capture.with_suffix('.svg')),clear=False)
        return
    try:
        with Live(output(),console=console,auto_refresh=False,vertical_overflow='crop') as live:
            while True:live.update(output(),refresh=True);time.sleep(1)
    except KeyboardInterrupt:console.print('Viewer closed; trainer unchanged.')


if __name__=='__main__':main()
