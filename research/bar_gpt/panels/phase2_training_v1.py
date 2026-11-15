"""Read-only Rich training console; never imports Torch or controls the trainer."""
import argparse
from collections import deque
from datetime import datetime
import json
import math
import os
import sys
import subprocess
from pathlib import Path
import time
from zoneinfo import ZoneInfo
import psutil

from rich.console import Console,Group
from rich import box
from rich.live import Live
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text


def number(value,precision=0):
    return f'{value:,.{precision}f}' if isinstance(value,(float,int)) and math.isfinite(value) else '—'


def compact_number(value):
    for size,suffix in ((1e9,'B'),(1e6,'M'),(1e3,'k')):
        if value>=size:return f'{value/size:.1f}'.rstrip('0').rstrip('.')+suffix
    return str(value)


class BlockBar:
    """Solid Unicode fills; shaded remainder stays legible with NO_COLOR."""
    def __init__(self,done,total,active=False):self.done,self.total,self.active=done,total,active

    def __rich_measure__(self,console,options):return Measurement(3,max(3,options.max_width))

    def __rich_console__(self,console,options):
        width=max(1,options.max_width)
        if not self.total:
            text=Text('░'*width,style='grey35')
            if self.active:
                start=int(time.monotonic())%width
                text.stylize('bright_cyan',start,min(width,start+3))
            yield text;return
        fraction=max(0,min(1,self.done/self.total));scaled=fraction*width
        full=int(scaled);part=(scaled-full)>=.5
        text=Text('█'*full,style='green' if fraction==1 else 'bright_cyan')
        if part and full<width:text.append('▌',style='bright_cyan')
        text.append('░'*max(0,width-full-bool(part)),style='grey35')
        yield text


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


