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

def read_key(getwch):
    """Consume the whole Windows extended key; scan codes are not shortcuts."""
    key=getwch()
    if key in ('\x00','\xe0'):
        return {'H':'k','P':'j','K':'back','M':'\r','I':'b','Q':'n','G':'home','O':'end'}.get(getwch(),'')
    return 'back' if key in ('\x1b','\b') else key.lower()

def progress_bar(done,total,width=10,color='bright_cyan'):
    if not total:return Text('waiting',style='dim')
    fraction=max(0.,min(1.,(done or 0)/total));filled=int(width*fraction)
    value=Text('━'*filled,style=color);value.append('─'*(width-filled),style='bright_black');value.append(f' {fraction:.0%}',style=color)
    return value

def replay_progress(progress,state='running'):
    if state=='complete':return progress_bar(1,1,color='bright_green')
    if state!='running':return Text(state,style='bright_red' if state in ('failed','STALE') else 'bright_yellow')
    if progress.get('total_candidates') and progress.get('total_seconds'):
        done=progress.get('completed_candidates',0)+progress.get('batch_candidates',0)*min(1.,progress.get('completed_seconds',0)/progress['total_seconds'])
        return progress_bar(done,progress['total_candidates'])
    total=progress.get('total_seconds');batches=progress.get('total_local_batches')
    if total and batches:
        return progress_bar((progress.get('completed_local_batches') or 0)+min(1.,(progress.get('completed_seconds') or 0)/total),batches)
    if total:return progress_bar(progress.get('completed_seconds'),total)
    return Text(progress.get('stage','working'),style='bright_yellow')

def generation_progress(done,total=50):
    filled=int(6*max(0,min(total,done))/total)
    value=Text(f'{done}/{total} ',style='bright_green')
    value.append('━'*filled,style='bright_green');value.append('─'*(6-filled),style='bright_black')
    return value

GROUP_SORT={'score':'best_score','net_pnl':'median_net','gross_losses':'median_loss','drawdown':'median_drawdown','nodes':'median_nodes'}

def sorted_rows(rows,field,descending):
    known=[r for r in rows if r.get(field) is not None];missing=[r for r in rows if r.get(field) is None]
    return sorted(known,key=lambda r:r[field],reverse=descending)+missing

def duration(seconds):
    if seconds is None:return '-'
    seconds=max(0,int(seconds))
    if seconds>=3600:return f'{seconds//3600}h{seconds%3600//60:02d}m'
    if seconds>=60:return f'{seconds//60}m{seconds%60:02d}s'
    return f'{seconds}s'

