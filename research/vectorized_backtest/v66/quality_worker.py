"""Incremental, disposable observer caches; never write optimizer artifacts."""
import gc,hashlib,json,time
from pathlib import Path
import numpy as np
from .publication import write_json
from .progress_sessions import population_metadata,read_verified
from .quality_metrics import extract,combine,summary,generalization,finite

VERSION=1
_reads={}
_failed={}


def cached_read(path):
    key=str(path);version=stamp(path)
    if key not in _reads or _reads[key][0]!=version:
        _reads[key]=(version,read_verified(path))
        if len(_reads)>4:_reads.pop(next(iter(_reads)))
    return _reads[key][1]


def stamp(path):
    s=Path(path).stat();return [s.st_size,s.st_mtime_ns]


def local(path,job):
    from .distributed_assets import remote
    return remote(path,job['workstation_share']) if str(job['campaign']).startswith('\\\\') else Path(path)


def fees(metrics,job):
    import polars as pl
    refs=metrics.get('execution_orders');n=len(metrics['net_pnl'])
    if refs is None:return None
    out=np.full(n,np.nan);groups={}
    for i,r in enumerate(refs):groups.setdefault(r['receipt'],[]).append((r['candidate_index'],i,r['sha256']))
    for path,pairs in groups.items():
        p=local(path,job);manifest=read_verified(p)
        if any(manifest['candidate_sha256'][candidate]!=sha for candidate,_,sha in pairs):raise ValueError('Order journal candidate identity mismatch')
        files=[str(p.parent/v['file']) for v in manifest['pages']]
        if not files:
            for _,i,_ in pairs:out[i]=0.
            continue
        frame=pl.scan_parquet(files).group_by('candidate').agg(pl.col('fees').sum()).collect(engine='streaming')
        values=dict(frame.iter_rows())
        for candidate,i,_ in pairs:out[i]=values.get(candidate,0.)
    return out


def cache_session(job,cache,generation,role,day):
    root=Path(job['campaign'])/'phase-2';folder=root/'global'/f'generation-{generation:04d}'
    path=root/'replays'/role/f'generation-{generation:04d}'/day/'receipt.json'
    ids,token=population_metadata(folder/'population.json');raw=path.read_bytes();r=json.loads(raw)
    if r['population_sha256']!=token or r['day']!=day or r['split']!=role or not r['full_session'] or r['execution']!=job['execution'] or r['validation_opened']!=(role=='validation'):
        raise ValueError('Quality receipt identity mismatch')
    if len(r['metrics']['net_pnl'])!=len(ids):raise ValueError('Quality candidate alignment mismatch')
    warning=None
    try:commission=fees(r['metrics'],job)
    except (OSError,ValueError,KeyError) as exc:commission=None;warning='Commission evidence unavailable: '+str(exc)
    arrays=extract(r['metrics'],commission);arrays['ids']=np.asarray(ids,np.int64)
    if len(r.get('program_keys',[]))!=len(ids):raise ValueError('Missing genome identity evidence')
    arrays['keys']=np.asarray(r['program_keys'])
    target=cache/f'{generation}-{role}-{day}.npz';temporary=target.with_suffix('.tmp.npz')
    np.savez_compressed(temporary,**arrays);temporary.replace(target)
    write_json(target.with_suffix('.json'),dict(stamp=stamp(path),sha256=hashlib.sha256(raw).hexdigest(),token=token,warning=warning))
    del r,raw,arrays;gc.collect()


def groups(records):
    from .templates import FAMILIES
    from .evolution_report import operators
    result={}
    for i,r in enumerate(records):
        t=r.get('template_ancestry',0);random=r.get('random_ancestry',0)
        origin='mixed' if t and random else 'template' if t else 'random' if random else 'unknown'
        tags=['origin: '+origin]+['family: '+f for f,v in zip(FAMILIES,r.get('family_contributions',[])) if v]
        tags+=['operator: '+op for op in operators(r)]
        parents=sorted({p for p in r.get('parents',[]) if p>=0})
        tags+=['lineage: parent '+str(p) for p in parents] if parents else ['lineage: fresh (no parents)']
        for tag in tags:result.setdefault(tag,[]).append(i)
    return result


def load_arrays(path):
    with np.load(path,allow_pickle=False) as z:return {k:z[k] for k in z.files}


