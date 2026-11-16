"""Read-only V5 Rich console; no Torch import, trainer control or GPU assumptions."""
import argparse,json,time,sys,os,math
from collections import deque
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
    try:
        snapshot=json.loads((root/'progress.json').read_text(encoding='utf-8-sig'))
        try:
            owner=json.loads((root/'process.json').read_text(encoding='utf-8-sig'))
            snapshot['_producer_identity']=(owner['pid'],owner['created'])
        except (OSError,ValueError,KeyError):snapshot['_producer_identity']=None
        return snapshot
    except (OSError,ValueError):return dict(state='unavailable',stage='waiting for a readable status snapshot')


class ValidationRate:
    """Read-only, bounded 60-second counter observer; never edits trainer status.

    An attaching viewer cannot recover the beginning of an in-progress pass.
    Rates therefore cover only this observer's fresh samples, not the full pass.
    """
    def __init__(self):self.reset()

    def reset(self):
        self.key=None;self.points=deque(maxlen=256);self.changed=None

    def update(self,snapshot,*,now=None,wall=None):
        now=time.monotonic() if now is None else now
        wall=time.time() if wall is None else wall
        result=dict(snapshot,validation_samples_per_second=None,validation_eta_seconds=None,
                    validation_rate_window_seconds=None,validation_rate_status='inactive')
        if snapshot.get('stage')!='validation' or snapshot.get('state') not in ('validating','running','starting'):
            self.reset();return result
        done=snapshot.get('validation_done');total=snapshot.get('validation_total')
        updated=snapshot.get('updated')
        if (not isinstance(done,int) or isinstance(done,bool) or done<0
            or (total is not None and (not isinstance(total,int) or isinstance(total,bool) or total<=0 or done>total))
            or not isinstance(updated,(int,float)) or not math.isfinite(updated)):
            self.reset();result['validation_rate_status']='unavailable';return result
        if wall-updated>30:
            self.reset();result['validation_rate_status']='stale';return result
        key=(snapshot.get('_producer_identity'),snapshot.get('epoch'),snapshot.get('step'),total)
        if self.key!=key or (self.points and (done<self.points[-1][1] or now<self.points[-1][0])):
            self.reset();self.key=key
        if not self.points or done!=self.points[-1][1]:self.changed=now
        self.points.append((now,done))
        while len(self.points)>2 and now-self.points[1][0]>=60:self.points.popleft()
        span=now-self.points[0][0];delta=done-self.points[0][1]
        if total is not None and done==total:
            result['validation_eta_seconds']=0.;result['validation_rate_status']='complete'
        elif now-self.changed>=30:
            result['validation_rate_status']='stalled';return result
        else:result['validation_rate_status']='measuring'
        if span>=10 and delta>0:
            rate=delta/span
            result.update(validation_samples_per_second=rate,validation_rate_window_seconds=span)
            if total is not None:result['validation_eta_seconds']=(total-done)/rate
            if done!=total:result['validation_rate_status']='active'
        return result


def duration(seconds):
    if seconds is None:return '—'
    seconds=max(0,math.ceil(seconds));minutes,seconds=divmod(seconds,60);hours,minutes=divmod(minutes,60)
    if hours>=24:return f'{hours//24}d {hours%24:02d}h'
    if hours:return f'{hours}h {minutes:02d}m'
    return f'{minutes}m {seconds:02d}s'


def render(p,width=120,height=35):
    short=height<22;compact=width<90;state=p.get('state','unavailable');stage=p.get('stage','starting')
    stale=state in ('starting','running','profiling','validating') and time.time()-p.get('updated',0)>30
    rows=[Text('BarGPT V5 · BAR-TOKEN MAE'+(f' · width{p["encoder_width"]} →{p.get("embedding_width",512)}' if p.get('encoder_width') else ''),style='bold bright_cyan'),
          Text(('STALE · ' if stale else '')+state.upper()+' · '+stage,
               style='red' if state=='failed' else 'yellow' if stale else 'bold')]
    if p.get('error'):rows.append(Text(p['error'],style='bold red',overflow='ellipsis',no_wrap=True))
    if p.get('read_warning'):rows.append(Text(p['read_warning']+' · retaining last snapshot',style='yellow',overflow='ellipsis',no_wrap=True))
    bars=Table.grid(expand=True,padding=(0,1));bars.add_column(width=14 if compact else 20,no_wrap=True,overflow='ellipsis');bars.add_column(ratio=1);bars.add_column(justify='right',width=17 if compact else 27,no_wrap=True,overflow='ellipsis')
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
        bar('Epochs' if compact else 'Full-pass epochs',overall,epochs,f'pass {epoch}/{epochs or "?"}')
        bar('Train windows' if compact else 'Training windows',count,total,f'{number(count)}/{number(total)}')
        bar('Validation',p.get('validation_done',0),p.get('validation_total'),
            f'{number(p.get("validation_done"))}/{number(p.get("validation_total"))}')
    rows.append(bars)
    if stage=='validation':
        speed=None if stale else p.get('validation_samples_per_second')
        eta=None if stale else p.get('validation_eta_seconds')
        label='Val' if compact else 'Validation recent'
        unit='win/s' if compact else 'windows/s'
        rate_text=f'{label} {number(speed)} {unit} · ETA {duration(eta)}'
        if speed is None and eta is None:
            reason='stale' if stale else p.get('validation_rate_status','measuring')
            rate_text+=f' · {reason}'
    else:
        rate=(f'recent {number(p["recent_samples_per_second"])}/s · ' if p.get('recent_samples_per_second') is not None else '')
        scope='profile' if p.get('mode')=='profile' else 'epoch avg'
        rate_text=f'{rate}{scope} {number(p.get("samples_per_second"))} windows/s'
    rows.append(Text(f'Step {p.get("step",0):,} · {rate_text}',style='dim',no_wrap=True,overflow='ellipsis'))
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
    elif not short and (p.get('mode')!='profile' or bool(val)):
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
    console=Console(width=a.width,height=a.height,record=bool(a.capture));observer=ValidationRate()
    snapshot=observer.update(read(a.run))
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
                if current.get('state')=='unavailable':
                    observer.reset()
                    snapshot=dict(snapshot,read_warning=current['stage'],validation_samples_per_second=None,
                                  validation_eta_seconds=None,validation_rate_status='unavailable')
                else:snapshot=observer.update(current)
    except KeyboardInterrupt:console.print('Viewer closed; model processes are unchanged.',style='dim')
    return 0


if __name__=='__main__':raise SystemExit(main())