class Observer:
    def __init__(self,root,setup=None):
        self.root,self.setup=Path(root),Path(setup) if setup else None
        self.cached={};self.history=[];self.history_stamp=None;self.samples=deque(maxlen=90)
        self.gpu={};self.gpu_checked=-math.inf

    def gpu_capacity(self,now):
        if now-self.gpu_checked>=20:
            self.gpu_checked=now
            try:
                result=subprocess.run(['nvidia-smi','--query-gpu=memory.total,memory.used,memory.free',
                    '--format=csv,noheader,nounits','--id=0'],capture_output=True,text=True,timeout=2,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if result.returncode:raise ValueError('GPU query failed')
                total,used,free=map(float,result.stdout.strip().splitlines()[0].split(','))
                if not 0<=used<=total or not 0<=free<=total:raise ValueError('Invalid device memory')
                self.gpu=dict(gpu_total=total/1024,gpu_used=used/1024,gpu_free=free/1024,gpu_updated=now)
            except (OSError,ValueError,subprocess.SubprocessError):pass
        return self.gpu if now-self.gpu.get('gpu_updated',-math.inf)<60 else {}

    def read(self,now=None):
        now=time.time() if now is None else now
        warnings=[]
        try:
            value=read_json(self.root/'progress.json')
            if (not isinstance(value,dict) or not isinstance(value.get('state'),str)
                    or not math.isfinite(value.get('updated',math.nan))):raise ValueError('Invalid progress')
            self.cached=value
        except (OSError,ValueError):warnings.append('Status unavailable · retaining last snapshot')
        p=dict(self.cached)
        age=max(0,now-p.get('updated',0))
        try:
            path=self.root/'results.json';stamp=path.stat().st_mtime_ns
            if stamp!=self.history_stamp:
                history=read_json(path)
                if not isinstance(history,list) or any(not isinstance(h,dict) or
                    not isinstance(h.get('step'),int) or not isinstance(h.get('validation'),dict)
                    or not isinstance(h['validation'].get('mse'),(int,float)) for h in history):
                    raise ValueError('Invalid history')
                self.history=history;self.history_stamp=stamp
        except (OSError,ValueError):
            warnings.append('Validation history unavailable · retaining previous results')
        history=self.history
        try:proof=read_json(self.root/'checkpoint_last.proof.json')
        except (OSError,ValueError):proof={}
        try:identity=read_json(self.root/'wandb-identity.json')
        except (OSError,ValueError):identity={}
        try:
            with (self.root/'metrics.jsonl').open('rb') as stream:
                stream.seek(max(0,stream.seek(0,2)-65536));lines=stream.read().decode().splitlines()
            rows=[]
            for line in lines:
                try:
                    row=json.loads(line)
                    if 'train_loss' in row:rows.append(row)
                except ValueError:pass
            last=rows[-1] if rows else {}
        except (OSError,UnicodeError):last={}
        resources={};owner='not verified';config={}
        try:
            if str(self.root).startswith(chr(92)*2):
                raise ValueError('Remote data requires running viewer on training host')
            resources=dict(ram=psutil.virtual_memory().available/2**30,
                           disk=psutil.disk_usage(str(self.root)).free/2**30,**self.gpu_capacity(now))
            if self.setup:
                ledger=read_json(self.setup/'child.json');proc=psutil.Process(ledger['pid'])
                if abs(proc.create_time()-ledger['created'])>.01:raise ValueError('Reused process ID')
                actual=proc.cmdline();expected=ledger.get('command')
                if expected and actual!=expected:raise ValueError('Trainer command changed')
                owner=f'PID {proc.pid} verified'
                manifest=read_json(self.setup/'resume-manifest.json')
                config=manifest['model'].get('optimization',{})
        except (OSError,ValueError,KeyError,psutil.Error):
            if p.get('state')=='running' and self.setup:warnings.append('Trainer ownership unverified · inspect process ledger')
        if resources.get('ram',math.inf)<64:warnings.append('RAM below 64 GiB reserve')
        if resources.get('disk',math.inf)<150:warnings.append('Disk below 150 GiB reserve')
        if age>30 and p.get('state')=='running':warnings.append(f'STALE heartbeat · {age:.0f}s old')
        if p.get('error'):warnings.insert(0,str(p['error']))
        if proof and isinstance(proof.get('step'),int) and p.get('step',0)-proof['step']>3000:
            warnings.append('Recovery checkpoint more than 3000 updates behind')
        kind=p.get('epoch_kind')=='full dataset pass'
        if not kind and p:warnings.insert(0,'Full-pass progress unavailable · legacy labels are not dataset passes')
        stage=p.get('stage','waiting');step=p.get('step',0)
        count=p.get('done',0) if stage=='validation' else p.get('interval_origins')
        token=(stage,step,count)
        if self.samples and (stage!=self.samples[-1][1] or step<self.samples[-1][2]
                or count is None or self.samples[-1][3] is None or count<self.samples[-1][3]):self.samples.clear()
        if not self.samples or token!=self.samples[-1][1:]:self.samples.append((now,stage,step,count))
        while len(self.samples)>2 and now-self.samples[0][0]>60:self.samples.popleft()
        rate=step_rate=None
        if len(self.samples)>1 and 10<=now-self.samples[0][0]<=90 and age<=30 and not warnings:
            a=self.samples[0];seconds=now-a[0]
            rate=(count-a[3])/seconds if count is not None and a[3] is not None else None
            step_rate=(step-a[2])/seconds
        last_val=history[-1].get('validation',{}) if history else {}
        total=last_val.get('origins')
        if stage=='validation' and total and p.get('done',0)>total:
            warnings.insert(0,'Validation population exceeds previous full count');total=None
        best=min((h['validation']['mse'] for h in history),default=None)
        stale=0
        if best is not None:
            for h in reversed(history):
                if h['validation']['mse']==best:break
                stale+=1
        interval=p.get('validation_every_updates',150000)
        previous=max((h['step'] for h in history if h['step']<step or (h['step']==step and stage!='validation')),default=0)
        next_step=((step//interval)+1)*interval
        if stage=='validation' and step%interval==0:next_step=step
        trained=None
        if all(isinstance(h.get('train',{}).get('origins'),int) for h in history):
            if history or step<interval:
                trained=sum(h['train']['origins'] for h in history if h['step']<=step)
                if not history or history[-1]['step']<step:
                    trained=trained+p['interval_origins'] if isinstance(p.get('interval_origins'),int) else None
        return dict(progress=p,age=age,warnings=warnings,owner=owner,resources=resources,
                    proof=proof,identity=identity,config=config,last=last,last_val=last_val,
                    validations=len(history),best=best,stale=stale,validation_total=total,
                    previous=previous,next_step=next_step,rate=rate,step_rate=step_rate,kind=kind,
                    trained_origins=trained)


def render(s,width=110,height=32):
    p=s['progress'];state=p.get('state','waiting');stage=p.get('stage','waiting for status')
    compact=height<22 or width<85;short=height<15
    style='red' if state in ('failed','learning_guard_stop') or s['warnings'] else 'green' if state=='complete' else 'cyan'
    rows=[]
    def line(value,style=None):rows.append(Text(value,style=style,no_wrap=True,overflow='ellipsis'))
    line(f'BarGPT · PHASE 2 TRAINING    {state.upper()} / {stage.upper()}' if width>=60 else f'PHASE 2 · {state.upper()} / {stage.upper()}',f'bold {style}')
    try:clock=datetime.fromtimestamp(p.get('updated',time.time()),ZoneInfo('America/Vancouver')).strftime('%H:%M:%S %Z')
    except Exception:clock=datetime.fromtimestamp(p.get('updated',time.time()),__import__('datetime').timezone.utc).strftime('%H:%M:%S UTC')
    line((f"{s['identity'].get('name','Waiting for run identity')}  ·  " if width>=60 else '')+f"heartbeat {s['age']:.0f}s  ·  {clock}" if p else 'Waiting for progress.json · no trustworthy snapshot yet','dim')
    for warning in s['warnings'][:1 if short else 2]:line('! '+warning,'bold red')
    epoch=p.get('data_pass',p.get('epoch',1));epochs=p.get('epochs',10)
    fraction=p.get('pass_fraction') if s['kind'] else None
    complete=epochs if state=='complete' else max(0,epoch-1)
    overall=complete+(fraction or 0)
    bars=Table.grid(expand=True,padding=(0,1))
    bars.add_column(width=14 if width<60 else 18 if width<85 else 23,no_wrap=True,overflow='ellipsis')
    bars.add_column(ratio=1,min_width=3)
    bars.add_column(width=18 if width<60 else 22 if width<85 else 32,no_wrap=True,justify='right',overflow='ellipsis')
    def bar(label,done,total,detail,active=False):
        bars.add_row(Text(label),BlockBar(max(0,done or 0),total,active),Text(detail))
    bar('Full passes' if width<60 else 'Run · full passes',overall if s['kind'] else 0,epochs if s['kind'] else None,
        f'{100*overall/epochs:.2f}% · {complete}/{epochs} complete' if s['kind'] else 'unknown')
    blocks=p.get('total_training_blocks');consumed=round(fraction*blocks) if fraction is not None and blocks else None
    bar(f'Pass {epoch} blocks',consumed,blocks if s['kind'] else None,
        (f'{fraction*100:.1f}% {compact_number(consumed)}/{compact_number(blocks)}' if width<85 else f'{fraction*100:.2f}% · {consumed:,}/{blocks:,}') if consumed is not None else 'unknown')
    interval=max(1,s['next_step']-s['previous']);position=max(0,p.get('step',0)-s['previous'])
    bar('Train updates',position,interval,f'{position:,}/{interval:,} updates' if width>=85 else f'{compact_number(position)}/{compact_number(interval)}')
    vd=p.get('done',0) if stage=='validation' else s['validation_total'] or 0
    bar('Val origins' if stage=='validation' else 'Last val origins',vd,s['validation_total'],
        (f'{compact_number(vd)}/{compact_number(s["validation_total"])}' if width<85 else f'{vd:,}/{s["validation_total"]:,} origins') if s['validation_total'] else f'{vd:,} · total unknown',stage=='validation')
    rows.append(bars)
    rate=s['rate'];speed=(f'{number(rate)} origins/s · recent' if rate is not None else
        f'{number(p.get("samples_per_second"))} origins/s · reported' if stage=='training' and state=='running' and s['age']<=30 else
        'validation rate warming up' if stage=='validation' and state=='running' else 'rate unavailable / inactive')
    line(f"Step {p.get('step',0):,}  ·  {speed}  ·  LR {number(p.get('learning_rate'),9)}" if width>=60 else f"Step {p.get('step',0):,} · LR {p.get('learning_rate',0):.3g}")
    if not short:
        eta=''
        if stage=='training' and s['step_rate'] and s['step_rate']>0:
            seconds=max(0,s['next_step']-p['step'])/s['step_rate'];eta=f' · ~{seconds/60:.0f} min'
        line(f"Next validation @ {s['next_step']:,}{eta}  ·  validations {s['validations']}")
        line(f"Origins (reported): {number(s['trained_origins'])} total · {number(p.get('interval_origins'))} origins since validation")
        if stage=='validation':line('Validation total = previous full population; weights and LR are not updated here.','dim')
    val=s['last_val'];last=s['last']
    res=s['resources'];proof=s['proof'];lag=p.get('step',0)-proof.get('step',p.get('step',0))
    memory=p.get('gpu_memory_mib');memory=memory/1024 if isinstance(memory,(float,int)) else None
    cp=(f'{lag:,} updates behind' if lag>=0 else 'heartbeat catching up') if proof else 'proof unavailable'
    def table(headers):
        t=Table(box=None if short else box.SIMPLE_HEAD,expand=False,padding=(0,1),
                collapse_padding=True,header_style='bold dim',show_edge=False)
        for i,title in enumerate(headers):
            t.add_column(title,overflow='ellipsis',no_wrap=True,max_width=26 if i or title.startswith('Decoded') else 20)
        return t
    if short:
        t=table(['Metric','Latest'])
        t.add_row('Train / val MSE',f"{number(last.get('train_loss'),5)} / {number(val.get('mse'),5)}")
        t.add_row('GPU / origins/s',f"{number(p.get('gpu_util'))}% / {number(rate if rate is not None else p.get('samples_per_second'))}")
        t.add_row('Checkpoint / disk',f"{compact_number(proof['step']) if proof else '—'} / {number(res.get('disk'))} GiB")
        rows.append(t)
        return Group(*rows)
    learning=table(['Learning metric','Train batch','Last full val','Reference'])
    learning.add_row('Latent MSE',number(last.get('train_loss'),6),number(val.get('mse'),6),'best '+number(s['best'],6))
    learning.add_row('Cosine','—',number(val.get('cosine'),5),'target alignment')
    learning.add_row('Gradient norm',number(last.get('gradient_norm'),4),'—','clip '+number(s['config'].get('gradient_clip'),1))
    if not compact:
        learning.add_row('Baseline MSE','—',number(val.get('persistence_mse'),6),'persistence')
        learning.add_row('Mean-baseline MSE','—',number(val.get('training_mean_mse'),6),'training-only mean')
    learning.add_row('Patience','—',f"{s['stale']}/{s['config'].get('patience',8)}",'non-improving vals')
    rows.append(learning)
    if not compact:
        decoded=table(['Decoded close MAE ($)','Trade','Bid','Ask'])
        decoded.add_row('Target-referenced',*[number(val.get('decoded_raw_mae/1s/'+k+'_close'),4) for k in ('trade','bid','ask')])
        rows.append(decoded)
    resources=table(['Resource','Current','Resource','Current'])
    resources.add_row('GPU utilization',number(p.get('gpu_util'))+'%','GPU power',number(p.get('gpu_power_w'))+' W')
    resources.add_row('VRAM used / total' if res.get('gpu_total') else 'Reported GPU memory',
        f"{number(res.get('gpu_used'),1)} / {number(res.get('gpu_total'),1)} GiB" if res.get('gpu_total') else number(memory,1)+' GiB',
        'Free RAM',number(res.get('ram'),1)+' GiB')
    resources.add_row('Free disk',number(res.get('disk'),1)+' GiB','Checkpoint',number(proof.get('step')))
    if not compact:resources.add_row('Free VRAM',number(res.get('gpu_free'),1)+' GiB','Scope','device 0 · global')
    rows.append(resources)
    line(f"{s['owner']} · {cp}"+(' · Ctrl+C closes viewer' if compact else ' · reserves RAM64 / disk150 GiB'),'dim')
    if not compact:
        c=s['config'];line(f"Batch {c.get('batch','—')} {c.get('precision','—')} · prepare {c.get('prepare_workers','—')} / I/O {c.get('io_workers','—')} · W&B linked (remote uploads not polled)",'dim')
    if not compact:line('Read-only · Ctrl+C closes this panel only · pass bars count frozen block traversal','dim')
    return Group(*rows)


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--run',type=Path,required=True);a.add_argument('--setup',type=Path)
    a.add_argument('--once',action='store_true');a.add_argument('--json',action='store_true')
    a.add_argument('--width',type=int);a.add_argument('--height',type=int)
    a.add_argument('--refresh',type=float,default=2);a.add_argument('--capture',type=Path)
    a.add_argument('--observer-state',type=Path,help='Optional viewer-only heartbeat outside the training run')
    args=a.parse_args(argv)
    if not 1<=args.refresh<=10:a.error('Refresh must be between1 and10 seconds')
    console=Console(width=args.width,height=args.height,record=bool(args.capture),legacy_windows=False)
    observer=Observer(args.run,args.setup)
    def artifact_path(path):
        target=path.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
        if not target.is_relative_to(runtime) or target.is_relative_to(args.run.resolve()):
            raise ValueError('Viewer artifacts belong under runtimes, outside the training run')
        target.parent.mkdir(parents=True,exist_ok=True)
        return target
    def heartbeat(s):
        if args.observer_state:
            target=artifact_path(args.observer_state);own=psutil.Process()
            record=dict(pid=own.pid,created=own.create_time(),role='read-only phase2 training panel',
                updated=time.time(),width=console.width,height=console.height,
                trainer_step=s['progress'].get('step'),trainer_state=s['progress'].get('state'))
            if os.name=='nt':
                import ctypes
                session=ctypes.c_uint();ctypes.windll.kernel32.ProcessIdToSessionId(own.pid,ctypes.byref(session))
                ctypes.windll.kernel32.GetConsoleWindow.restype=ctypes.c_void_p
                hwnd=ctypes.windll.kernel32.GetConsoleWindow()
                record.update(session=session.value,console_hwnd=hwnd,visible=bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(hwnd))))
            temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(record));temporary.replace(target)
    def snapshot():return observer.read()
    s=snapshot()
    if os.name=='nt' and console.is_terminal:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW('BarGPT Phase 2 Training · read-only')
        handle=ctypes.windll.kernel32.GetStdHandle(-11);mode=ctypes.c_uint()
        if ctypes.windll.kernel32.GetConsoleMode(handle,ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle,mode.value|4)
    def capture():
        if args.capture:
            target=artifact_path(args.capture)
            console.save_text(str(target.with_suffix('.txt')),clear=False)
            console.save_svg(str(target.with_suffix('.svg')),title='BarGPT Phase 2 Training',clear=False)
            console.export_text(clear=True);console.record=False
    if args.json:print(json.dumps(s));return 0
    if args.once or not console.is_terminal:
        console.print(render(s,console.width,console.height));capture();heartbeat(s)
        return 1 if s['progress'].get('state')=='failed' else 0
    try:
        with Live(render(s,console.width,console.height),console=console,auto_refresh=False,
                  transient=False,vertical_overflow='crop') as live:
            live.refresh();capture();heartbeat(s)
            while True:
                time.sleep(args.refresh);s=snapshot()
                live.update(render(s,console.width,console.height),refresh=True);heartbeat(s)
    except KeyboardInterrupt:console.print('Panel closed; training continues.',style='dim')
    return 0


if __name__=='__main__':raise SystemExit(main())
