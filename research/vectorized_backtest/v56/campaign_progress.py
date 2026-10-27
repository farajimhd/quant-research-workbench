"""V48-style multi-device overview, session drill-down, and evolution attribution."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from rich.console import Console
from rich.layout import Layout
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from research.vectorized_backtest.v27.fixed_terminal import FixedTerminal
from research.vectorized_backtest.v42.distributed_assets import remote

def number(value):return '-' if value is None else f'{value:,.2f}'

def diagnostics(value,prefix=''):
    rows=[]
    for key,item in value.items():
        if isinstance(item,dict):rows.extend(diagnostics(item,prefix+key+'.'))
        else:rows.append((prefix+key,'unavailable' if item is None else str(item)))
    return rows

class Model:
    def __init__(self,job):
        self.job=job;self.root=Path(job['campaign']);self.cache={};self.reports={};self.last_error=None
        self.bus=remote(job['transport'],job['workstation_share'])
        self.day=None;self.view='overview';self.selection=0;self.sort='score';self.reverse=False;self.filter=None;self.selected=None;self.validation=False;self.detail_page=0;self.snapshot_cache=None
    def read(self,path,default=None):
        path=Path(path);key=str(path)
        try:
            stamp=(path.stat().st_mtime_ns,path.stat().st_size)
            if key not in self.cache or self.cache[key][0]!=stamp:self.cache[key]=(stamp,json.loads(path.read_bytes()))
            if len(self.cache)>64:self.cache.pop(next(iter(self.cache)))
            return self.cache[key][1]
        except (OSError,ValueError) as error:
            self.last_error=str(error)
            return self.cache.get(key,(None,default or {}))[1]
    def session_root(self,day):
        node=next(n for n,m in self.job['machines'].items() if day in m['session_days'])
        path=self.root/'sessions'/day
        return path if node=='laptop' else remote(path,self.job['workstation_share'])
    def target(self):return self.session_root(self.day) if self.day else remote(self.root/'phase-2',self.job['workstation_share'])
    def status(self):return self.read(self.target()/'full-status.json')
    def report(self):
        root=self.target()/'global';report=self.read(root/'evolution-report.json')
        if report:return report
        status=self.status();g=status.get('ranking_generation',status.get('completed_generations',0))
        if not g:return dict(generation=0,groups=[],candidates=[])
        key=(str(root),g)
        if key not in self.reports:
            from .evolution_report import historical
            try:self.reports[key]=historical(root/f'generation-{g:04d}')
            except (OSError,ValueError,KeyError):return dict(generation=g,groups=[],candidates=[],unavailable=True)
            if len(self.reports)>26:self.reports.pop(next(iter(self.reports)))
        return self.reports[key]
    def snapshot(self):
        if self.snapshot_cache and time.monotonic()-self.snapshot_cache[0]<1:return self.snapshot_cache[1]
        nodes={n:self.read(self.bus/(n+'-status.json')) for n in self.job['machines']};sessions=[]
        stopped=(self.root/'STOP').exists() or (self.bus/'STOP').exists()
        for day in self.job['training_days']:
            node=next(n for n,m in self.job['machines'].items() if day in m['session_days']);ns=nodes[node]
            s=self.read(self.session_root(day)/'full-status.json');done=s.get('completed_generations',0)
            complete=day in ns.get('completed',[]) or (self.bus/'sessions'/day/'export.json').exists()
            active=day in ns.get('active_sessions',[]) or ns.get('active')==day
            state='complete' if complete else 'stopped' if stopped else s.get('status','running') if active else 'queued'
            if active and not stopped and time.time()-s.get('updated_epoch',0)>15:state='STALE'
            progress=s.get('worker_progress',{}) if active and not complete else {};stage=progress.get('stage',s.get('stage','')) if active and not complete else ''
            sessions.append(dict(day=day,node=node,done=50 if complete else done,state=state,stage=stage,progress=progress))
        result=(nodes,sessions,stopped);self.snapshot_cache=(time.monotonic(),result);return result
    def rows(self):
        if self.view=='overview':return self.snapshot()[1]
        if self.view=='reproduction':return diagnostics(self.report().get('next_generation_reproduction') or {})
        if self.view in ('operators','families','methods'):
            category={'operators':'operator','families':'family','methods':'founder-method'}[self.view]
            return [g for g in self.report().get('groups',[]) if g['category']==category]
        if self.view=='founders':rows=list(self.read(self.target()/'global/founder-results.json').values())
        elif self.view=='ranking':
            details={r['serial']:r for r in self.report().get('candidates',[])}
            return [dict(details.get(r['serial'],{}),**r) for r in self.status().get('validation_ranking' if self.validation else 'training_ranking',[])]
        else:rows=self.report().get('candidates',[])
        if self.filter:
            kind,value=self.filter
            if kind=='descendant':rows=[r for r in rows if not r.get('original_founder') and int(r.get('founder_mask','0'),16)&(1<<value)]
            else:rows=[r for r in rows if value in r.get(kind,[])]
        reverse=self.sort not in ('gross_losses','drawdown','nodes')
        rows=sorted(rows,key=lambda r:(r.get(self.sort) is not None,r.get(self.sort) or 0),reverse=reverse!=self.reverse)
        return rows
    def key(self,key,height=40):
        if self.view in ('detail','lineage') and key in ('n','b'):
            self.detail_page=max(0,self.detail_page+(1 if key=='n' else -1));return
        if key=='l' and self.selected:self.view='lineage';self.detail_page=0;return
        if key=='v':self.view='reproduction';self.selection=0;return
        if key in ('h','\x1b'):self.view='overview';self.day=None;self.selection=0;self.filter=None;return
        if key=='g':self.day=None;self.view='ranking';self.selection=0;self.filter=None;return
        if key in ('o','f','m','a','i'):
            self.view={'o':'operators','f':'families','m':'methods','a':'candidates','i':'founders'}[key];self.selection=0;self.filter=None;return
        if key=='t':self.validation=not self.validation;return
        if key=='s':
            fields=['score','net_pnl','gross_losses','drawdown','nodes'];self.sort=fields[(fields.index(self.sort)+1)%len(fields)];return
        if key=='r':self.reverse=not self.reverse;return
        if key=='u' and self.selected and self.selected.get('founder_index') is not None:
            self.filter=('descendant',self.selected['founder_index']);self.view='candidates';self.selection=0;return
        rows=self.rows();step=max(1,height-12)
        if key in ('j','k','n','b'):self.selection=max(0,min(len(rows)-1,self.selection+{'j':1,'k':-1,'n':step,'b':-step}[key]));return
        if key in ('\r','\n') and rows and self.view not in ('reproduction','detail','lineage'):
            row=rows[min(self.selection,len(rows)-1)]
            if self.view=='overview':self.day=row['day'];self.view='ranking';self.selection=0
            elif self.view in ('operators','families','methods'):
                self.filter=({'operators':'operators','families':'founder_groups','methods':'founder_methods'}[self.view],row['group']);self.view='candidates';self.selection=0
            else:self.selected=row;self.view='detail';self.detail_page=0

def render(model,width,height):
    layout=Layout();layout.split_column(Layout(name='head',size=3),Layout(name='body'),Layout(name='keys',size=4))
    title=f"V56 MULTI-DEVICE | {'SESSION '+model.day if model.day else 'GLOBAL / OVERVIEW'} | 50 session + 50 global"
    note='Score: P&L - losses - 2 x DD | risk caps OFF | validation display-only'
    table=Table(expand=True,padding=(0,0));limit=max(1,height-12);start=model.selection//limit*limit
    if model.view=='overview':
        nodes,rows,stopped=model.snapshot();done=sum(r['state']=='complete' for r in rows)
        stage='STOPPED' if stopped else ' | '.join(f"{n}: {s.get('status','unavailable')} ({len(s.get('active_sessions',[]))} active)" for n,s in nodes.items())
        if nodes.get('workstation',{}).get('status')=='global-stage':
            state=model.read(remote(model.root/'phase-2/full-status.json',model.job['workstation_share']));initial=state.get('initialization_progress',{})
            stage+=f" | global {state.get('completed_generations',0)}/50"
            if initial:stage+=f" | blind {initial['completed']}/{initial['total']}"
        title+=f' | {done}/24 complete';note=stage+'\n'+note
        for h in ('Session','Host','Sealed','State','Activity'):table.add_column(h,no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):
            p=r['progress'];activity=r['stage']+(f" {p.get('completed_seconds',0)}/{p.get('total_seconds',0)} clocks" if p else '')
            table.add_row(r['day'],r['node'],f"{r['done']}/50",r['state'],activity,style='bold cyan' if i==model.selection else None)
        caption='J/K select a session; Enter opens its results. Completed exports and saved generations are preserved.'
    elif model.view in ('operators','families','methods'):
        report=model.report();rows=model.rows()
        for h in ('Group','Eval','Keep','Best $','Median $','Change $','Loss $','DD $','Nodes','Beat parent'):table.add_column(h,no_wrap=True,justify='right')
        for i,r in enumerate(rows[start:start+limit],start):table.add_row(r['group'],str(r['evaluated']),str(r['retained']) if r['retained'] is not None else '-',number(r['best_score']),number(r['median_score']),number(r.get('median_score_change')),number(r['median_loss']),number(r['median_drawdown']),number(r.get('median_nodes')),f"{r['beat_best_parent']}/{r['parent_comparisons']}",style='bold cyan' if i==model.selection else None)
        caption=f"TRAINING | generation {report.get('generation',0)} | Overlapping groups; Enter lists members. Missing historical fields are '-'."
        if not rows:table.add_column('Status') if not table.columns else None;table.add_row('Not evaluated / unavailable',*['']*(len(table.columns)-1))
    elif model.view=='reproduction':
        report=model.report();items=model.rows()
        table.add_column('Reproduction diagnostic');table.add_column('Observed value',justify='right')
        for name,value in items[start:start+limit]:table.add_row(name,value)
        if not items:table.add_row('No reproduction evidence','unavailable')
        caption=f"Successor creation after measured generation {report.get('generation',0)}; not the evaluated population's scores."
    elif model.view=='lineage':
        row=model.selected or {};mix=row.get('mix') or {};path=mix.get('provenance_file')
        detail=model.read(remote(path,model.job['workstation_share'])) if path else {}
        for h in ('Step','Parents','Operator','Depth'):table.add_column(h)
        steps=detail.get('steps',[]);offset=model.detail_page*limit
        for step in steps[offset:offset+limit]:table.add_row(step['id'],', '.join(step['parents']),step['mode'],str(step['depth']))
        if not steps:table.add_row('No blind mixing provenance','See candidate birth metadata','-','-')
        caption=f"Candidate {row.get('serial')} | {len(steps)} recorded joins | page {model.detail_page+1} | N/B page"
    elif model.view=='detail':
        r=model.selected or {};mix=r.get('mix') or {};lines=[f"Candidate {r.get('serial')} | {r.get('operation','ranking')} | measured generation {r.get('last_evaluated_generation',r.get('generation',model.report().get('generation')))}",
            f"Score {number(r.get('score'))} | Net {number(r.get('net_pnl'))} | Loss {number(r.get('gross_losses'))} | DD {number(r.get('drawdown'))}",
            f"Original founder: {r.get('original_founder','unavailable')} | families: {', '.join(r.get('founder_groups',[]))}",
            f"Operators: {', '.join(r.get('operators',[]))}",f"Sessions: {', '.join(r.get('source_sessions',[]))}",f"Mix depth: {mix.get('depth','-')} | parents: {mix.get('parent_serials',r.get('parents',[]))}",
            f"Mutations: {mix.get('mutations','see sealed pedigree')} | U lists surviving descendants; founder score is never inherited."]
        table.add_column('Session');table.add_column('Score $',justify='right');table.add_column('Net $',justify='right');table.add_column('Loss $',justify='right');table.add_column('DD $',justify='right')
        generation=r.get('last_evaluated_generation',r.get('generation',model.report().get('generation',0)))
        data=model.read(model.target()/'global'/f'generation-{generation:04d}'/'session-metrics.json')
        if r.get('serial') in data.get('ids',[]):
            index=data['ids'].index(r['serial'])
            page_size=max(1,height-18);offset=model.detail_page*page_size
            for day,p in list(data.get('sessions',{}).items())[offset:offset+page_size]:table.add_row(day,*(number(p[k][index]) for k in ('score','net_pnl','gross_losses','drawdown')))
        else:table.add_row('Per-session detail unavailable','-','-','-','-')
        from rich.console import Group
        table=Group(Text('\n'.join(lines)),table);caption='N/B pages sessions | L mixing lineage | H summary | A current candidates'
    else:
        rows=model.rows()
        for h in ('Serial','Score $','Net $','Loss $','DD $','Nodes','Technique / family'):table.add_column(h,no_wrap=True,justify='right')
        for i,r in enumerate(rows[start:start+limit],start):table.add_row(str(r['serial']),*(number(r.get(k)) for k in ('score','net_pnl','gross_losses','drawdown','nodes')),', '.join(r.get('operators',r.get('founder_groups',[]))),style='bold cyan' if i==model.selection else None)
        status=model.status();caption=f"{'VALIDATION display-only' if model.validation and model.view=='ranking' else 'TRAINING'} | {model.view} | {len(rows)} candidates | sort {model.sort} | {status.get('stage','Not evaluated')}"
        if not rows:table.add_row('Not evaluated',*['']*6)
    layout['head'].update(Text(title+'\n'+note,style='cyan'))
    layout['body'].update(Panel(table,title=model.view.upper(),subtitle=caption,padding=0))
    layout['keys'].update(Text('J/K select | N/B page | Enter details | H summary | G global\nO crossover | F families | M methods | A candidates | I founders\nS sort | R reverse | T train/val | U descendants | L lineage | V attempts\nQ closes this observer only; optimization continues.'))
    return layout

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--view',default='overview');a=p.parse_args()
    model=Model(json.loads(a.job.read_bytes()));model.view=a.view;console=Console()
    if a.once or not console.is_terminal:console.print(render(model,console.width,console.height));return
    if os.name=='nt':
        import ctypes,msvcrt
        handle=msvcrt.get_osfhandle(console.file.fileno());mode=ctypes.c_ulong()
        ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle),ctypes.byref(mode));ctypes.windll.kernel32.SetConsoleMode(ctypes.c_void_p(handle),mode.value|4)
    try:
        with FixedTerminal(console) as live:
            while True:
                live.update(lambda w,h:render(model,w,h))
                until=time.monotonic()+1
                while time.monotonic()<until:
                    if os.name=='nt' and msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q':return
                        model.key(key,live.dimensions()[1]);break
                    time.sleep(.05)
    except KeyboardInterrupt:return
if __name__=='__main__':main()