def build_generation(job,cache,generation,signature):
    root=Path(job['campaign'])/'phase-2/global';folder=root/f'generation-{generation:04d}'
    ids,token=population_metadata(folder/'population.json');records=read_verified(folder/'evaluated-pedigree.json')
    by_id={r['serial']:r for r in records};records=[by_id[i] for i in ids]
    previous_path=cache/f'population-{generation-1}.npz'
    previous=load_arrays(previous_path) if previous_path.exists() else {}
    previous_ids=set(previous.get('ids',[]).tolist()) if previous else set()
    fresh=np.asarray([i not in previous_ids for i in ids]) if generation else np.zeros(len(ids),bool)
    cohort={'all':list(range(len(ids))),'offspring':np.flatnonzero(fresh).tolist(),'survivors':np.flatnonzero(~fresh).tolist() if generation else []}
    next_path=root/f'generation-{generation+1:04d}/population.json'
    next_ids=set(population_metadata(next_path)[0]) if next_path.exists() else None
    report=dict(version=VERSION,generation=generation,signature=signature,updated=time.time(),sealed=(folder/'complete.json').exists(),roles={},generalization={'available':False})
    packed={'ids':np.asarray(ids)};totals={};candidate_rows={};group_indices=groups(records)
    for role in ('training','validation'):
        days=[d for d in job[role+'_days'] if (cache/f'{generation}-{role}-{d}.npz').exists()]
        if not days:continue
        sessions=[load_arrays(cache/f'{generation}-{role}-{d}.npz') for d in days]
        if any(s['ids'].tolist()!=ids for s in sessions):raise ValueError('Cached session IDs differ')
        p=combine(sessions);totals[role]=p;keys=sessions[0]['keys'];delta=np.full(len(ids),np.nan)
        prior_days=previous.get(role+'_days',np.array([])).tolist()
        if days==job[role+'_days'] and prior_days==days:
            scores=dict(zip(previous['ids'].tolist(),previous[role+'_score'].tolist()))
            for i,r in enumerate(records):
                parents=[v for v in r.get('parents',[]) if v>=0]
                if fresh[i] and parents and all(v in scores for v in parents):delta[i]=p['score'][i]-max(scores[v] for v in parents)
        stats=lambda idx:summary(p,idx,records,keys,sessions,len(days),len(job[role+'_days']),delta,next_ids)
        cohorts={k:stats(v) for k,v in cohort.items()}
        prior_keys=set(previous.get('keys',np.array([])).tolist())
        for name,idx in cohort.items():
            if generation and len(idx):cohorts[name]['no_change_offspring']=sum(fresh[i] and keys[i] in prior_keys for i in idx)
        report['roles'][role]=dict(days=days,total=len(job[role+'_days']),cohorts=cohorts,
            groups=[dict(group=g,**stats(idx)) for g,idx in group_indices.items()])
        packed.update({role+'_'+k:v for k,v in p.items() if k!='behavior'})
        packed[role+'_days']=np.asarray(days);packed['keys']=keys
        rows=[];memberships=[[] for _ in ids]
        for g,idx in group_indices.items():
            for i in idx:memberships[i].append(g)
        for i,serial in enumerate(ids):
            r=records[i]
            row=dict(serial=serial,generation=generation,nodes=r.get('node_count'),parents=r.get('parents',[]),
                operation=r.get('operation'),groups=memberships[i],
                new_offspring=bool(fresh[i]),metric_scope=role,coverage=len(days),
                beat_parent=finite(delta[i]),sessions={role+':'+d:{k:finite(s[k][i]) for k in ('score','net_pnl','gross_losses','drawdown','closed_positions')} for d,s in zip(days,sessions)})
            row.update({k:finite(v[i]) for k,v in p.items() if k!='behavior'})
            rows.append(row)
        candidate_rows[role]=rows
        del sessions
    if 'training' in totals and 'validation' in totals:
        report['generalization']=generalization(totals['training'],totals['validation'],ids,len(report['roles']['training']['days']),len(report['roles']['validation']['days']))
        report['generalization']['complete']=all(len(report['roles'][r]['days'])==len(job[r+'_days']) for r in totals)
        for role,rows in candidate_rows.items():
            other='validation' if role=='training' else 'training'
            for i,row in enumerate(rows):
                row.update(opposite_net=finite(totals[other]['net_pnl'][i]),opposite_score=finite(totals[other]['score'][i]))
                row['sessions'].update(candidate_rows[other][i]['sessions'])
    # Small timing/reproduction artifacts, never infer missing GPU measurements.
    report['timings']=read_verified(folder/'generation-timings.json') if (folder/'generation-timings.json').exists() else {}
    reproduction=read_verified(folder/'reproduction.json') if (folder/'reproduction.json').exists() else {}
    report['reproduction']=reproduction.get('telemetry',{})
    elapsed=0.
    for role in ('training','validation'):
        for day in report['roles'].get(role,{}).get('days',[]):
            t=Path(job['campaign'])/'phase-2/replays'/role/f'generation-{generation:04d}'/day/'timings.json'
            if t.exists():elapsed+=sum(v.get('replay_seconds',0.) for v in read_verified(t))
    report['replay_worker_seconds']=elapsed
    report['warnings']=[read_verified(cache/f'{generation}-{role}-{d}.json')['warning'] for role,v in report['roles'].items() for d in v['days'] if read_verified(cache/f'{generation}-{role}-{d}.json').get('warning')]
    np.savez_compressed(cache/f'population-{generation}.npz',**packed)
    for role,rows in candidate_rows.items():write_json(cache/f'candidates-{generation}-{role}.json',rows)
    write_json(cache/f'generation-{generation}.json',report)
    return report