def replay_numbers(live):
    p=live['progress'];total=p.get('total_candidates',0);done=p.get('completed_candidates',0)
    if p.get('total_seconds'):done+=p.get('batch_candidates',0)*min(1.,p.get('completed_seconds',0)/p['total_seconds'])
    if live['status']=='complete':done=total
    elapsed=p.get('session_seconds') if live['status']=='complete' else max(0,time.time()-p['session_started_epoch']) if p.get('session_started_epoch') else None
    fraction=min(1.,done/total) if total else 1. if live['status']=='complete' else 0.
    reused=p.get('reused_candidates') or 0;new_total=total-reused
    measured=max(0,done-reused)/new_total if new_total>0 else 0.
    eta=elapsed*(1-measured)/measured if elapsed is not None and measured>=.05 and live['status']=='running' else None
    # Counts mean completed candidate replays; only the bar/ETA interpolate the
    # active batch's clocks. Never label partially replayed candidates complete.
    completed=total if live['status']=='complete' else p.get('completed_candidates',0)
    return dict(done=int(min(completed,total)),total=total,elapsed=elapsed,eta=eta,fraction=fraction)

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
        from .progress_archive import Archive
        self.archive=Archive(self.bus,job.get('training_days',[]),self.observer_stop,job.get('global_restart',False))
        self.day=None;self.view='overview';self.selection=0;self.sort='score';self.reverse=False;self.filter=None;self.selected=None;self.validation=False;self.detail_page=0;self.snapshot_cache=None
        self.navigation=[]
        self.replay_samples={};self.replay_sample_scope=None
        self.aggregate_role='training';self.aggregate_mode=False;self.origin_catalog={};self.live_data=None;self.live_thread=None;self.live_error=None;self.live_attempt=0.
    def remember(self):
        self.navigation.append({k:getattr(self,k) for k in ('view','day','global_day','global_role','aggregate_mode','aggregate_role','selection','filter','selected','validation','detail_page','sort','reverse')})
        self.navigation=self.navigation[-32:]
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
        path=Path(self.job.get('phase1_campaign',self.root))/'sessions'/day
        return path if node=='laptop' else remote(path,self.job['workstation_share'])
    def target(self):return self.session_root(self.day) if self.day else remote(self.root/'phase-2',self.job['workstation_share'])
    def session_state(self,day):
        return self.archive.data[day]['status'] if day in self.archive.data else self.read(self.session_root(day)/'full-status.json')
    def status(self):
        if self.day:return self.session_state(self.day)
        state=self.read(self.target()/'full-status.json')
        if state:return state
        node=self.read(self.bus/'workstation-status.json')
        return dict(status='running' if node.get('status','').startswith('verify-') else node.get('status','queued'),stage=node.get('status','Waiting for coordinator'),completed_generations=0)
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
        day=Path(path).name
        if day in self.archive.data and not (Path(path)/'global').exists():
            seconds=self.archive.data[day]['last_seconds'];saved=self.read(self.bus/'observer-timings'/(day+'.json'));rows=saved.get('rows',[])
            return dict(last=seconds,elapsed=None,work=rows[-1]['total'] if rows else None,eta=0,generation_eta=None,rows=rows,rate=None,complete=True)
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
    def aggregate_report(self,role):
        live=self.live_snapshot(self.global_replays());sealed=self.global_snapshot()
        data=live if live and (not sealed or live['generation']>=sealed['generation']) else sealed
        report=data.get('aggregates',{}).get(role) if data else None
        return report or dict(generation=data['generation'] if data else 0,candidates=[],groups=[],coverage=0,total=len(self.job.get(role+'_days',[])),scope='global-'+role,partial=True)
    def report(self):
        if self.aggregate_mode:return self.aggregate_report(self.aggregate_role)
        if self.day and self.day in self.archive.data:return self.archive.data[self.day]['report']
        if self.global_day:
            data=self.live_data if self.live_data and str(self.global_role)+':'+self.global_day in self.live_data['panels'] else self.global_data
            if data:
                key=str(self.global_role)+':'+self.global_day
                if key in data.get('reports',{}):return data['reports'][key]
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
        for node,value in nodes.items():
            if not value and all(d in self.archive.data for d in self.job['machines'][node]['session_days']):nodes[node]=dict(status='phase-1 complete (archived)',completed=self.job['machines'][node]['session_days'])
        stopped=(self.root/'STOP').exists() or (self.bus/'STOP').exists()
        for day in self.job['training_days']:
            node=next(n for n,m in self.job['machines'].items() if day in m['session_days']);ns=nodes[node]
            s=self.session_state(day);done=s.get('completed_generations',0)
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
    def global_replays(self):
        """Observe bounded progress samples; receipts, not samples, prove completion."""
        root=remote(self.root/'phase-2',self.job['workstation_share']);state=self.read(root/'full-status.json')
        sealed=state.get('completed_generations',0);generation=sealed
        if (root/'global'/f'generation-{sealed+1:04d}'/'population.json').exists():generation=sealed+1
        scope=(state.get('owner',{}).get('creation_time'),generation)
        if self.replay_sample_scope!=scope:self.replay_samples={};self.replay_sample_scope=scope
        stage=state.get('stage','');role='training' if stage=='Global training' else 'validation' if stage=='Phase 2 validation: display only' else None
        event=state.get('worker_progress',{});day=event.get('day');now=time.time()
        if role and state.get('replay_generation')==generation and state.get('replay_role')==role:
            for d,sample in state.get('session_paths',{}).items():
                if d in self.job[role+'_days']:self.replay_samples[role+':'+d]=dict(sample)
        if role and day in self.job[role+'_days']:
            self.replay_samples[role+':'+day]=dict(event)
        rows={}
        for split in ('training','validation'):
            for day in self.job[split+'_days']:
                key=split+':'+day;sample=self.replay_samples.get(key,{})
                complete=bool(generation) and (root/'replays'/split/f'generation-{generation:04d}'/day/'receipt.json').exists()
                active=role==split and day in state.get('active_sessions',[])
                status='complete' if complete else state.get('status','running') if active else 'queued'
                if active and status=='running' and not sample:sample=dict(stage='Active / awaiting update')
                rows[key]=dict(generation=generation,status=status,progress=sample,age=max(0,now-sample['updated_epoch']) if sample.get('updated_epoch') else None)
        return rows

    def diagnostic_replays(self):
        """A separate receipt/progress scope; never feed main candidate metrics."""
        root=remote(self.root/'phase-2',self.job['workstation_share']);state=self.read(root/'full-status.json')
        generation=state.get('completed_generations',0)
        if (root/'global'/f'generation-{generation+1:04d}'/'population.json').exists():generation+=1
        scope=(state.get('owner',{}).get('creation_time'),generation)
        if getattr(self,'diagnostic_scope',None)!=scope:self.diagnostic_scope=scope;self.diagnostic_samples={}
        active=state.get('stage')=='Paired mutation diagnostic; selection and RNG isolated' and state.get('replay_generation')==generation
        if active:
            for day,sample in state.get('session_paths',{}).items():
                if day in self.job['training_days']:self.diagnostic_samples[day]=dict(sample)
        rows=[];now=time.time();stopped=(self.root/'STOP').exists() or (root.parent/'exchange/STOP').exists()
        for day in self.job['training_days']:
            sample=self.diagnostic_samples.get(day,{})
            complete=bool(generation) and (root/'mutation-probe-replay/training'/f'generation-{generation:04d}'/day/'receipt.json').exists()
            status='complete' if complete else state.get('status','running') if active and day in state.get('active_sessions',[]) else 'queued'
            if not complete and stopped:status='stopped'
            elif status=='running' and sample.get('updated_epoch') and now-sample['updated_epoch']>15:status='STALE'
            rows.append(dict(day=day,generation=generation,status=status,progress=sample,age=max(0,now-sample['updated_epoch']) if sample.get('updated_epoch') else None))
        return rows

    def live_snapshot(self,live):
        generation=max((r['generation'] for r in live.values()),default=0)
        if not generation:return None
        if self.live_data is None or self.live_data['generation']!=generation:
            if self.live_thread and self.live_thread.is_alive():return None
            self.live_data=dict(generation=generation,panels={},summaries={},reports={});self.live_error=None
        data=self.live_data
        pending=[key for key,r in live.items() if r['status']=='complete' and (key not in data['summaries'] or key not in data['reports'])]
        if pending and not (self.live_thread and self.live_thread.is_alive()) and time.monotonic()-self.live_attempt>2:
            self.live_attempt=time.monotonic();root=remote(self.root/'phase-2',self.job['workstation_share'])
            def collect():
                try:
                    from .progress_sessions import population_metadata,load_live_session
                    ids,token=population_metadata(root/'global'/f'generation-{generation:04d}'/'population.json')
                    path=root/'global'/f'generation-{generation:04d}'/'evaluated-pedigree.json'
                    records=json.loads(path.read_bytes()) if path.exists() else None
                    if not self.origin_catalog:
                        founders=json.loads((root/'global/generation-0001/evaluated-pedigree.json').read_bytes())
                        self.origin_catalog={r['founder_index']:r['origins'][0] for r in founders if r.get('operation')=='session-expert' and r.get('origins')}
                    for key in pending:
                        if self.observer_stop.is_set():return
                        role,day=key.split(':',1)
                        if key not in data['panels']:
                            panel,summary=load_live_session(self.job,root,generation,role,day,ids,token)
                            data['panels'][key]=panel;data['summaries'][key]=summary
                        else:panel=data['panels'][key]
                        if records is not None:
                            from .evolution_report import summarize
                            report=summarize(ids,panel['panel'],records,generation=generation)
                            report.update(scope=key,live=True)
                            for row in report['candidates']:row['metric_scope']=key
                            data['reports'][key]=report
                        if records is not None:
                            from .progress_global import combined
                            data.setdefault('aggregates',{})[role]=combined(data,records,len(self.job[role+'_days']),role)
                            data['aggregate']=data['aggregates'].get('training')
                    self.live_error=None
                except Exception as error:self.live_error=str(error)
            self.live_thread=threading.Thread(target=collect,daemon=True);self.live_thread.start()
        return data

    def timing_rows(self):
        rows=[dict(label=f"Generation {r['generation']}",state='sealed',elapsed=r['seconds'],eta=0,reuse='-',**r) for r in self.timing(self.target(),self.status())['rows']]
        if self.day:return rows
        live=self.global_replays();self.live_snapshot(live)
        for key,value in live.items():
            if self.global_day and key!=str(self.global_role)+':'+self.global_day:continue
            p=dict(value['progress'])
            if self.live_data and key in self.live_data.get('summaries',{}):p={**self.live_data['summaries'][key].get('replay_details',{}),**p}
            n=replay_numbers(dict(value,progress=p))
            rows.append(dict(label=key,state=value['status'],elapsed=n['elapsed'],eta=n['eta'],reuse=f"{p.get('evaluated_candidates','?')}/{p.get('reused_candidates','?')}",generation=value['generation']))
        return rows

    def lineage_rows(self):
        row=self.selected or {};mix=row.get('mix') or {};path=mix.get('provenance_file');rows=[]
        if row.get('source_day'):rows.append(dict(step='Phase-1 source',parents=f"{row['source_day']} #{row.get('source_serial')}",operator='unchanged expert',depth=str(row.get('source_generation','-'))))
        parents=[str(p) for p in row.get('parents',[]) if p>=0]
        rows.append(dict(step=f"Candidate {row.get('serial')}",parents=', '.join(parents) or 'fresh / no parents',operator=row.get('operation','unknown'),depth='birth'))
        for serial,distance in row.get('ancestors',[]):rows.append(dict(step='Ancestor',parents=str(serial),operator='parent link',depth=str(distance)))
        for action in (row.get('crossover') or {}).get('actions',[]):rows.append(dict(step='Crossover action',parents=str(action.get('stage',action.get('action','-'))),operator=str(action.get('join',action.get('mode','-'))),depth='-'))
        for edit in row.get('mutation_fields',[]):rows.append(dict(step='Mutation',parents=str(edit.get('stage','-')),operator=str(edit.get('field','-')),depth='-'))
        if path:
            detail=self.read(remote(path,self.job['workstation_share']))
            from .identity import digest
            if mix.get('provenance_sha256') and digest(detail)!=mix['provenance_sha256']:raise ValueError('Blind lineage provenance changed')
            rows.extend(dict(step=str(v['id']),parents=', '.join(map(str,v['parents'])),operator=v['mode'],depth=str(v['depth'])) for v in detail.get('steps',[]))
        return rows

    def rows(self):
        if self.view=='global_splits':return [dict(role=role,**self.aggregate_report(role)) for role in ('training','validation')]
        if self.view=='overview':return self.snapshot()[1]
        if self.view=='global_sessions':
            data=self.global_snapshot();generation=data['generation'] if data else 0
            live=self.global_replays();current=self.live_snapshot(live);rows=[]
            diagnostic={r['day']:r for r in self.diagnostic_replays()} if self.status().get('stage')=='Paired mutation diagnostic; selection and RNG isolated' else {}
            for role in ('training','validation'):
                for day in self.job[role+'_days']:
                    key=role+':'+day;source=current if current and key in current['summaries'] else data
                    summary=source['summaries'].get(key,{}) if source else {}
                    if live[key]['status']=='complete' and source and source['generation']==live[key]['generation']:
                        live[key]['progress']={**summary.get('replay_details',{}),**live[key]['progress']}
                    rows.append(dict(day=day,role=role,generation=source['generation'] if source else 0,replay=diagnostic.get(day,live[key]) if role=='training' else live[key],**summary))
            return rows
        if self.view=='timings':return self.timing_rows()
        if self.view=='reproduction':return diagnostics(self.report().get('next_generation_reproduction') or {})
        if self.view in ('operators','families','methods','origins','immigration'):
            category={'operators':'operator','families':'family','methods':'founder-method','origins':'origin-session','immigration':'immigration'}[self.view]
            return sorted_rows([g for g in self.report().get('groups',[]) if g['category']==category],GROUP_SORT[self.sort],(self.sort not in ('gross_losses','drawdown','nodes'))!=self.reverse)
        if self.view=='global_ranking':
            from .full_common import ranking
            data=self.global_snapshot();key=str(self.global_role)+':'+str(self.global_day)
            if self.live_data and key in self.live_data['panels'] and (not data or self.live_data['generation']>=data['generation']):data=self.live_data
            if not data or key not in data['panels']:return []
            values=data['panels'][key];report=self.report();details={r['serial']:r for r in report.get('candidates',[])}
            rows=[dict(details.get(r['serial'],{}),**r,generation=data['generation'],metric_scope=key) for r in ranking(values['panel'],values['ids'])]
        elif self.view=='founders':rows=[r for r in self.report().get('candidates',[]) if r.get('original_founder')] if self.aggregate_mode else list(self.read(self.target()/'global/founder-results.json').values())
        elif self.view=='ranking' and self.aggregate_mode:rows=self.report().get('candidates',[])
        elif self.view=='ranking':
            details={r['serial']:r for r in self.report().get('candidates',[])}
            rows=[dict(details.get(r['serial'],{}),**r) for r in self.status().get('validation_ranking' if self.validation else 'training_ranking',[])]
        else:rows=self.report().get('candidates',[])
        if self.filter:
            kind,value=self.filter
            if kind=='descendant':rows=[r for r in rows if not r.get('original_founder') and int(r.get('founder_mask','0'),16)&(1<<value)]
            else:rows=[r for r in rows if value in r.get(kind,[])]
        reverse=self.sort not in ('gross_losses','drawdown','nodes')
        rows=sorted_rows(rows,self.sort,reverse!=self.reverse)
        return rows
    def key(self,key,height=40):
        if key=='a' and not self.day:
            self.remember();self.aggregate_mode=False;self.global_day=None;self.view='global_splits';self.selection=0;self.filter=None;self.selected=None;return
        if self.view=='global_splits' and key in ('1','2','t','v','\r','\n'):
            role=('training' if key in ('1','t') else 'validation') if key not in ('\r','\n') else ('training','validation')[min(self.selection,1)]
            self.remember();self.aggregate_role=role;self.aggregate_mode=True;self.view='ranking';self.selection=0;self.filter=None;self.validation=role=='validation';return
        if key in ('1','2'):
            self.remember();self.filter=None;self.selection=0
            if not self.day and not self.global_day:self.aggregate_mode=True
            self.view=('global_ranking' if self.global_day else 'ranking') if key=='1' else 'operators'
            return
        if key=='back':
            if self.navigation:
                for k,v in self.navigation.pop().items():setattr(self,k,v)
            return
        if self.view in ('detail','lineage') and key in ('n','b'):
            self.detail_page=max(0,self.detail_page+(1 if key=='n' else -1));return
        if key=='l':
            if self.view not in ('detail','lineage'):
                rows=self.rows()
                if not rows or 'serial' not in rows[min(self.selection,len(rows)-1)]:return
                self.selected=rows[min(self.selection,len(rows)-1)]
            if self.selected:self.remember();self.view='lineage';self.detail_page=0
            return
        if key=='v':self.view='reproduction';self.selection=0;return
        if key=='c':self.summary_mode='timing' if self.summary_mode=='combined' else 'combined';return
        if key=='d':self.remember();self.view='timings';self.selection=0;return
        if key in ('h','\x1b'):self.aggregate_mode=False;self.view='overview';self.day=None;self.global_day=None;self.selection=0;self.filter=None;self.navigation=[];return
        if key=='g':self.aggregate_mode=False;self.day=None;self.global_day=None;self.view='global_sessions';self.selection=0;self.filter=None;return
        if key in ('o','f','m','a','i','p','e'):
            self.remember()
            if not self.day and not self.global_day:self.aggregate_mode=True
            self.view={'p':'origins','e':'immigration','o':'operators','f':'families','m':'methods','a':'global_ranking' if self.global_day else 'ranking','i':'founders'}[key];self.selection=0;self.filter=None;return
        if key=='t':
            if not self.aggregate_mode:self.validation=not self.validation
            return
        if key=='s':
            fields=['score','net_pnl','gross_losses','drawdown','nodes'];self.sort=fields[(fields.index(self.sort)+1)%len(fields)];self.selection=0;return
        if key=='r':self.reverse=not self.reverse;return
        if key=='u' and self.selected and self.selected.get('founder_index') is not None:
            self.filter=('descendant',self.selected['founder_index']);self.view='candidates';self.selection=0;return
        rows=self.rows();step=max(1,height-(18 if self.view=='global_sessions' else 15))
        if key in ('home','end'):self.selection=0 if key=='home' else max(0,len(rows)-1);return
        if key in ('j','k','n','b'):self.selection=max(0,min(len(rows)-1,self.selection+{'j':1,'k':-1,'n':step,'b':-step}[key]));return
        if key in ('\r','\n') and rows and self.view not in ('reproduction','detail','lineage','timings'):
            row=rows[min(self.selection,len(rows)-1)]
            self.remember()
            if self.view=='overview':self.day=row['day'];self.view='ranking';self.selection=0;self.filter=None;self.validation=False
            elif self.view=='global_sessions':self.aggregate_mode=False;self.global_day=row['day'];self.global_role=row['role'];self.view='global_ranking';self.selection=0
            elif self.view in ('operators','families','methods','origins','immigration'):
                self.filter=({'operators':'operators','families':'founder_groups','methods':'founder_methods','origins':'source_sessions','immigration':'immigration_categories'}[self.view],row['group']);self.view='candidates';self.selection=0
            else:self.selected=row;self.view='detail';self.detail_page=0

