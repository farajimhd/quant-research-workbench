"""Detailed, read-only Phase-1 rankings; no imports from optimizer runners."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,mmap,re,time,threading,math
from pathlib import Path
from collections import OrderedDict
import psutil
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
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
        self.active=Path(active);self.day=None;self.data={};self.lock=threading.Lock();self.stop=threading.Event();self.cache=OrderedDict();self.genomes=OrderedDict();self.cp_stamps={};self.archive_stamps={}
        self.thread=threading.Thread(target=self.pump,daemon=True,name='v20-detail-reader')
    def start(self):self.thread.start();return self
    def close(self):self.stop.set();self.thread.join(2)
    def read(self):
        with self.lock:return dict(self.data)
    def select(self,day):self.day=day
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
            self.stop.wait(3)


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
    if row is None:return ['Awaiting the selected sessionâ€™s first sealed generation.']
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


def bar(label,done,total,width):
    """Only completed units fill the bar; an in-flight generation stays unfilled."""
    done=max(0,min(done,total));size=max(6,min(24,width-len(label)-20));filled=int(size*done/total) if total else 0
    return f"{label} [{'#'*filled}{'-'*(size-filled)}] {done}/{total}"


def progress_lines(data,width):
    days=data.get('days',[]);target=data.get('target',30);workers=data.get('workers',{})
    lines=[bar('Phase 1 sessions',data.get('completed_sessions',0),len(days),width),f"{len(workers)} active | {data.get('queued',0)} queued | {data.get('failed',0)} failed | Phase 2 waits for all sessions"]
    for day,w in sorted(workers.items()):
        done=w.get('completed_generations',0);stage=w.get('stage','Starting');sub=''
        if stage=='Evaluate rules' and w.get('total_rule_tiles'):
            sub=f" | rule tiles {w.get('rule_tiles',0)}/{w['total_rule_tiles']}"
        lines.append(bar(f"{day} generations",done,w.get('total_generations',target),width))
        lines.append(f"  NOW: generation {w.get('generation',done+1)} - {stage}{sub}")
    selected=data.get('day','pending');done=data.get('generation',0);wall=data.get('last_wall')
    label='COMPLETE' if done>=target else ('ACTIVE' if selected in workers else 'QUEUED')
    lines.append(f"Viewing {selected}: {label} | sealed generation {done}/{target}"+(f" | last {wall:.1f}s" if wall else ''))
    if data.get('display_error'):lines.append('STALE DISPLAY: '+data['display_error'])
    return lines


def render(data,width=110,height=38,mode='profit',selection=0,view='financial',stage='entry',detail_page=0):
    rows=ordered_rows(data.get('rows',[]),mode);selection=min(max(0,selection),max(0,len(rows)-1));detail_height=10 if height>=34 else 5
    progress=progress_lines(data,width);progress_height=len(progress)
    page_size=max(1,min(50,height-(3+progress_height+detail_height+4+4)));page=selection//page_size
    layout=Layout();layout.split_column(Layout(name='header',size=3),Layout(name='progress',size=progress_height),Layout(name='leaders',ratio=1),Layout(name='details',size=detail_height),Layout(name='keys',size=4))
    days=data.get('days',[]);day=data.get('day','pending');number=days.index(day)+1 if day in days else 0
    layout['header'].update(Text(f"V20 {'RUNNING' if data.get('running') else 'OPTIMIZER STOPPED'} | PHASE 1 | Session {number}/{len(days)}: {day}\nDetailed training rankings | sealed generation {data.get('generation',0)} | sort: {mode} | read only",style='cyan' if data.get('running') else 'bold red'))
    layout['progress'].update(Text('\n'.join(progress),style='yellow' if data.get('display_error') else 'green'))
    table=Table(expand=True,padding=(0,0));columns=['Rank','Serial','Net P&L $','Win %','Positions','Risk $','E']
    if width>=95:columns=['Rank','Serial','Local score','Net P&L $','Win %','Positions','Risk $','Stop %','E']
    for c in columns:table.add_column(c,justify='right',no_wrap=True)
    for i,row in enumerate(rows[page*page_size:(page+1)*page_size],page*page_size):
        m=row['metrics'];values=[str(row['rank'])+(' *' if i==selection else ''),str(row['serial']),f"{m['net_pnl']:,.1f}",'-' if m['win_rate'] is None else f"{100*m['win_rate']:.1f}",str(int(m['closed_positions'])),f"{m['maximum_position_risk']:.1f}",'Y' if row['eligible'] else 'N']
        if width>=95:values.insert(2,f"{row['score']:,.1f}");values.insert(-1,f"{100*m['maximum_stop_distance']:.2f}")
        table.add_row(*values,style='bold cyan' if i==selection else None)
    layout['leaders'].update(Panel(table,title=f"Session ranking | {len(rows)} candidates | page {page+1}/{max(1,math.ceil(len(rows)/page_size))} | E = eligible parent"))
    row=rows[selection] if rows else None;payload=data.get('payloads',{}).get(row['serial']) if row else None
    lines=detail_lines(row,payload,view,stage);available=max(1,detail_height-2)
    # Wrap before pagination so long expressions remain fully accessible.
    import textwrap
    wrapped=[part for line in lines for part in textwrap.wrap(line,max(20,width-4),replace_whitespace=False,drop_whitespace=False) or ['']]
    pages=max(1,math.ceil(len(wrapped)/available));p=detail_page%pages
    layout['details'].update(Panel(Text('\n'.join(wrapped[p*available:(p+1)*available])),title=f"Candidate {row['serial'] if row else '-'} | {view}{' / '+stage if view=='rules' else ''} | detail {p+1}/{pages}"))
    layout['keys'].update(Text('[/] session | V follow active | J/K candidate | N/B page\n1 profit 2 score 3 win 4 risk | F financial O score P policy\nE/X/A/D/T rules: entry/exit/add/reduce/trail | U/I detail\nQ close observer | Rank = profit rank. N = ineligible parent.'))
    return layout


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--active',type=Path,default=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v20/active-campaign.json'));parser.add_argument('--once',action='store_true');args=parser.parse_args()
    proc=psutil.Process()
    try:
        if os.name=='nt':proc.nice(psutil.IDLE_PRIORITY_CLASS);proc.ionice(1)
        proc.cpu_affinity([proc.cpu_affinity()[-1]])
    except (psutil.Error,AttributeError):pass
    console=Console();observer=DetailObserver(args.active).start();selection=0;mode='profit';view='financial';stage='entry';detail_page=0;switch=None
    if os.name=='nt' and console.is_terminal:
        import ctypes
        from ctypes import wintypes
        k=ctypes.windll.kernel32;k.SetConsoleTitleW('V20 detailed session monitor - read only');k.GetConsoleWindow.restype=wintypes.HWND
        u=ctypes.windll.user32;u.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];u.ShowWindow(k.GetConsoleWindow(),3)
    try:
        with Live(console=console,screen=console.is_terminal and not args.once,auto_refresh=False) as live:
            while True:
                data=observer.read()
                if data.get('global_output'):switch=Path(data['global_output']);break
                live.update(render(data,console.width,console.height,mode,selection,view,stage,detail_page),refresh=True)
                if args.once:
                    if not data and observer.thread.is_alive():time.sleep(.1);continue
                    break
                if os.name=='nt':
                    import msvcrt
                    while msvcrt.kbhit():
                        key=msvcrt.getwch().lower();days=data.get('days',[])
                        if key=='q':return 0
                        if key in '[]' and days:
                            i=days.index(observer.day if observer.day in days else data['day']);observer.select(days[(i+(1 if key==']' else -1))%len(days)]);selection=0;detail_page=0
                        elif key=='v':observer.select(None);selection=0;detail_page=0
                        elif key in '1234':mode={'1':'profit','2':'score','3':'win','4':'risk'}[key];selection=0
                        elif key in ('j','k','n','b'):
                            step=1 if key in ('j','k') else max(1,min(50,console.height-(3+len(progress_lines(data,console.width))+(10 if console.height>=34 else 5)+8)))
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