def refresh(job,cache_path):
    """One receipt per invocation bounds I/O bursts and lets UI requests interleave."""
    cache=Path(cache_path);cache.mkdir(parents=True,exist_ok=True);root=Path(job['campaign'])/'phase-2'
    generations=sorted(int(p.name.split('-')[-1]) for p in (root/'global').glob('generation-*') if (p/'evaluated-pedigree.json').exists())
    processed=0;pending=0;changed=set();errors=[];completed=0
    for gen in generations:
        for role in ('training','validation'):
            for day in job[role+'_days']:
                path=root/'replays'/role/f'generation-{gen:04d}'/day/'receipt.json'
                if not path.exists():continue
                meta=cache/f'{gen}-{role}-{day}.json'
                if meta.exists() and read_verified(meta)['stamp']==stamp(path):continue
                pending+=1
                if processed:continue
                failure=_failed.get(str(path))
                if failure and failure[0]==stamp(path) and time.time()-failure[1]<60:
                    errors.append(failure[2]);continue
                try:cache_session(job,cache,gen,role,day);changed.add(gen);_failed.pop(str(path),None);completed+=1
                except Exception as exc:
                    error=f'{gen}/{role}/{day}: {exc}';errors.append(error);_failed[str(path)]=(stamp(path),time.time(),error)
                processed+=1
    history=[]
    for gen in generations:
        folder=root/'global'/f'generation-{gen:04d}'
        evidence=sorted(cache.glob(f'{gen}-*.json'))
        inputs=evidence+[p for p in (folder/'complete.json',folder/'reproduction.json',folder/'generation-timings.json',root/'global'/f'generation-{gen+1:04d}/population.json',cache/f'population-{gen-1}.npz') if p.exists()]
        signature=hashlib.sha256(json.dumps([(str(p),stamp(p)) for p in inputs]).encode()).hexdigest()
        dest=cache/f'generation-{gen}.json';report=read_verified(dest) if dest.exists() else None
        if evidence and (report is None or report['signature']!=signature):report=build_generation(job,cache,gen,signature)
        if report:history.append({k:v for k,v in report.items() if k not in ('reproduction','signature','generalization','roles')}|dict(roles={r:dict(days=v['days'],total=v['total'],cohorts=v['cohorts']) for r,v in report['roles'].items()},generalization=report['generalization']))
    index=dict(version=VERSION,updated=time.time(),pending=max(0,pending-completed),errors=errors,history=history)
    write_json(cache/'index.json',index);return index


def read_view(cache_path,generation,role,group=None,cohort='all'):
    cache=Path(cache_path);p=cache/f'generation-{generation}.json'
    report=cached_read(p) if p.exists() else {}
    path=cache/f'candidates-{generation}-{role}.json';rows=cached_read(path) if path.exists() else []
    if group:rows=[r for r in rows if group in r['groups']]
    if cohort!='all':rows=[r for r in rows if generation>0 and r['new_offspring']==(cohort=='offspring')]
    return dict(report=report,rows=[{k:v for k,v in r.items() if k!='sessions'} for r in rows])


def read_detail(cache_path,generation,role,serial):
    p=Path(cache_path)/f'candidates-{generation}-{role}.json'
    return next((r for r in cached_read(p) if r['serial']==serial),{}) if p.exists() else {}
