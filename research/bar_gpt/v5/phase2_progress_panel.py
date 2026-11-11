"""Read-only Rich phase-two build console; worker bars use worker heartbeats."""

import argparse
from collections import Counter, deque
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import psutil
from rich.console import Console, Group
from rich import box
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

TERMINAL = frozenset(('complete','capacity_reached','inventory_exhausted','origin_coverage_exhausted','pilot_complete','failed','stopped'))


def byte_label(value):
    number=float(value or 0)
    for suffix in ("B","KB","MB","GB","TB"):
        if number<1000 or suffix=="TB":return f"{number:,.1f} {suffix}"
        number/=1000


def duration(value):
    n=max(0,float(value or 0))
    return f"{n:.0f}s" if n<60 else f"{int(n)//60}m{int(n)%60:02d}"


class SnapshotReader:
    def __init__(self, root):
        self.root=Path(root)
        self.execution={}
        self.progress={}
        self.workers={}
        self.run=None
        self.cpu={}
        self.rate=deque(maxlen=120)

    def read(self):
        errors=[]
        for name,attribute in (("execution.json","execution"),("progress.json","progress")):
            try:setattr(self,attribute,json.loads((self.root/name).read_text()))
            except (OSError,ValueError) as error:errors.append(name+": "+type(error).__name__)
        run=self.execution.get("telemetry_run")
        if run!=self.run:
            self.workers.clear();self.cpu.clear();self.rate.clear();self.run=run
        now=time.time()
        worker_root=self.root
        name=self.execution.get('worker_root')
        if name:
            if Path(name).name!=name or name in ('.','..'):
                errors.append('Invalid campaign worker root')
            else:worker_root=self.root.parent/name
        native=not str(self.root).startswith("\\\\")
        if run and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}",run):
            directory=worker_root/"worker-status"/run
            for role,total in (("cpu",self.execution.get("workers",32)),("io",self.execution.get("io_workers",8))):
                for slot in range(int(total)):
                    key=(role,slot)
                    try:
                        record=json.loads((directory/f"{role}-{slot:02d}.json").read_text())
                        if record["run"]!=run or record["role"]!=role or record["slot"]!=slot:raise ValueError('Foreign worker identity')
                        self.workers[key]=record
                    except (OSError,ValueError,KeyError):
                        continue
        for record in self.workers.values():
            record["age"]=max(0,now-record.get("updated",0))
            if native:
                try:
                    p=psutil.Process(record["pid"])
                    record["alive"]=p.is_running() and abs(p.create_time()-record["created"])<1
                    stamp=(record['pid'],record['created']);cpu=sum(p.cpu_times()[:2])
                    old=self.cpu.get(stamp);self.cpu[stamp]=(now,cpu)
                    record["cpu_pct"]=max(0,100*(cpu-old[1])/(now-old[0])) if old and now>old[0] else None
                except psutil.Error:record["alive"]=False
        p=self.progress
        point=(p.get("updated",0),p.get("bytes",0))
        if self.rate and point[1]<self.rate[-1][1]:self.rate.clear()
        if point[0] and (not self.rate or point[0]>self.rate[-1][0]):self.rate.append(point)
        while len(self.rate)>2 and self.rate[-1][0]-self.rate[0][0]>60:self.rate.popleft()
        rate=None
        if (p.get('state') not in TERMINAL and now-p.get('updated',0)<=15
            and self.execution.get('started',0)<=p.get('updated',0)
            and len(self.rate)>1 and self.rate[-1][0]>self.rate[0][0]):
            rate=(self.rate[-1][1]-self.rate[0][1])/(self.rate[-1][0]-self.rate[0][0])
        return dict(execution=self.execution,progress=p,workers=dict(self.workers),now=now,
                    read_errors=errors,rate=rate,native=native)


def worker_state(record, running=True):
    if record is None:return ("Starting" if running else "No report"),"dim"
    if record.get("state")=="Failed":return "FAILED","bold red"
    if record.get("closed") or record.get("alive") is False:return "Exited","yellow" if running else "dim"
    if record.get("age",0)>5:return "STALE","yellow"
    state=record.get('state','Unknown')
    return state, {"Query":"cyan","Wait":"yellow","Write":"blue","Verify":"magenta","Ready":"dim","Published":"green"}.get(state,'green')


