"""V48-style multi-device overview, session drill-down, and evolution attribution."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,statistics,math,threading
from pathlib import Path
from rich.console import Console
from rich.layout import Layout
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from research.vectorized_backtest.v27.fixed_terminal import FixedTerminal
from research.vectorized_backtest.v42.distributed_assets import remote

def number(value):return '-' if value is None else f'{value:,.2f}'
SELECTED='bold bright_yellow on blue'

def duration(seconds):
    if seconds is None:return '-'
    seconds=max(0,int(seconds))
    if seconds>=3600:return f'{seconds//3600}h{seconds%3600//60:02d}m'
    if seconds>=60:return f'{seconds//60}m{seconds%60:02d}s'
    return f'{seconds}s'

def diagnostics(value,prefix=''):
    rows=[]
    for key,item in value.items():
        if isinstance(item,dict):rows.extend(diagnostics(item,prefix+key+'.'))
        else:rows.append((prefix+key,'unavailable' if item is None else str(item)))
    return rows

class Model:
    def __init__(self,job):
        self.job=job;self.root=Path(job['campaign']);self.cache={};self.reports={};self.last_error=None;self.timing_cache={};self.strength_cache={}
        self.global_data=None;self.global_loading=None;self.global_error=None;self.global_thread=None;self.observer_stop=threading.Event();self.global_attempt=0.;self.global_day=None;self.global_role=None;self.summary_mode='combined'
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
    def close(self):self.observer_stop.set()
    def strength(self,path,state):
        from .progress_sessions import strength
        key=(str(path),state.get('ranking_generation',state.get('completed_generations',0)))
        if key not in self.strength_cache:
            self.strength_cache={k:v for k,v in self.strength_cache.items() if k[0]!=str(path)}
            self.strength_cache[key]=strength(state.get('training_ranking',[]))
        return self.strength_cache[key]
    def global_snapshot(self):
        target=remote(self.root/'phase-2',self.job['workstation_share']);state=self.read(target/'full-status.json');generation=state.get('ranking_generation',state.get('completed_generations',0))
        if generation and (self.global_data is None or self.global_data['generation']!=generation) and not (self.global_thread and self.global_thread.is_alive()) and time.monotonic()-self.global_attempt>10:
            self.global_attempt=time.monotonic();self.global_loading=(generation,0,36);self.global_error=None
            def collect():
                from .progress_sessions import load_global
                try:self.global_data=load_global(self.job,target/'global',generation,self.observer_stop,lambda done,total:setattr(self,'global_loading',(generation,done,total)))
                except InterruptedError:pass
                except Exception as error:self.global_error=str(error)
                finally:self.global_loading=None
            self.global_thread=threading.Thread(target=collect,daemon=True);self.global_thread.start()
        return self.global_data
    def timing(self,path,state,running=False,complete=False):
        """Immutable seal durations are loaded once; active elapsed is approximate."""
        root=Path(path)/'global';done=50 if complete else int(state.get('completed_generations',0));cache=self.timing_cache.setdefault(str(root),{})
        for generation in range(1,done+1):
            if generation in cache:continue
            seal=self.read(root/f'generation-{generation:04d}'/'complete.json');seconds=seal.get('wall_seconds')
            if isinstance(seconds,(int,float)) and math.isfinite(seconds) and seconds>=0:
                reused=False
                if seal.get('source_sha256')==self.job.get('source_sha256'):
                    evidence=self.read(root/f'generation-{generation:04d}'/'training.json');refs=evidence.get('receipts',{})
                    # An absent/empty batch timing cannot establish a full replay rate.
                    reused=bool(refs) and all(not self.read((remote(r['receipt'],self.job['workstation_share']) if str(root).startswith('\\\\') else Path(r['receipt'])).parent/'timings.json') for r in refs.values())
                cache[generation]=(seconds,seal.get('source_sha256'),reused)
        rows=[];total=0.
        for generation,(seconds,source,reused) in sorted(cache.items()):
            if generation>done:continue
            total+=seconds;rows.append(dict(generation=generation,seconds=seconds,total=total,current_source=source==self.job.get('source_sha256'),cached_replay=reused))
        elapsed=None
        if running and done<50:
            try:
                started=(root/f'generation-{done+1:04d}'/'population.json').stat().st_mtime
                if started>=state.get('owner',{}).get('creation_time',float('inf')):elapsed=max(0.,time.time()-started)
            except OSError:pass
        recent=[r['seconds'] for r in rows if r['current_source'] and not r['cached_replay'] and r['generation']>1][-3:]
        rate=statistics.median(recent) if recent else None
        eta=0. if complete else None;generation_eta=None
        if running and rate is not None:
            generation_eta=max(rate*.1,rate-(elapsed or 0.))
            eta=generation_eta+max(0,50-done-1)*rate
        return dict(last=cache.get(done,(None,None))[0],elapsed=elapsed,work=total if rows else None,eta=eta,generation_eta=generation_eta,rows=rows,rate=rate,complete=complete)
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
            assigned=day in ns.get('active_sessions',[]) or ns.get('active')==day
            active=assigned and ns.get('status')=='session-stage'
            state='complete' if complete else 'failed' if assigned and ns.get('status')=='failed' else 'stopped' if stopped else s.get('status','running') if active else 'queued'
            preparing=active and s.get('owner',{}).get('creation_time',0)<ns.get('owner',{}).get('creation_time',0)
            if active and not complete and not stopped:
                if preparing:state='preparing'
                elif state=='running' and time.time()-s.get('updated_epoch',0)>15:state='STALE'
            progress=s.get('worker_progress',{}) if active and not complete else {};stage=progress.get('stage',s.get('stage','')) if active and not complete else ''
            if preparing:progress={};stage='Verify inputs / resume saved state'
            timing=self.timing(self.session_root(day),s,running=active and state=='running' and not stopped,complete=complete)
            sessions.append(dict(day=day,node=node,done=50 if complete else done,state=state,stage=stage,progress=progress,timing=timing,strength=self.strength(self.session_root(day),s)))
        result=(nodes,sessions,stopped);self.snapshot_cache=(time.monotonic(),result);return result
    def rows(self):
        if self.view=='overview':return self.snapshot()[1]
        if self.view=='global_sessions':
            data=self.global_snapshot();generation=data['generation'] if data else 0
            return [dict(day=day,role=role,generation=generation,**(data['summaries'].get(role+':'+day,{}) if data else {})) for role in ('training','validation') for day in self.job[role+'_days']]
        if self.view=='timings':return self.timing(self.target(),self.status())['rows']
        if self.view=='reproduction':return diagnostics(self.report().get('next_generation_reproduction') or {})
        if self.view in ('operators','families','methods'):
            category={'operators':'operator','families':'family','methods':'founder-method'}[self.view]
            return [g for g in self.report().get('groups',[]) if g['category']==category]
        if self.view=='global_ranking':
            from .full_common import ranking
            data=self.global_snapshot();key=str(self.global_role)+':'+str(self.global_day)
            if not data or key not in data['panels']:return []
            values=data['panels'][key];report=self.read(self.target()/'global'/f"generation-{data['generation']:04d}"/'evolution-report.json');details={r['serial']:r for r in report.get('candidates',[])}
            rows=[dict(details.get(r['serial'],{}),**r,generation=data['generation'],metric_scope=key) for r in ranking(values['panel'],values['ids'])]
        elif self.view=='founders':rows=list(self.read(self.target()/'global/founder-results.json').values())
        elif self.view=='ranking':
            details={r['serial']:r for r in self.report().get('candidates',[])}
            rows=[dict(details.get(r['serial'],{}),**r) for r in self.status().get('validation_ranking' if self.validation else 'training_ranking',[])]
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
        if key=='c':self.summary_mode='timing' if self.summary_mode=='combined' else 'combined';return
        if key=='d':self.view='timings';self.selection=0;self.global_day=None;return
        if key in ('h','\x1b'):self.view='overview';self.day=None;self.global_day=None;self.selection=0;self.filter=None;return
        if key=='g':self.day=None;self.global_day=None;self.view='global_sessions';self.selection=0;self.filter=None;return
        if key in ('o','f','m','a','i'):
            self.view={'o':'operators','f':'families','m':'methods','a':'candidates','i':'founders'}[key];self.selection=0;self.filter=None;return
        if key=='t':self.validation=not self.validation;return
        if key=='s':
            fields=['score','net_pnl','gross_losses','drawdown','nodes'];self.sort=fields[(fields.index(self.sort)+1)%len(fields)];return
        if key=='r':self.reverse=not self.reverse;return
        if key=='u' and self.selected and self.selected.get('founder_index') is not None:
            self.filter=('descendant',self.selected['founder_index']);self.view='candidates';self.selection=0;return
        rows=self.rows();step=max(1,height-13)
        if key in ('j','k','n','b'):self.selection=max(0,min(len(rows)-1,self.selection+{'j':1,'k':-1,'n':step,'b':-step}[key]));return
        if key in ('\r','\n') and rows and self.view not in ('reproduction','detail','lineage','timings'):
            row=rows[min(self.selection,len(rows)-1)]
            if self.view=='overview':self.day=row['day'];self.view='ranking';self.selection=0
            elif self.view=='global_sessions':self.global_day=row['day'];self.global_role=row['role'];self.view='global_ranking';self.selection=0
            elif self.view in ('operators','families','methods'):
                self.filter=({'operators':'operators','families':'founder_groups','methods':'founder_methods'}[self.view],row['group']);self.view='candidates';self.selection=0
            else:self.selected=row;self.view='detail';self.detail_page=0

def render(model,width,height):
    layout=Layout();layout.split_column(Layout(name='head',size=4),Layout(name='body'),Layout(name='keys',size=4))
    title=f"V56 MULTI-DEVICE | {'SESSION '+model.day if model.day else 'PHASE 1' if model.view=='overview' else 'PHASE 2 GLOBAL'} | 50 session + 50 global"
    note='Score: P&L - losses - 2 x DD | risk caps OFF | validation display-only'
    table=Table(expand=True,padding=(0,0));limit=max(1,height-13);start=model.selection//limit*limit
    if model.view=='overview':
        nodes,rows,stopped=model.snapshot();done=sum(r['state']=='complete' for r in rows)
        stage='STOPPED' if stopped else ' | '.join(f"{n}: {s.get('status','unavailable')} ({len(s.get('active_sessions',[])) if s.get('status')=='session-stage' else 0} active)" for n,s in nodes.items())
        if nodes.get('workstation',{}).get('status')=='global-stage':
            state=model.read(remote(model.root/'phase-2/full-status.json',model.job['workstation_share']));initial=state.get('initialization_progress',{})
            stage+=f" | global {state.get('completed_generations',0)}/50"
            if initial:stage+=f" | blind {initial['completed']}/{initial['total']}"
        title+=f' | {done}/24 complete';note=stage+'\n'+note
        compact=width<120
        columns=('Session','Host','Gen','Last','Current','Work','ETA','State') if compact else ('Session','Host','Sealed','State','Last gen','Current gen','Session work','Session ETA','Activity')
        if model.summary_mode=='combined':columns=('Session','Host','Gen','Top net $','Median $','Top DD $','Win %') if compact else ('Session','Host','Gen','State','Last','Current','Work','ETA','Best score','Top net $','Median $','Top DD $','Win %','Profit %')
        for h in columns:table.add_column(h,no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):
            p=r['progress'];activity=r['stage']+(f" {p.get('completed_seconds',0)}/{p.get('total_seconds',0)} clocks" if p else '')
            t=r['timing'];eta='done' if t['complete'] else '~'+duration(t['eta']) if t['eta'] is not None else '-'
            times=[duration(t['last']),duration(t['elapsed']),duration(t['work']),eta]
            cells=[r['day'],r['node'][0].upper(),f"{r['done']}/50",*times,r['state']] if compact else [r['day'],r['node'],f"{r['done']}/50",r['state'],*times,activity]
            if model.summary_mode=='combined':
                s=r['strength'];metrics=[number(s.get(k)) for k in ('top_net','median_net','top_drawdown','top_win_rate')]
                cells=[r['day'],r['node'][0].upper(),f"{r['done']}/50",*metrics] if compact else [r['day'],r['node'][0].upper(),f"{r['done']}/50",r['state'],*times,number(s.get('best_score')),*metrics,number(s.get('profitable_fraction'))]
            color={'complete':'bright_green','running':'bright_cyan','preparing':'bright_yellow','failed':'bold bright_red','STALE':'bold bright_red','stopped':'dim'}.get(r['state'])
            table.add_row(*cells,style=SELECTED if i==model.selection else color)
        caption='C: strength / timing columns | DD and win rate: top-profit candidate. Profit %: profitable eligible candidates.'
        if rows:
            selected=rows[min(model.selection,len(rows)-1)];t=selected['timing']
            note+='\nSelected '+selected['day']+' | generation ETA '+('done' if t['complete'] else '~'+duration(t['generation_eta']) if t['generation_eta'] is not None else 'warming / unavailable')+' | Enter then D: generation history'
    elif model.view=='global_sessions':
        rows=model.rows();compact=width<120
        columns=('Session','Split','Top net $','Median $','Top DD $','Win %') if compact else ('Session','Split','Gen','Best score','Top net $','Median net $','Top DD $','Median DD $','Win %','Profit %','Candidates','Replay work')
        for h in columns:table.add_column(h,no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):
            s=r.get('strength',{});metrics=[number(s.get(k)) for k in ('top_net','median_net','top_drawdown')]
            cells=[r['day'],'VAL*' if r['role']=='validation' else 'Train']
            cells+= [*metrics,number(s.get('top_win_rate'))] if compact else [str(r['generation']) if r['generation'] else '-',number(s.get('best_score')),*metrics,number(s.get('median_drawdown')),number(s.get('top_win_rate')),number(s.get('profitable_fraction')),str(s.get('candidates','-')),'cached' if r.get('cached') else duration(r.get('batch_seconds'))]
            table.add_row(*cells,style=SELECTED if i==model.selection else 'bright_magenta' if r['role']=='validation' else 'bright_cyan')
        loaded=model.global_data;caption=f"Sealed global generation {loaded['generation']}" if loaded else 'Global results not yet evaluated'
        if model.global_loading:caption+=f' | loading {model.global_loading[1]}/{model.global_loading[2]} sessions'
        if model.global_error:caption+=' | evidence unavailable: '+model.global_error
        caption+=f' | rows {start+1}-{min(start+limit,len(rows))}/{len(rows)}'
        caption+=' | VAL*: display-only | eligible candidates | DD / Win %: top-profit candidate | Enter: session ranking' if not compact else ' | N/B page | VAL*: display-only'
    elif model.view in ('operators','families','methods'):
        report=model.report();rows=model.rows()
        compact=width<120
        columns=('Group','Eval','Keep','Best $','Median $','Beat parent') if compact else ('Group','Eval','Keep','Best $','Median $','Change $','Loss $','DD $','Nodes','Beat parent')
        for h in columns:table.add_column(h,no_wrap=True,justify='right')
        for i,r in enumerate(rows[start:start+limit],start):
            cells=[r['group'],str(r['evaluated']),str(r['retained']) if r['retained'] is not None else '-',number(r['best_score']),number(r['median_score'])]
            if not compact:cells.extend([number(r.get('median_score_change')),number(r['median_loss']),number(r['median_drawdown']),number(r.get('median_nodes'))])
            cells.append(f"{r['beat_best_parent']}/{r['parent_comparisons']}");table.add_row(*cells,style=SELECTED if i==model.selection else None)
        caption=f"TRAINING | generation {report.get('generation',0)} | Overlapping groups; Enter lists members. Missing historical fields are '-'."
        if not rows:table.add_column('Status') if not table.columns else None;table.add_row('Not evaluated / unavailable',*['']*(len(table.columns)-1))
    elif model.view=='timings':
        rows=model.rows()
        for h in ('Generation','Duration','Cumulative work','Timing period'):table.add_column(h,no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):table.add_row(str(r['generation']),duration(r['seconds']),duration(r['total']),'cache reuse' if r['cached_replay'] else 'current code' if r['current_source'] else 'before update',style=SELECTED if i==model.selection else None)
        if not rows:table.add_row('No sealed generation','-','-','-')
        caption='Measured generation wall times; excludes between-generation pauses and initial setup.'
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
        if not model.day and model.global_data and model.global_data['generation']==generation:
            panels=model.global_data['panels'];first=next(iter(panels.values()),None)
            if first:data=dict(ids=first['ids'],sessions={k:v['panel'] for k,v in panels.items()})
        if r.get('metric_scope'):lines[0]+=' | '+r['metric_scope']+(' display-only' if r['metric_scope'].startswith('validation:') else '')
        if r.get('serial') in data.get('ids',[]):
            index=data['ids'].index(r['serial'])
            page_size=max(1,height-19);offset=model.detail_page*page_size
            for day,p in list(data.get('sessions',{}).items())[offset:offset+page_size]:table.add_row(day,*(number(p[k][index]) for k in ('score','net_pnl','gross_losses','drawdown')))
        else:table.add_row('Per-session detail unavailable','-','-','-','-')
        from rich.console import Group
        table=Group(Text('\n'.join(lines)),table);caption='N/B pages sessions | L mixing lineage | H summary | A current candidates'
    else:
        rows=model.rows()
        for h in ('Serial','Score $','Net $','Loss $','DD $','Nodes','Technique / family'):table.add_column(h,no_wrap=True,justify='right')
        for i,r in enumerate(rows[start:start+limit],start):table.add_row(str(r['serial']),*(number(r.get(k)) for k in ('score','net_pnl','gross_losses','drawdown','nodes')),', '.join(r.get('operators',r.get('founder_groups',[]))),style=SELECTED if i==model.selection else None)
        status=model.status();caption=f"{'VALIDATION display-only' if model.validation and model.view=='ranking' else 'TRAINING'} | {model.view} | {len(rows)} candidates | sort {model.sort} | {status.get('stage','Not evaluated')}"
        if model.view=='global_ranking':caption=f"GLOBAL {model.global_role} {'display-only' if model.global_role=='validation' else ''} | {model.global_day} | sealed generation {model.global_data['generation'] if model.global_data else '-'} | {len(rows)} candidates | sort {model.sort}"
        if not rows:table.add_row('Not evaluated',*['']*6)
    if model.view!='overview':
        state=model.status();t=model.timing(model.target(),state,running=state.get('status')=='running' and time.time()-state.get('updated_epoch',0)<15,complete=state.get('status')=='complete')
        note+='\nLast gen '+duration(t['last'])+' | current '+duration(t['elapsed'])+' | work '+duration(t['work'])+' | session ETA '+('~'+duration(t['eta']) if t['eta'] is not None else 'warming / unavailable')
        note+='\nGeneration ETA '+('~'+duration(t['generation_eta']) if t['generation_eta'] is not None else '-')+' | D: measured generation history'
    layout['head'].update(Text(title+'\n'+note,style='bright_cyan'))
    layout['body'].update(Panel(table,title=model.view.upper(),subtitle=caption,padding=0))
    layout['keys'].update(Text('J/K select | N/B page | Enter details | H phase 1 | G global sessions\nO crossover | F families | M methods | A candidates | I founders | D timings\nC columns | S sort | R reverse | T train/val | U descendants | L lineage | V attempts\nQ closes this observer only; optimization continues.'))
    return layout

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--view',default='overview');a=p.parse_args()
    model=Model(json.loads(a.job.read_bytes()));model.view=a.view;console=Console()
    if console.is_terminal:console=Console(color_system='truecolor',no_color=False,legacy_windows=False)
    if a.once or not console.is_terminal:console.print(render(model,console.width,console.height));model.close();return
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
    finally:model.close()
if __name__=='__main__':main()
