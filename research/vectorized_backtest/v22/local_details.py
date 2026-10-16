"""Detailed, read-only Phase-1 rankings; no imports from optimizer runners."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,mmap,re,time,threading,math
from pathlib import Path
from collections import OrderedDict
from contextlib import nullcontext
import psutil
from rich.console import Console
from .fixed_terminal import FixedTerminal
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from .progress_io import read_progress,shared_binary

COLUMNS=('net_pnl','closed_positions','winning_positions','drawdown','maximum_position_risk','maximum_stop_distance','terminal_valid','capital_dollar_seconds','stop_risk_dollar_seconds','turnover','worst_position_pnl')
PATTERN=re.compile(rb'"('+b'|'.join(k.encode() for k in COLUMNS)+rb')"\s*:\s*\[')


def metric_columns(path,expected_token=None):
    """Project flat summary arrays in one scan; never parse the position ledger."""
    result={}
    with shared_binary(path) as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
        if expected_token is not None:
            identity=re.search(rb'"population_sha256"\s*:\s*"([^"]+)"',data)
            if identity is None or identity.group(1).decode()!=expected_token:raise ValueError('Display population identity mismatch')
        for match in PATTERN.finditer(data):
            key=match.group(1).decode();end=data.find(b']',match.end())
            if end<0 or key in result:raise ValueError('Malformed or duplicate display metric')
            result[key]=json.loads(data[match.end()-1:end+1])
    if any(k not in result for k in COLUMNS):raise ValueError('Required display metrics missing')
    return result


def load_generation(folder,generation):
    root=folder/f'generation-{generation:04d}';seal=read_progress(root/'complete.json')
    if not seal.get('population_sha256'):raise ValueError('Awaiting sealed generation')
    scores=read_progress(root/'scores.json');metrics=metric_columns(root/folder.name/'receipt.json',seal['population_sha256']);ids=scores['ids'];n=len(ids)
    if any(len(v)!=n for v in metrics.values()) or len(scores['scores'])!=n or len(scores['eligible'])!=n:raise ValueError('Display candidate alignment changed')
    order=sorted(range(n),key=lambda i:(-metrics['net_pnl'][i],ids[i]));ranks={i:p+1 for p,i in enumerate(order)}
    rows=[]
    for i in range(n):
        m={k:v[i] for k,v in metrics.items()};closed=m['closed_positions'];m['win_rate']=m['winning_positions']/closed if closed else None
        rows.append(dict(serial=ids[i],rank=ranks[i],score=scores['scores'][i],eligible=scores['eligible'][i],metrics=m))
    return rows


class DetailObserver:
    """One idle-priority reader, selected session only, two-generation cache."""
    def __init__(self,active):
        self.active=Path(active);self.day=None;self.data={};self.lock=threading.Lock();self.stop=threading.Event();self.wake=threading.Event();self.cache=OrderedDict();self.genomes=OrderedDict();self.cp_stamps={};self.archive_stamps={}
        self.thread=threading.Thread(target=self.pump,daemon=True,name='v22-detail-reader')
    def start(self):self.thread.start();return self
    def close(self):self.stop.set();self.wake.set();self.thread.join(2)
    def read(self):
        with self.lock:return dict(self.data)
    def select(self,day):
        self.day=day
        with self.lock:
            self.data={**self.data,'day':day,'rows':[],'payloads':{},'generation':0,'session_progress':{}}
        self.wake.set()
    def refresh(self):
        owner=read_progress(self.active);root=Path(owner['campaign']);status=read_progress(root/'status.json');manifest=read_progress(root/'manifest.json');days=manifest.get('days',[])
        workers=status.get('workers',{})
        day=self.day if self.day in days else next((d for d in days if d in workers and workers[d].get('status')=='running'),next((d for d in days if (root/'local'/d/'checkpoint.json').exists()),days[0] if days else None))
        running=False
        try:running=abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01
        except (psutil.NoSuchProcess,KeyError,TypeError):pass
        state=dict(root=str(root),days=days,day=day,status=status.get('status','starting'),stage=status.get('stage',''),running=running,
                   completed_sessions=status.get('completed',0),target=manifest.get('local_generations',30),rows=[],payloads={},generation=0,
                   workers=workers,queued=status.get('queued',0),failed=status.get('failed',0),updated_epoch=status.get('updated_epoch'),global_output=str(root/'global') if (root/'global'/'status.json').exists() else None)
        if day:
            folder=root/'local'/day;cp=folder/'checkpoint.json';key=(str(root),day)
            state['session_progress']=read_progress(folder/'status.json')
            if cp.exists():
                stamp=cp.stat().st_mtime_ns
                if self.cp_stamps.get(key,(None,))[0]!=stamp:
                    checkpoint=read_progress(cp)
                    if 'state' not in checkpoint:raise ValueError('Checkpoint snapshot temporarily unavailable')
                    current=checkpoint['state'];payloads=self.genomes.setdefault(key,OrderedDict())
                    for serial,payload in zip(current['ids'],current['population']):payloads[serial]=payload;payloads.move_to_end(serial)
                    for expert in current['archive']:payloads[expert['serial']]=expert['payload'];payloads.move_to_end(expert['serial'])
                    while len(payloads)>3072:payloads.popitem(last=False)
                    self.cp_stamps[key]=(stamp,current['completed']);self.genomes.move_to_end(key)
                    while len(self.genomes)>2:
                        evicted,_=self.genomes.popitem(last=False);self.cp_stamps.pop(evicted,None)
                generation=self.cp_stamps[key][1];state['generation']=generation
                cache_key=(str(root),day,generation)
                if generation and cache_key not in self.cache:
                    self.cache[cache_key]=(load_generation(folder,generation),read_progress(folder/f'generation-{generation:04d}'/'wall.json').get('full_generation_seconds'))
                    while len(self.cache)>2:self.cache.popitem(last=False)
                state['rows'],state['last_wall']=self.cache.get(cache_key,([],None));state['payloads']=dict(self.genomes.get(key,{}))
        if self.day is None or self.day==day or self.day not in days:
            with self.lock:self.data=state
    def pump(self):
        while not self.stop.is_set():
            try:self.refresh()
            except (OSError,ValueError,KeyError,TypeError) as error:
                with self.lock:self.data={**self.data,'display_error':str(error)}
            self.wake.wait(3);self.wake.clear()


def ordered_rows(rows,mode):
    field={'profit':lambda r:r['metrics']['net_pnl'],'score':lambda r:r['score'],'win':lambda r:r['metrics']['win_rate'] if r['metrics']['win_rate'] is not None else -1,
           'risk':lambda r:-r['metrics']['maximum_position_risk']}[mode]
    return sorted(rows,key=lambda r:(-field(r),r['serial']))


def rule_lines(payload,stage):
    if payload is None:return ['Rules unavailable: this historical genome was not retained before observer attachment.','Final populations and archived experts retain complete rules.']
    from .features import CATALOG
    program=payload['rules'][stage] if stage=='entry' or payload.get('open_rules') is None else payload['open_rules'][stage]
    nodes=program['nodes'];used=set()
    def visit(i):
        if i in used:return
        used.add(i);node=nodes[i]
        for k in ('a','b'):
            if node[k]>=0:visit(node[k])
    visit(program['output']);lines=[];binary={2:'+',3:'-',4:'*',5:'/',6:'>',7:'>=',8:'<',9:'<=',10:'AND',11:'OR',18:'CROSS_ABOVE',19:'CROSS_BELOW'}
    unary={12:'NOT',13:'LAG',14:'MEAN',15:'MIN',16:'MAX',17:'DIFFERENCE',20:'ABS',21:'BOOL_RATIO',23:'STD'}
    for i in sorted(used):
        n=nodes[i];op=n['op']
        if op==0:value=CATALOG[n['feature']].name
        elif op==1:value=f"{n['value']:.10g} [{n['unit']}]"
        elif op in binary:value=f"(n{n['a']} {binary[op]} n{n['b']})"
        else:value=f"{unary.get(op,str(op))}(n{n['a']}, window={n['window']})"
        lines.append(f'n{i} = {value}')
    lines.append(f"OUTPUT = n{program['output']} | minimum age {payload.get('minimum_age',{}).get(stage,0)} seconds")
    if stage=='add' and payload['policy']['maximum_adds']==0:lines.insert(0,'DISABLED: maximum_adds = 0')
    return lines


def detail_lines(row,payload,view,stage):
    if row is None:return ['No sealed ranking yet for this session. Active sessions publish results after each complete generation.']
    m=row['metrics'];win='â€”' if m['win_rate'] is None else f"{100*m['win_rate']:.2f}%"
    if view=='rules':return rule_lines(payload,stage)
    if view=='objective':return [f"Saved local score {row['score']:,.4f} | parent eligible: {row['eligible']}",'Local eligibility: positive trade-trimmed profit, at least three completed positions, valid financial/risk checks.',f"Observed drawdown ${m['drawdown']:,.2f} | peak stock risk ${m['maximum_position_risk']:,.2f}",'Exact trade trimming and tail-loss terms are not in this summary projection; trade ledgers are deliberately not reprocessed.']
    if view=='policy':
        if payload is None:return rule_lines(None,stage)
        p=payload['policy'];stop=f"Swing low: {p['swing_left']} left / {p['swing_right']} right" if p['swing_left'] else f"Initial fixed stop: {100*p['stop_fraction']:.3f}%"
        return [stop,f"Target: +{100*p['target_fraction']:.3f}% | reduce fraction {100*p['reduce_fraction']:.3f}%",
            f"Adds: at most {p['maximum_adds']} x $200 after +{100*p['add_minimum_profit']:.3f}% | initial $1000",
            f"Reduction signal profit gate: +{100*p['reduce_minimum_profit']:.3f}% | cooldown {p['cooldown']} s",
            f"Trailing distance: {100*p['trail_fraction']:.3f}% | age gates {payload.get('minimum_age',{})}",
            'Actual stop distance may widen after additions; observed risk is shown separately.']
    return [f"Net profit ${m['net_pnl']:,.2f} | local score {row['score']:,.2f} | parent eligible: {row['eligible']}",
        f"Win rate {win} | completed positions {m['closed_positions']:.0f} | wins {m['winning_positions']:.0f}",
        f"Peak planned stock risk ${m['maximum_position_risk']:,.2f} / $300 | maximum stop distance {100*m['maximum_stop_distance']:.3f}% / 20%",
        f"Maximum drawdown ${m['drawdown']:,.2f} | worst position ${m.get('worst_position_pnl',0):,.2f}",
        f"Capital hours {m.get('capital_dollar_seconds',0)/3600:,.2f} | stop-risk hours {m.get('stop_risk_dollar_seconds',0)/3600:,.2f}",
        'Training-session results only. No cross-session or validation performance implied.']


def ranking_size(height):
    return max(1,min(50,height-(2+4+(10 if height>=34 else 5)+(2 if height>=34 else 1)+(3 if height>=34 else 1)+2+4)))


def session_layout(data,width,height):
    """Use the Phase-2 canvas; adapt only the session-local data and labels."""
    from .terminal import render as phase2_render
    from rich.progress import Progress,TextColumn,BarColumn,TaskProgressColumn
    from rich.table import Column
    day=data.get('day','pending');target=data.get('target',30);done=data.get('generation',0)
    lane=data.get('workers',{}).get(day,data.get('session_progress',{}));complete=done>=target
    state='complete' if complete else lane.get('status','queued') if data.get('running') else 'stopped'
    stage='All local generations completed' if complete else lane.get('stage','Waiting for session worker')
    layout=phase2_render(dict(status=state,stage=stage,config=dict(population=1024,generations=target,training_sessions=1),top_strategies=[]),width=width,height=height)
    days=data.get('days',[]);number=days.index(day)+1 if day in days else 0
    current=f"Generation {lane.get('generation',done+1)}: " if not complete else ''
    layout['header'].update(Text(f"V22 PHASE 1 | SESSION {number}/{len(days)}: {day} | {state.upper()}\n{current}{stage} | {data.get('completed_sessions',0)}/{len(days)} sessions complete",style='cyan'))
    bars=Progress(TextColumn('{task.description}',table_column=Column(width=19,no_wrap=True)),BarColumn(bar_width=None),TaskProgressColumn(),TextColumn('{task.fields[units]}',table_column=Column(width=16,no_wrap=True)),expand=True)
    tasks=[('Session generations',done,target)]
    if not complete:
        if lane.get('total_rule_tiles'):tasks.append(('Current batch rules',lane.get('rule_tiles',0),lane['total_rule_tiles']))
        if lane.get('total_seconds'):tasks.append(('Backtest timestamps',lane.get('completed_seconds',0),lane['total_seconds']))
        if lane.get('completed_local_batches') is not None:tasks.append(('Candidate batches',lane['completed_local_batches'],2))
    for label,count,total in tasks:
        bars.add_task(label,total=total,completed=count,units=f'{count:,}/{total:,}')
    layout['progress'].update(bars);layout['progress'].size=4
    layout['leaders'].size=ranking_size(height)+4
    layout['metrics'].size=10 if height>=34 else 5
    layout['timing'].size=2 if height>=34 else 1
    wall=data.get('last_wall');timing=f"Last full generation: {wall:.1f}s" if wall else 'Timing: awaiting first completed generation'
    if wall and not complete:timing+=f" | session remaining ~{max(0,target-done)*wall/60:.1f} min (estimate)"
    layout['timing'].update(Text(timing))
    layout['messages'].size=3 if height>=34 else 1
    note=data.get('display_error') or 'One session only. Rank = profit rank. E = eligible parent. Phase 2 waits for all sessions.'
    layout['messages'].update(Panel(Text(note),title='STALE DISPLAY' if data.get('display_error') else 'Session scope',padding=0) if height>=34 else Text(data.get('display_error') or 'Session only | Rank = profit rank | E = eligible parent'))
    layout['keys'].size=2
    keys='S next session | W previous | J/K candidate | N/B ranking page | 1 profit 2 score 3 win 4 risk\nF financial O score P policy | E/X/A/D/T rules | U/I detail page | Q close observer' if width>=105 else 'S/W session | J/K candidate | N/B page | F/O/P details\n1-4 sort | E/X/A/D/T rules | U/I detail page | Q close'
    layout['keys'].update(Text(keys))
    return layout


def render(data,width=110,height=38,mode='profit',selection=0,view='financial',stage='entry',detail_page=0):
    layout=session_layout(data,width,height)
    rows=ordered_rows(data.get('rows',[]),mode);selection=min(max(0,selection),max(0,len(rows)-1));page_size=ranking_size(height);page=selection//page_size
    table=Table(expand=True,padding=(0,0),show_edge=False)
    columns=['Rank','Serial','Net P&L $','Win %','Positions','Risk $','E']
    if width>=95:columns=['Rank','Serial','Local score','Net P&L $','Win %','Positions','Risk $','Stop %','E']
    for c in columns:table.add_column(c,justify='right',no_wrap=True)
    for i,row in enumerate(rows[page*page_size:(page+1)*page_size],page*page_size):
        m=row['metrics'];values=[str(row['rank'])+(' *' if i==selection else ''),str(row['serial']),f"{m['net_pnl']:,.1f}",'-' if m['win_rate'] is None else f"{100*m['win_rate']:.1f}",str(int(m['closed_positions'])),f"{m['maximum_position_risk']:.1f}",'Y' if row['eligible'] else 'N']
        if width>=95:values.insert(2,f"{row['score']:,.1f}");values.insert(-1,f"{100*m['maximum_stop_distance']:.2f}")
        table.add_row(*values,style='bold cyan' if i==selection else None)
    title=f"{data.get('day','pending')} ranking | sealed gen {data.get('generation',0)} | {len(rows)} candidates | {mode} | page {page+1}/{max(1,math.ceil(len(rows)/page_size))}" if width>=105 else f"{data.get('day','pending')} | gen {data.get('generation',0)} | {mode} | page {page+1}/{max(1,math.ceil(len(rows)/page_size))}"
    layout['leaders'].update(Panel(table,title=title,padding=0))
    row=rows[selection] if rows else None;payload=data.get('payloads',{}).get(row['serial']) if row else None
    lines=detail_lines(row,payload,view,stage);available=max(1,layout['metrics'].size-2)
    import textwrap
    wrapped=[part for line in lines for part in textwrap.wrap(line,max(20,width-4),replace_whitespace=False,drop_whitespace=False) or ['']]
    pages=max(1,math.ceil(len(wrapped)/available));p=detail_page%pages
    layout['metrics'].update(Panel(Text('\n'.join(wrapped[p*available:(p+1)*available])),title=f"Candidate {row['serial'] if row else '-'} | {view}{' / '+stage if view=='rules' else ''} | detail {p+1}/{pages}"))
    return layout


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--active',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v22/active-campaign.json'));parser.add_argument('--once',action='store_true');args=parser.parse_args()
    proc=psutil.Process()
    try:
        if os.name=='nt':proc.nice(psutil.IDLE_PRIORITY_CLASS);proc.ionice(1)
        proc.cpu_affinity([proc.cpu_affinity()[-1]])
    except (psutil.Error,AttributeError):pass
    console=Console();once=args.once or not console.is_terminal;observer=DetailObserver(args.active).start();selection=0;mode='profit';view='financial';stage='entry';detail_page=0;switch=None
    if os.name=='nt' and console.is_terminal:
        import ctypes
        from ctypes import wintypes
        k=ctypes.windll.kernel32;k.SetConsoleTitleW('V22 detailed session monitor - read only');k.GetConsoleWindow.restype=wintypes.HWND
        u=ctypes.windll.user32;u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];u.ShowWindow(k.GetConsoleWindow(),3)
    try:
        with FixedTerminal(console) if not once else nullcontext() as live:
            while True:
                data=observer.read()
                if not data:time.sleep(.1);continue
                if data.get('global_output'):switch=Path(data['global_output']);break
                if live is not None:live.update(lambda w,h:render(data,w,h,mode,selection,view,stage,detail_page))
                else:console.print(render(data,console.width,console.height,mode,selection,view,stage,detail_page))
                if once:break
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch().lower();days=data.get('days',[])
                        if key=='q':return 0
                        if key in '[]sw' and days:
                            i=days.index(observer.day if observer.day in days else data['day']);observer.select(days[(i+(1 if key in (']','s') else -1))%len(days)]);selection=0;detail_page=0
                        elif key=='v':observer.select(None);selection=0;detail_page=0
                        elif key in '1234':mode={'1':'profit','2':'score','3':'win','4':'risk'}[key];selection=0
                        elif key in ('j','k','n','b'):
                            step=1 if key in ('j','k') else ranking_size(live.dimensions()[1] if live is not None else console.height)
                            selection=max(0,min(max(0,len(data.get('rows',[]))-1),selection+(step if key in ('j','n') else -step)));detail_page=0
                        elif key in ('f','o','p','r'):view={'f':'financial','o':'objective','p':'policy','r':'rules'}[key];detail_page=0
                        elif key in 'exadt':view='rules';stage={'e':'entry','x':'exit','a':'add','d':'reduce','t':'trail'}[key];detail_page=0
                        elif key in ('u','i'):detail_page=max(0,detail_page+(1 if key=='i' else -1))
                time.sleep(.2)
    except KeyboardInterrupt:pass
    finally:observer.close()
    if switch:
        from .observe import main as global_panel
        return global_panel(['--output',str(switch)])
    return 0


if __name__=='__main__':raise SystemExit(main())