def worker_table(snapshot, role, slots, width):
    table=Table(box=box.SIMPLE_HEAD,expand=True,padding=(0,1),show_edge=False,width=width)
    table.add_column("ID",width=3)
    table.add_column("Ticker / task",min_width=9,max_width=17,no_wrap=True,overflow="ellipsis")
    table.add_column("State",width=8,no_wrap=True,overflow="ellipsis")
    assignment_key='worker_assignments' if role=='cpu' else 'io_assignments'
    table.add_column("Plan" if snapshot['progress'].get(assignment_key) else "Prepared" if role=='cpu' else "Sealed",width=8)
    table.add_column("Session" if role=='cpu' else "Files",width=7,justify="right",no_wrap=True)
    table.add_column("Tasks D/A",width=13,justify="right",no_wrap=True)
    if width>=88:table.add_column("Tkrs",width=4,justify="right")
    if width>=95:table.add_column("%" if snapshot['progress'].get(assignment_key) else "Q" if role=='cpu' else "MB",width=5,justify="right")
    if width>=95:table.add_column("Time",width=5,justify="right")
    if width>=110:table.add_column("PID",width=6,justify="right")
    if width>=120 and role=='cpu':table.add_column("CPU%",width=5,justify="right")
    running=snapshot['progress'].get('state') not in TERMINAL
    restoring=snapshot['execution'].get('started',0)>snapshot['progress'].get('updated',0)
    for slot in slots:
        record=snapshot['workers'].get((role,slot))
        state,style=worker_state(record,running)
        r=record or {}
        assignment=next((a for a in snapshot['progress'].get(assignment_key,[]) if a['slot']==slot),None)
        if record is None and assignment is not None and role=='io' and running:
            state,style='Idle','dim'
        total=assignment['total'] if assignment else r.get('progress_total')
        done=assignment['completed']+assignment['deferred'] if assignment else r.get('progress_done',0)
        if assignment and done>=total and not restoring and state!='FAILED':
            state,style='Done','green'
        elif not running and not restoring and state!='FAILED':
            state,style='Stopped','yellow'
        bar=ProgressBar(total=total,completed=done,width=8,pulse=total is None and state not in ('Ready','Exited','Starting'),complete_style='cyan' if role=='cpu' else 'green') if total is not None or state not in ('Starting','Ready','Exited') else Text('—',style='dim')
        task=(str(r.get('ticker','—'))+' '+str(r.get('month',''))[2:]).strip()
        cells=[Text(f"{'C' if role=='cpu' else 'I'}{slot+1:02d}"),Text(task),Text(state,style),bar,
               Text(f"{r.get('progress_done',0)}/{r['progress_total']}" if r.get('progress_total') is not None else '—'),Text(f"{done}/{total}" if assignment else f"{r.get('completed',0)}/{r.get('assigned',0)}")]
        if width>=88:cells.append(Text(str(assignment['tickers'] if assignment else r.get('tickers',0))))
        if width>=95:cells.append(Text(f"{100*done/total:.1f}" if assignment and total else "100" if assignment else str(r.get('queries',0)) if role=='cpu' else f"{r.get('bytes_done',0)/1e6:.1f}"))
        if width>=95:cells.append(Text(duration(snapshot['now']-r['task_started'] if r.get('task_started') else r.get('task_seconds',0))))
        if width>=110:cells.append(Text(str(r.get('pid','—'))))
        if width>=120 and role=='cpu':cells.append(Text(f"{r['cpu_pct']:.0f}" if r.get('cpu_pct') is not None else '—'))
        table.add_row(*cells)
    return table


def paired(tables):
    if len(tables)==1:return tables[0]
    grid=Table.grid(expand=True,padding=(0,1))
    for _ in tables:grid.add_column(ratio=1)
    grid.add_row(*tables)
    return grid