def render(model,width,height):
    head_size=9 if model.view=='global_sessions' else 7 if model.aggregate_mode else 6
    layout=Layout();layout.split_column(Layout(name='head',size=head_size),Layout(name='body'),Layout(name='keys',size=4))
    title=f"V56 MULTI-DEVICE | {'SESSION '+model.day if model.day else 'PHASE 1' if model.view=='overview' else 'PHASE 2 GLOBAL'} | 50 session + 50 global"
    note='Score: P&L - losses - 2 x DD | risk caps OFF | validation display-only'
    if model.aggregate_mode:
        report=model.report();note=f"GLOBAL {model.aggregate_role.upper()}: {report.get('coverage',0)}/{report.get('total',24)} sessions | {'PARTIAL totals' if report.get('partial',True) else 'COMPLETE totals'}\n"+note
    table=Table(expand=True,padding=(0,0));limit=max(1,height-head_size-9);start=model.selection//limit*limit
    if model.view=='overview':
        nodes,rows,stopped=model.snapshot();done=sum(r['state']=='complete' for r in rows)
        stage='STOPPED' if stopped else ' | '.join(f"{n}: {s.get('status','unavailable')} ({len(s.get('active_sessions',[])) if s.get('status')=='session-stage' else 0} active)" for n,s in nodes.items())
        if nodes.get('workstation',{}).get('status')=='global-stage':
            state=model.read(remote(model.root/'phase-2/full-status.json',model.job['workstation_share']));initial=state.get('initialization_progress',{})
            stage+=f" | global {state.get('completed_generations',0)}/50"
            if initial:stage+=f" | blind {initial['completed']}/{initial['total']}"
        title+=f' | {done}/24 complete';note=stage+'\n'+note
        compact=width<120
        columns=('Session','Generations','Replay','Last','Current','Work','ETA') if compact else ('Session','Host','Generations','Replay','Last gen','Current gen','Session work','Session ETA','Activity')
        if model.summary_mode=='combined':columns=('Session','Generations','Replay','Max net $','Median $','Win %') if compact else ('Session','Host','Generations','Replay','Last','Current','Work','ETA','Best score','Max net $','Median $','Max-net DD $','Win %','Profit %')
        for h in columns:table.add_column(h,no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):
            p=r['progress'];activity=r['stage']+(f" {p.get('completed_seconds',0)}/{p.get('total_seconds',0)} clocks" if p else '')
            t=r['timing'];eta='done' if t['complete'] else '~'+duration(t['eta']) if t['eta'] is not None else '-'
            times=[duration(t['last']),duration(t['elapsed']),duration(t['work']),eta]
            progress=replay_progress(p,r['state'])
            generations=generation_progress(r['done'])
            cells=[r['day'],generations,progress,*times] if compact else [r['day'],r['node'],generations,progress,*times,activity]
            if model.summary_mode=='combined':
                s=r['strength'];metrics=[number(s.get(k)) for k in ('top_net','median_net','top_drawdown','top_win_rate')]
                cells=[r['day'],generations,progress,*metrics[:2],metrics[3]] if compact else [r['day'],r['node'][0].upper(),generations,progress,*times,number(s.get('best_score')),*metrics,number(s.get('profitable_fraction'))]
            color={'complete':'bright_green','running':'bright_cyan','preparing':'bright_yellow','failed':'bold bright_red','STALE':'bold bright_red','stopped':'dim'}.get(r['state'])
            table.add_row(*cells,style=SELECTED if i==model.selection else color)
        caption='Generations: sealed / 50 | Replay: current replay only | C: strength / timings | DD / Win %: top-profit candidate'
        if rows:
            selected=rows[min(model.selection,len(rows)-1)];t=selected['timing']
            note+='\nSelected '+selected['day']+' | generation ETA '+('done' if t['complete'] else '~'+duration(t['generation_eta']) if t['generation_eta'] is not None else 'warming / unavailable')+' | Enter then D: generation history'
            note+='\n'+selected['node']+' | '+selected['state']+' | '+selected['stage']
    elif model.view=='global_sessions':
        rows=model.rows();compact=width<120
        timing=model.summary_mode=='timing'
        columns=('Session','Split','Replay','Time','Max net $','DD $') if compact else ('Session','Split','Replay','Done/total','Time','ETA','Batches','New/reused','Stage') if timing else ('Session','Split','Replay','Time','Gen','Best score','Max net $','Median net $','Max-net DD $','Median DD $','Win %','Profit %','Candidates')
        for h in columns:table.add_column(h,no_wrap=True,**({'max_width':18} if h=='Replay' else {}))
        for i,r in enumerate(rows[start:start+limit],start):
            s=r.get('strength',{});metrics=[number(s.get(k)) for k in ('top_net','median_net','top_drawdown')]
            live=r['replay'];bar=replay_progress(live['progress'],live['status']);numbers=replay_numbers(live)
            if live['status']=='running' and live['age'] is not None and live['age']>10:bar.append(' ~',style='bright_yellow')
            p=live['progress'];elapsed=duration(numbers['elapsed'] if numbers['elapsed'] is not None else r.get('batch_seconds'))
            if compact:cells=[r['day'],'VAL*' if r['role']=='validation' else 'Train',bar,elapsed,metrics[0],metrics[2]]
            elif timing:cells=[r['day'],'VAL*' if r['role']=='validation' else 'Train',bar,f"{numbers['done']}/{numbers['total'] or '?'}",elapsed,duration(numbers['eta']),f"{p.get('completed_local_batches',0)}/{p.get('total_local_batches','?')}",f"{p.get('evaluated_candidates','?')}/{p.get('reused_candidates','?')}",p.get('stage',live['status'])]
            else:cells=[r['day'],'VAL*' if r['role']=='validation' else 'Train',bar,elapsed,str(r['generation']) if r['generation'] else '-',number(s.get('best_score')),*metrics,number(s.get('median_drawdown')),number(s.get('top_win_rate')),number(s.get('profitable_fraction')),str(s.get('candidates','-'))]
            table.add_row(*cells,style=SELECTED if i==model.selection else 'bright_magenta' if r['role']=='validation' else 'bright_cyan')
        loaded=model.global_data;caption=f"Last seal {loaded['generation']}" if loaded else 'No sealed global generation yet'
        caption+=' | metrics update after each replay; Gen = metric generation'
        if model.status().get('stage')=='Paired mutation diagnostic; selection and RNG isolated':caption+=' | Replay/Time: mutation diagnostic; financial columns: main population'
        if model.global_loading:caption+=f' | loading {model.global_loading[1]}/{model.global_loading[2]} sessions'
        if model.global_error:caption+=' | evidence unavailable: '+model.global_error
        if model.live_error:caption+=' | live evidence unavailable: '+model.live_error
        caption+=f' | rows {start+1}-{min(start+limit,len(rows))}/{len(rows)}'
        if rows:
            selected=rows[min(model.selection,len(rows)-1)];live=selected['replay'];sample=live['progress']
            note+='\nReplay gen '+str(live['generation'] or '-')+' | '+selected['day']+' | '+live['status']+' | '+sample.get('stage','')
            if live['age'] is not None:note+=' | update '+duration(live['age'])+' ago'
            counts=replay_numbers(live)
            note+=f"\nCandidates {counts['done']}/{counts['total'] or '?'} | new {sample.get('evaluated_candidates','?')} / reused {sample.get('reused_candidates','?')} | batches {sample.get('completed_local_batches',0)}/{sample.get('total_local_batches','?')} | ETA {duration(counts['eta'])}"
            caption+=' | ~ last observed update'
        caption+=' | A: all candidates | C: timings / strength | VAL*: display-only | Enter: session ranking' if not compact else ' | N/B page | VAL*: display-only'
    elif model.view=='global_splits':
        for h in ('Path','Sessions ready','Candidates','Purpose'):table.add_column(h)
        for i,r in enumerate(model.rows()):
            table.add_row(r['role'].title(),f"{r['coverage']}/{r['total']}",str(len(r['candidates'])), 'Selection score' if r['role']=='training' else 'Display only',style=SELECTED if i==model.selection else None)
        caption='1 Training | 2 Validation | Enter: open selected path'
    elif model.view in ('operators','families','methods','origins','immigration'):
        report=model.report();rows=model.rows()
        compact=width<120
        columns=('Group','Eval','Keep','Best score','Median net','Beat parent') if compact else ('Group','Eval','Keep','Best score','Median net','Change score','Loss $','DD $','Nodes','Beat parent')
        for h in columns:table.add_column(h,no_wrap=True,justify='right')
        for i,r in enumerate(rows[start:start+limit],start):
            cells=[r['group'],str(r['evaluated']),str(r['retained']) if r['retained'] is not None else '-',number(r['best_score']),number(r.get('median_net'))]
            if not compact:cells.extend([number(r.get('median_score_change')),number(r['median_loss']),number(r['median_drawdown']),number(r.get('median_nodes'))])
            cells.append(f"{r['beat_best_parent']}/{r['parent_comparisons']}");table.add_row(*cells,style=SELECTED if i==model.selection else None)
        caption=f"Each row = aggregate group | sort: {GROUP_SORT[model.sort]} | Enter: members | groups overlap"
        if not rows:
            table.add_row('No groups yet',*['']*(len(table.columns)-1))
            if model.day and model.view in ('families','methods'):caption='Founder families and mixing methods apply to global founders. O: session crossover comparison | Esc: back'
    elif model.view=='timings':
        rows=model.rows()
        for h in ('Generation / session','State','Elapsed','ETA','New/reused'):table.add_column(h,no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):table.add_row(r['label'],r['state'],duration(r['elapsed']),duration(r['eta']),r['reuse'],style=SELECTED if i==model.selection else None)
        if not rows:table.add_row('No measured timing yet','pending','-','-','-')
        caption='Sealed generation times + live session replay times | Esc: return | N/B page'
    elif model.view=='reproduction':
        report=model.report();items=model.rows()
        table.add_column('Reproduction diagnostic');table.add_column('Observed value',justify='right')
        for name,value in items[start:start+limit]:table.add_row(name,value)
        if not items:table.add_row('No reproduction evidence','unavailable')
        caption=f"Successor creation after measured generation {report.get('generation',0)}; not the evaluated population's scores."
    elif model.view=='lineage':
        rows=model.lineage_rows();offset=model.detail_page*limit
        for h in ('Step','Parents / source','Operation','Distance / generation'):table.add_column(h)
        for r in rows[offset:offset+limit]:table.add_row(r['step'],r['parents'],r['operator'],r['depth'])
        caption=f"Candidate {(model.selected or {}).get('serial')} | {len(rows)} lineage records | N/B page | Esc: back"
    elif model.view=='detail':
        r=dict(model.selected or {})
        if 'winning_positions' not in r:
            matched=next((v for v in model.status().get('training_ranking',[]) if v['serial']==r.get('serial')),None)
            if matched and model.report().get('generation')==r.get('generation'):r.update({k:matched[k] for k in ('closed_positions','winning_positions') if k in matched})
        trades=r.get('closed_positions');wins=r.get('winning_positions');win_rate=100*wins/trades if wins is not None and trades else None
        mix=r.get('mix') or {};lines=[f"Candidate {r.get('serial')} | {r.get('operation','ranking')} | measured generation {r.get('last_evaluated_generation',r.get('generation',model.report().get('generation')))}",
            f"Score {number(r.get('score'))} | Net {number(r.get('net_pnl'))} | Loss {number(r.get('gross_losses'))} | DD {number(r.get('drawdown'))}",
            f"Closed trades {number(trades)} | Wins {number(wins)} | Win rate {number(win_rate)}% | Nodes {number(r.get('nodes'))}",
            f"Original founder: {r.get('original_founder','unavailable')} | families: {', '.join(r.get('founder_groups',[]))}",
            f"Selected by: {', '.join(r.get('immigration_categories',[]))}",f"Operators: {', '.join(r.get('operators',[]))}",f"Sessions: {', '.join(r.get('source_sessions',[]))}",f"Mix depth: {mix.get('depth','-')} | parents: {mix.get('parent_serials',r.get('parents',[]))}",
            f"Mutations: {mix.get('mutations','see sealed pedigree')} | U lists surviving descendants; founder score is never inherited."]
        origins=r.get('origins') or [o for i,o in model.origin_catalog.items() if int(r.get('origin_mask','0'),16)&(1<<i)]
        count=2 if width<120 else 4
        origin_page=origins[model.detail_page*count:model.detail_page*count+count]
        if width<120:lines=lines[:3]+[lines[4]]
        if model.day:lines=[v for v in lines if not v.startswith(('Selected by:','Mix depth:'))]
        lines.append(f'Origin: {model.day} / phase-1 #{r.get("serial")}' if model.day else f'Phase-1 origins: {len(origins)} | N/B pages')
        lines.extend(f"{o['day']} #{o['source_serial']} | score rank {o.get('score_rank','?')} / net rank {o.get('net_rank','?')} | {o.get('selection','?')}" for o in origin_page)
        table.add_column('Session');table.add_column('Score $',justify='right');table.add_column('Net $',justify='right');table.add_column('Loss $',justify='right');table.add_column('DD $',justify='right')
        generation=r.get('last_evaluated_generation',r.get('generation',model.report().get('generation',0)))
        data=model.read(model.target()/'global'/f'generation-{generation:04d}'/'session-metrics.json')
        detail_data=model.live_data if model.live_data and model.live_data['generation']==generation else model.global_data
        if not model.day and detail_data and detail_data['generation']==generation:
            panels=detail_data['panels'];first=next(iter(panels.values()),None)
            if first:data=dict(ids=first['ids'],sessions={k:v['panel'] for k,v in panels.items()})
        if r.get('metric_scope'):lines[0]+=' | '+r['metric_scope']+(' display-only' if 'validation' in r['metric_scope'] else '')
        if model.day:lines=[line for line in lines if not line.startswith(('Original founder:','Sessions:','Mutations:'))]
        if r.get('serial') in data.get('ids',[]):
            index=data['ids'].index(r['serial'])
            page_size=max(1,height-24);offset=model.detail_page*page_size
            sessions=[(day,p) for day,p in data.get('sessions',{}).items() if not model.aggregate_mode or day.startswith(model.aggregate_role+':')]
            for day,p in sessions[offset:offset+page_size]:table.add_row(day,*(number(p[k][index]) for k in ('score','net_pnl','gross_losses','drawdown')))
        elif model.day:table.add_row(model.day,*(number(r.get(k)) for k in ('score','net_pnl','gross_losses','drawdown')))
        else:table.add_row('Per-session detail unavailable','-','-','-','-')
        from rich.console import Group
        table=Group(Text('\n'.join(lines)),table);caption='One candidate: metrics and ancestry | Esc: back | L: lineage | - = unavailable'
    elif model.aggregate_mode:
        rows=model.rows();report=model.report();compact=width<120
        columns=('Serial','Global score $','Global net $','DD sum $','Nodes') if compact else ('Serial','Global score $','Global net $','Loss $','DD sum $','Worst DD $','Win %','Trades','Nodes','Origins','Technique')
        for h in columns:table.add_column(h,justify='right',no_wrap=True)
        for i,r in enumerate(rows[start:start+limit],start):
            cells=[str(r['serial']),number(r['score']),number(r['net_pnl'])]
            if compact:cells.extend([number(r['drawdown']),str(r.get('nodes','-'))])
            else:cells.extend([number(r['gross_losses']),number(r['drawdown']),number(r.get('worst_drawdown')),number(r.get('win_rate')),str(int(r['closed_positions'])),str(r.get('nodes','-')),str(len(r.get('source_sessions',[]))),', '.join(r.get('operators',[]))])
            table.add_row(*cells,style=SELECTED if i==model.selection else None)
        if not rows:table.add_row('Loading',*['-']*(len(columns)-1))
        caption=f"GLOBAL {model.aggregate_role.upper()} | {report.get('coverage',0)}/{report.get('total',24)} sessions | {'PARTIAL totals' if report.get('partial',True) else 'COMPLETE totals'} | {len(rows)} candidates | sort {model.sort} | Enter detail | L lineage"
    else:
        rows=model.rows()
        compact=width<120;fields=('score','net_pnl','gross_losses','drawdown') if compact else ('score','net_pnl','gross_losses','drawdown','nodes')
        for h in (('Serial','Score $','Net $','Loss $','DD $') if compact else ('Serial','Score $','Net $','Loss $','DD $','Nodes')):table.add_column(h,no_wrap=True,justify='right',min_width=6 if h=='Serial' else 9)
        table.add_column('Technique',no_wrap=True,max_width=12 if compact else 65)
        for i,r in enumerate(rows[start:start+limit],start):table.add_row(str(r['serial']),*(number(r.get(k)) for k in fields),', '.join(r.get('operators',r.get('founder_groups',[]))),style=SELECTED if i==model.selection else None)
        status=model.status();caption=f"{'VALIDATION display-only' if model.validation and model.view=='ranking' else 'TRAINING'} | {model.view} | {len(rows)} candidates | sort {model.sort} | {status.get('stage','Not evaluated')}"
        if model.view=='global_ranking':caption=f"GLOBAL {model.global_role} {'display-only' if model.global_role=='validation' else ''} | {model.global_day} | sealed generation {model.global_data['generation'] if model.global_data else '-'} | {len(rows)} candidates | sort {model.sort}"
        if model.filter:caption=f"Group {model.filter[1]} | {len(rows)} individual candidates | sort {model.sort} | Esc: groups"
        elif model.view in ('ranking','candidates'):caption=f"Each row = one candidate | {len(rows)} candidates | sort {model.sort} | Enter: metrics + ancestry"
        if not rows:table.add_row('Pending',*['']*(len(fields)+1))
    if model.view!='overview':
        state=model.status();t=model.timing(model.target(),state,running=state.get('status')=='running' and time.time()-state.get('updated_epoch',0)<15,complete=state.get('status')=='complete')
        note+='\nLast gen '+duration(t['last'])+' | current '+duration(t['elapsed'])+' | work '+duration(t['work'])+' | session ETA '+('~'+duration(t['eta']) if t['eta'] is not None else 'warming / unavailable')
        note+='\nGeneration ETA '+('~'+duration(t['generation_eta']) if t['generation_eta'] is not None else '-')+' | D: measured generation history'
    header=Text(title+'\n'+note,style='bright_cyan')
    if model.view!='overview':
        header.append(f"\nGenerations {state.get('completed_generations',0)}/50 ",style='white');header.append(progress_bar(state.get('completed_generations',0),50,width=max(20,min(60,width-38)) if model.view=='global_sessions' else 10,color='bright_green'))
        if model.view=='global_sessions':
            diagnostic=state.get('stage')=='Paired mutation diagnostic; selection and RNG isolated'
            active_rows=[r for r in rows if r['role']=='training'] if diagnostic else rows
            completed=sum(r['replay']['status']=='complete' for r in active_rows);work=sum(replay_numbers(r['replay'])['fraction'] for r in active_rows)
            label='Mutation replay' if diagnostic else 'Overall replay'
            header.append(f'\n{label} {completed}/{len(active_rows)} sessions ',style='white');header.append(progress_bar(work,len(active_rows),width=max(15,min(50,width-45))))
        if model.view!='global_sessions':header.append(' | Replay ',style='white');header.append(replay_progress(state.get('worker_progress',{}),state.get('status','queued')))
        p=state.get('worker_progress',{});header.append('\n'+state.get('stage','Waiting for session')+' | '+p.get('stage',''),style='bright_yellow')
    layout['head'].update(header)
    names={'global_splits':'ALL GLOBAL > TRAINING / VALIDATION','ranking':'ALL GLOBAL '+model.aggregate_role.upper()+' CANDIDATES' if model.aggregate_mode else 'SESSION CANDIDATES','operators':'GROUPS > CROSSOVER TECHNIQUE','families':'GROUPS > FOUNDER FAMILY','methods':'GROUPS > MIXING METHOD','global_sessions':'GLOBAL SESSION RESULTS','candidates':'GROUP > '+str(model.filter[1])+' > CANDIDATES' if model.filter else 'ALL CANDIDATES'}
    layout['body'].update(Panel(table,title=names.get(model.view,model.view.upper()),subtitle=caption,padding=0))
    if width<120:
        keys='1 candidates | 2 groups | Enter open | Esc back | Q close\nO crossover | F family | M method | P source | E immigration\nArrows select | S sort | R reverse | N/B page | L lineage\nH overview | G sessions | A all global | C columns | D timings'
    else:keys='1: CANDIDATES (individuals) | 2: GROUPS (aggregate statistics)\nGroups O: crossover | F: family | M: method | P: source session | E: immigration\nArrows: select | Enter: open | Esc: back | S: sort | R: reverse | N/B: page\nH overview | G sessions | A all global | C columns | D timings | L lineage | Q close'
    if model.view=='global_splits':keys='1 / T: Training | 2 / V: Validation | Arrows: select | Enter: open\nEach split has its own candidates, groups, sorting, and details.\nValidation is display-only and never influences selection.\nEsc: back | G: global sessions | Q: close panel'
    layout['keys'].update(Text(keys))
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
                        key=read_key(msvcrt.getwch)
                        if key=='q':return
                        model.key(key,live.dimensions()[1]);break
                    time.sleep(.05)
    except KeyboardInterrupt:return
    finally:model.close()
if __name__=='__main__':main()
