"""Read-only V5 Rich console; no Torch import, trainer control or GPU assumptions."""
import argparse,json,time,sys,os
from pathlib import Path
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]));__package__='research.bar_gpt.phase1_v5'
from rich.console import Console,Group
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich import box
from ..panels.phase2_training_v1 import BlockBar,number


def read(root):
    try:return json.loads((root/'progress.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return dict(state='unavailable',stage='waiting for a readable status snapshot')


def render(p,width=120,height=35):
    short=height<22;compact=width<90;state=p.get('state','unavailable');stage=p.get('stage','starting')
    stale=state in ('starting','running','profiling') and time.time()-p.get('updated',0)>30
    rows=[Text('BarGPT V5 · BAR-TOKEN MAE',style='bold bright_cyan'),
          Text(('STALE · ' if stale else '')+state.upper()+' · '+stage,
               style='red' if state=='failed' else 'yellow' if stale else 'bold')]
    if p.get('error'):rows.append(Text(p['error'],style='bold red',overflow='ellipsis',no_wrap=True))
    if p.get('read_warning'):rows.append(Text(p['read_warning']+' · retaining last snapshot',style='yellow',overflow='ellipsis',no_wrap=True))
    bars=Table.grid(expand=True,padding=(0,1));bars.add_column(width=14 if compact else 20);bars.add_column(ratio=1);bars.add_column(justify='right',width=17 if compact else 27,no_wrap=True,overflow='ellipsis')
    def bar(label,done,total,tail):bars.add_row(label,BlockBar(done or 0,total),tail)
    if p.get('mode')=='profile':
        trial=p.get('profile_trial',0);total=p.get('profile_trials');done=p.get('profile_done',0);size=p.get('profile_total',0)
        fraction=max(0,trial-1)+(done/size if size else 0)
        if state=='profile_complete':fraction=total or 1;total=total or 1
        bar('Profile trials',fraction,total,f'{trial}/{total or "?"} trials')
        bar('Current trial',done,size,f'{done:,}/{size or "?"} updates')
        if height>=26:rows.append(Text(str(p.get('configuration','Preparing authenticated source')),style='dim'))
    else:
        epoch=p.get('epoch',0);epochs=p.get('epochs');count=p.get('epoch_done',0);total=p.get('epoch_total')
        overall=max(0,epoch-1)+(count/total if total else 0)
        if state=='complete':overall=epochs or epoch
        bar('Full-pass epochs',overall,epochs,f'pass {epoch}/{epochs or "?"}')
        bar('Training windows',count,total,f'{number(count)}/{number(total)}')
        bar('Validation',p.get('validation_done',0),p.get('validation_total'),
            f'{number(p.get("validation_done"))}/{number(p.get("validation_total"))}')
    rows.append(bars)
    rows.append(Text(f'Step {p.get("step",0):,} · {number(p.get("samples_per_second"))} windows/s · 16 bars + 1 reference token',style='dim'))
    train=p.get('train',{});val=p.get('validation',{});families=val.get('masked_families',[None]*3)
    metrics=Table('Objective','Profile' if p.get('mode')=='profile' else 'Train','Masked val',box=box.SIMPLE_HEAD if height<34 else box.HORIZONTALS,
                  show_lines=height>=34,padding=(0,1),expand=False)
    for name,key,v in [('Balanced Huber','loss',val.get('masked_objective')),
        ('Price','price_loss',families[0]),('Activity','activity_loss',families[1]),('References','reference_loss',families[2])]:
        metrics.add_row(name,number(train.get(key),5),number(v,5))
    rows.append(metrics)
    if not short and p.get('mode')=='profile' and p.get('profile'):
        table=Table('Configuration','Windows/s','Scope',box=box.HORIZONTALS,show_lines=True,padding=(0,1))
        trials=p['profile'].get('trials',[])
        selected=[t for t in trials if t['kind']=='actual_loader'] or trials[-3:]
        for trial in selected:
            name=trial.get('version','V5 '+('captured' if trial.get('captured') else 'eager'))
            table.add_row(name+' B'+str(trial['batch']),number(trial['windows_per_second']),
                          'actual loader' if trial['kind']=='actual_loader' else 'compute only')
        rows.append(table)
    elif not short and p.get('mode')!='profile':
        raw=val.get('full_raw_mae',{})
        table=Table('Raw close MAE ($)','Trade','Bid','Ask',box=box.SIMPLE_HEAD if height<34 else box.HORIZONTALS,
                    show_lines=height>=34,padding=(0,1))
        for frame in (('1s','1m','daily') if height<34 else ('100ms','1s','10s','1m','1h','daily')):
            values=raw.get(frame,{})
            table.add_row(frame,*(number(values.get(k+'_close'),5) for k in ('trade','bid','ask')))
        rows.append(table)
        rows.append(Text('Raw MAE uses predicted references · all64 bars visible',style='dim',no_wrap=True,overflow='ellipsis'))
    gpu=p.get('gpu_used_mib');capacity=p.get('gpu_total_mib')
    rows.append(Text(f'GPU memory {number(gpu/1024 if gpu is not None else None,1)}/{number(capacity/1024 if capacity else None,1)} GiB · LR {number(train.get("learning_rate"),7)}',style='dim'))
    if state=='profile_complete':rows.append(Text('Profiling finished; full training has not been started.',style='green'))
    return Group(*rows)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--once',action='store_true');p.add_argument('--json',action='store_true')
    p.add_argument('--width',type=int);p.add_argument('--height',type=int);p.add_argument('--capture',type=Path)
    p.add_argument('--refresh',type=float,default=2)
    a=p.parse_args(argv)
    if a.refresh<.5 or (a.width and a.width<40) or (a.height and a.height<12):p.error('Unsupported refresh/dimensions')
    if a.capture and not a.capture.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):p.error('Captures belong under runtimes')
    console=Console(width=a.width,height=a.height,record=bool(a.capture));snapshot=read(a.run)
    if os.name=='nt' and console.is_terminal:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW('BarGPT V5 · bar-token MAE · read-only')
    if a.json:print(json.dumps(snapshot));return 0
    def output():return render(snapshot,console.width,console.height)
    if a.once or not console.is_terminal:
        console.print(output())
        if a.capture:
            a.capture.parent.mkdir(parents=True,exist_ok=True);console.save_text(str(a.capture.with_suffix('.txt')),clear=False)
            console.save_svg(str(a.capture.with_suffix('.svg')),title='BarGPT V5',clear=False)
        return int(snapshot.get('state')=='failed')
    try:
        with Live(output(),console=console,auto_refresh=False,transient=False,vertical_overflow='crop') as live:
            while True:
                live.update(output(),refresh=True);time.sleep(a.refresh)
                current=read(a.run)
                if current.get('state')=='unavailable':snapshot=dict(snapshot,read_warning=current['stage'])
                else:snapshot=current
    except KeyboardInterrupt:console.print('Viewer closed; model processes are unchanged.',style='dim')
    return 0


if __name__=='__main__':raise SystemExit(main())