def render(snapshot, width=180, height=50, timezone='America/Vancouver', page=None):
    p,e=snapshot['progress'],snapshot['execution'];now=snapshot['now']
    cpus=int(e.get('workers',32));ios=int(e.get('io_workers',8))
    columns=2 if width>=170 else 1
    half=(width-2*(columns-1))//columns
    io_rows=min(math.ceil(ios/columns),max(1,(height-19)//4))
    per_column=max(1,min(math.ceil(cpus/columns),height-19-io_rows))
    capacity=per_column*columns;pages=max(1,math.ceil(cpus/capacity))
    current=int(now//10)%pages if page is None else int(page)%pages
    visible=list(range(current*capacity,min(cpus,(current+1)*capacity)))
    state=p.get('state','waiting for status');age=max(0,now-p.get('updated',0))
    if state=='inventory_exhausted':state='Plan exhausted; byte targets are upper limits'
    if state=='origin_coverage_exhausted':state='Eligible unique origins exhausted'
    if e.get('started',0)>p.get('updated',0):state='starting / restoring file ledger'
    terminal=p.get('state') in TERMINAL and not state.startswith('starting')
    severity='bold red' if p.get('failure') and not state.startswith('starting') else ('yellow' if age>15 and not terminal else 'green')
    heading=Text('BarGPT  PHASE TWO  ',style='bold cyan');heading.append(state,style=severity)
    stamp=datetime.fromtimestamp(p.get('updated',now),ZoneInfo(timezone)).strftime('%H:%M:%S %Z')
    heading.append(f"    {stamp} · "+('final status' if terminal else f'{age:.0f}s old'),style='dim')
    overview=Table.grid(expand=True,padding=(0,1))
    overview.add_column(width=21);overview.add_column(ratio=1);overview.add_column(justify='right',width=29)
    capacities=p.get('capacity',{})
    train=p.get('train_bytes',0);target=e.get('target_bytes',4_000_000_000_000)
    val=p.get('validation_bytes',0);vtarget=e.get('validation_bytes',250_000_000)
    for name,used,limit,color in [('Training',train,target,'cyan'),('Validation',val,vtarget,'green')]:
        c=capacities.get(name.lower(),{})
        label='LIMIT' if c.get('blocked') else 'HEADROOM' if p.get('state')=='inventory_exhausted' and c else 'OPEN' if c else '?'
        title=f'{name} headroom' if label=='HEADROOM' else f'{name} cap {label}'
        overview.add_row(title,ProgressBar(total=limit,completed=used,complete_style=color),f'{byte_label(used)} / {byte_label(limit)}')
    planned=p.get('planned_units');accounted=p.get('accounted_months',0)
    overview.add_row('Month plan status',ProgressBar(total=planned,completed=accounted,pulse=planned is None),f'{accounted:,} / {planned:,}' if planned is not None else 'awaiting plan counters')
    rate='no active transfer' if terminal else byte_label(snapshot['rate'])+'/s' if snapshot.get('rate') is not None else 'warming rate'
    summary=Text(f"Published {byte_label(p.get('bytes',0))}  ·  {p.get('units',0):,} sessions  ·  {p.get('selected_origins',0):,} selected origins  ·  {rate}")
    activity=Text(f"CPU tasks {p.get('active_cpu',0)}/{cpus}  ·  I/O active {p.get('active_io',0)}/{ios}  ·  Source queries {p.get('active_queries',0)} / cap {p.get('query_limit','—')}  ·  Queued months {p.get('queued_units',0):,}",style='dim')
    groups=[heading,overview,summary,activity]
    if p.get('campaign'):
        groups.append(Text(f"Campaign total · current plan {p.get('campaign_plan_number','—')} · blocks stay within one session; context overlap only",style='bold cyan'))
    if terminal and p.get('state')=='inventory_exhausted':
        groups.append(Text('No queued work in this plan. Campaign continuation requires the next sample plan.',style='bold yellow'))
    for name,c in capacities.items():
        if c.get('blocked'):
            groups.append(Text(f"{name.title()} admission closed: {c.get('reason','byte limit')} · {byte_label(c.get('remaining_bytes',0))} remaining · {c.get('deferred_units',0):,} capacity deferrals",style='yellow'))
    unfinished=sum(max(0,a['total']-a['completed']-a['deferred']) for a in p.get('worker_assignments',[]))
    if terminal and unfinished and not state.startswith('starting'):
        groups.append(Text(f'STOPPED WITH QUEUED WORK: {unfinished:,} assigned months unprocessed',style='bold red'))
    if p.get('failure') and not state.startswith('starting'):groups.append(Text('Failure: '+str(p['failure']),style='bold red'))
    groups.append(Text(f"CPU WORKERS {visible[0]+1 if visible else 0}-{visible[-1]+1 if visible else 0}/{cpus}"+(f' | page {current+1}/{pages}, cycles 10s' if pages>1 else '')+(' | fixed assigned months' if p.get('worker_assignments') else ' | prepared sessions'),style='bold'))
    tables=[worker_table(snapshot,'cpu',visible[i*per_column:(i+1)*per_column],half) for i in range(columns)]
    groups.append(paired(tables))
    io_capacity=io_rows*columns
    io_pages=max(1,math.ceil(ios/max(1,io_capacity)))
    io_page=(int(now//10) if page is None else int(page))%io_pages
    io_begin=io_page*io_capacity
    groups.append(Text('I/O WORKERS | '+('fixed assigned month groups' if p.get('io_assignments') else 'sealed files')+(f' | page {io_page+1}/{io_pages}, cycles 10s' if io_pages>1 else ''),style='bold'))
    tables=[worker_table(snapshot,'io',range(io_begin+i*io_rows,min(ios,io_begin+(i+1)*io_rows)),half) for i in range(columns)]
    groups.append(paired(tables))
    waits=Counter(r.get('detail','admission') for (role,_),r in snapshot['workers'].items() if role=='cpu' and r.get('state')=='Wait')
    if waits:groups.append(Text('Waiting: '+' · '.join(f'{n} {why}' for why,n in waits.most_common(2)),style='yellow'))
    if snapshot.get('read_errors'):groups.append(Text('Retaining last snapshot: '+', '.join(snapshot['read_errors']),style='yellow'))
    groups.append(Text('Plan: done+capacity-deferred/fixed assigned months; Tkrs: assigned tickers.' if p.get('worker_assignments') else 'Tasks D/A = done/assigned tasks; Tkrs = unique tickers; plan includes deferrals.',style='dim'))
    groups.append(Text('Read-only | Ctrl+C closes panel only | Session/Files show the current unit.',style='dim'))
    for item in groups:
        if isinstance(item,Text):
            item.no_wrap=True
            item.overflow='ellipsis'
    return Group(*groups)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase2-ar-sampled-v2'))
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--width',type=int)
    parser.add_argument('--height',type=int)
    parser.add_argument('--no-color',action='store_true')
    parser.add_argument('--export-svg',type=Path)
    args=parser.parse_args(argv)
    if args.export_svg and not args.once:parser.error('--export-svg requires --once')
    vt_enabled=None
    if os.name=='nt':
        # Scheduled interactive consoles may inherit a legacy mode even inside
        # Windows Terminal. Enable VT on our own output handle, never globally.
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.GetStdHandle.restype=ctypes.c_void_p
        handle=kernel.GetStdHandle(-11)
        mode=ctypes.c_ulong()
        if kernel.GetConsoleMode(ctypes.c_void_p(handle),ctypes.byref(mode)):
            vt_enabled=bool(kernel.SetConsoleMode(ctypes.c_void_p(handle),mode.value|4))
    console=Console(width=args.width,height=args.height,no_color=args.no_color or None,
                    legacy_windows=False if vt_enabled else None,record=bool(args.export_svg))
    reader=SnapshotReader(args.root)
    def frame():return render(reader.read(),console.width,console.height)
    if os.name=='nt' and not args.once and console.is_terminal and not str(args.root).startswith('\\\\'):
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW('BarGPT Phase 2 - worker progress')
        try:
            write=dict(pid=os.getpid(),created=psutil.Process().create_time(),hwnd=int(ctypes.windll.kernel32.GetConsoleWindow()),title='BarGPT Phase 2 - worker progress')
            (args.root.parent/'corrected-restart-20261110/rich-panel-window.json').write_text(json.dumps(write))
        except OSError:pass
    if args.once or not console.is_terminal:
        console.print(frame())
        if args.export_svg:
            args.export_svg.parent.mkdir(parents=True,exist_ok=True)
            console.save_svg(str(args.export_svg),title='BarGPT phase-two worker progress')
        return 0
    try:
        with Live(frame(),console=console,screen=True,refresh_per_second=1) as live:
            while True:
                time.sleep(1)
                live.update(frame())
    except KeyboardInterrupt:
        console.print('Progress panel closed. Data generation is unchanged.')
    return 0


if __name__=='__main__':raise SystemExit(main())
