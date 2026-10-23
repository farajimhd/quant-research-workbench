"""Training-only, bounded 3x Phase-2 screening; no optimizer RNG consumption."""
import json
from pathlib import Path
import numpy as np
from .run_search import digest

POLICY = 'v42-historical-3x-screen-v1'


def choose(records, count, seen, rng, *, random=False):
    """Prefer distinct evaluated generations before a second pass."""
    if count<=0:return []
    options=[r for r in records if r['canonical_sha256'] not in seen]
    if random:
        options=[options[i] for i in rng.permutation(len(options))]
    else:
        options=sorted(options,key=lambda r:(-r['score'],r['generation'],r['serial']))
    selected=[];generations=set()
    for distinct in (True,False):
        for r in options:
            key=r['canonical_sha256']
            if key in seen or (distinct and r['generation'] in generations):continue
            selected.append(r);seen.add(key);generations.add(r['generation'])
            if len(selected)==count:return selected
    return selected


def historical_experts(pools, archives, seed):
    """96/session: five bands of 8 objective + 8 stratified, then 16 archive."""
    rng=np.random.default_rng(np.random.SeedSequence([seed,4203072]))
    seen=set();selected=[];report=[];deficits={}
    for day in sorted(pools):
        selected_day=[]
        for lo in range(1,101,20):
            rows=[r for r in pools[day] if lo<=r['generation']<lo+20 and r['net_profit']>0 and np.isfinite(r['score'])]
            best=choose(rows,8,seen,rng)
            # Band membership is based on the original eligible range, before
            # objective picks. Duplicate exclusions never change percentile edges.
            ordered=sorted(rows,key=lambda r:(-r['net_profit'],r['canonical_sha256']))
            edges=[0,*[int(np.ceil(len(ordered)*x)) for x in (.1,.3,.6)],len(ordered)]
            sampled=[]
            for start,end in zip(edges[:-1],edges[1:]):
                sampled+=choose(ordered[start:end],2,seen,rng,random=True)
            selected_day+=best+sampled
            report.append(dict(day=day,range=[lo,lo+19],available=len(rows),objective=len(best),sampled=len(sampled),shortfall=16-len(best)-len(sampled)))
        extra=[]
        for r in archives[day]:
            if r['canonical_sha256'] in seen:continue
            extra.append(r);seen.add(r['canonical_sha256'])
            if len(extra)==16:break
        selected_day+=extra
        # Explicit fallback: other retained candidates from this session first.
        missing=96-len(selected_day)
        fallback=choose(pools[day],missing,seen,rng) if missing else []
        selected_day+=fallback;selected+=selected_day;deficits[day]=96-len(selected_day)
        report.append(dict(day=day,archive=len(extra),same_session_fallback=len(fallback),remaining_shortfall=deficits[day]))
    # Then other sessions, round-robin; fresh initialization fills final deficit.
    cross=[]
    while len(selected)<96*len(pools):
        added=0
        for day in sorted(pools):
            rows=choose(pools[day],1,seen,rng)
            selected+=rows;added+=len(rows)
            if rows:cross.append(dict(day=day,canonical_sha256=rows[0]['canonical_sha256']))
            if len(selected)==96*len(pools):break
        if not added:break
    return selected,dict(bands=report,cross_session_fallback=cross,unfilled=96*len(pools)-len(selected),sampling_seed=[seed,4203072])


def verify_record(folder, checkpoint, record, cache=None):
    """Bind payload to checkpoint, then its evaluated serial to sealed receipts."""
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    from .genome import Individual
    from .lineage import program_key
    g=record['generation'];gd=Path(folder)/f'generation-{g:04d}'
    cache={} if cache is None else cache;key=(record['day'],g)
    if key not in cache:
        seal=json.loads((gd/'complete.json').read_bytes())
        if file_hash(gd/'complete.json')!=checkpoint['state']['seals'][str(g)]:raise ValueError('Historical generation seal changed')
        scores=json.loads((gd/'scores.json').read_bytes());receipt=json.loads((gd/record['day']/'receipt.json').read_bytes())
        if file_hash(gd/'scores.json')!=seal['scores_sha256'] or file_hash(gd/record['day']/'receipt.json')!=seal['receipt_sha256']:raise ValueError('Historical evaluation changed')
        if receipt['validation_opened'] is not False:raise ValueError('Historical validation crossed')
        cache[key]={serial:(eligible,score,profit) for serial,eligible,score,profit in zip(scores['ids'],scores['eligible'],scores['scores'],receipt['metrics']['net_pnl'])}
    eligible,value,profit=cache[key][record['serial']]
    if not eligible or value!=record['score'] or profit!=record['net_profit']:raise ValueError('Historical candidate score changed')
    if program_key(Individual.restore(record['payload']))!=record['canonical_sha256']:raise ValueError('Historical candidate identity changed')


def survivor_indices(scores, eligible, count):
    scores=np.asarray(scores,dtype=float);eligible=np.asarray(eligible,dtype=bool)
    if not np.isfinite(scores).all() or eligible.shape!=scores.shape:raise ValueError('Invalid screen scores')
    order=np.lexsort((np.arange(len(scores)),-scores));order=order[eligible[order]]
    if len(order)<count:raise ValueError('Insufficient feasible screening candidates; preserve evidence')
    return order[:count].tolist()


def verify_screen(pool):
    """Reconcile the selected population against the durable wide-pool proof."""
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    proof=pool['screen'];root=Path(proof['root'])
    wide=json.loads((root.parent/'initial-screen-pool.json').read_bytes())
    if wide['pool_sha256']!=proof['wide_pool_sha256'] or digest({k:v for k,v in wide.items() if k!='pool_sha256'})!=wide['pool_sha256']:raise ValueError('Wide screening pool changed')
    rows=json.loads((root/'scores.json').read_bytes())
    if digest(rows)!=proof['scores_sha256'] or len(rows)!=3072:raise ValueError('Screen scores changed')
    chosen=survivor_indices([r['score'] for r in rows],[r['eligible'] for r in rows],1024)
    if chosen!=proof['selected_indices'] or pool['population']!=[wide['population'][i] for i in chosen] or pool['sources']!=[wide['sources'][i] for i in chosen]:raise ValueError('Screen survivors changed')
    for c in proof['cohorts']:
        if file_hash(Path(c['path'])/'complete.json')!=c['seal_sha256']:raise ValueError('Screen cohort seal changed')


def screen(pool, training, output, *, population_size, batch_size, session_workers,
           capture_gib, host_reserve_gib, host_admission_gib, distributed_job, shared_replay=False):
    """Three bounded cohorts; resume sealed cohorts, reuse survivors at global G1."""
    from dataclasses import asdict
    from .run_search import bind_dataset,read_panel,source_hash
    from .evaluator import Execution
    from .objectives import score,Objective
    from .complexity import count
    from .genome import Individual
    from .terminal import CampaignProgress
    from .publication import write_json
    from .session_lanes import SessionLanes
    from .distributed_lanes import DistributedLanes
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
    output=Path(output);screen_root=output/'initial-screen';screen_root.mkdir(parents=True,exist_ok=True)
    train=bind_dataset(training,'training');execution=asdict(Execution());rows=[];cohorts=[]
    contract=dict(version=POLICY,source_sha256=source_hash(),pool_sha256=pool['pool_sha256'],population=len(pool['population']),generations=1,
                  input_receipts=train['receipts'],batch_size=batch_size,session_workers=session_workers,execution=execution,session_execution='independent',validation_feedback=False)
    manifest=screen_root/'manifest.json'
    if manifest.exists() and json.loads(manifest.read_bytes())!=contract:raise ValueError('Screen resume contract changed')
    with owned_run(screen_root,version=POLICY),CampaignProgress(screen_root,contract) as panel:
        write_json(manifest,contract)
        for offset in range(0,len(pool['population']),population_size):
            chunk=pool['population'][offset:offset+population_size];members=[Individual.restore(p) for p in chunk];token=digest(chunk)
            base=screen_root/f'cohort-{offset//population_size:02d}';folder=base/'training'/'generation-0001'
            cfg=dict(inputs=train['inputs'],history=train['history'],feature_cache=train.get('feature_cache'),indicator_root=train['indicator_root'],enriched_root=train['enriched_root'],
                output=str(base/'training'),distributed_job=distributed_job,boundary_stop=str(output),device='cuda',backend='compile',batch_size=batch_size,replay_width_workers=1,
                maximum_data_gib=4.,maximum_gate_gib=8.,maximum_host_sessions=2,execution=execution,source_sha256=source_hash(),split='training',episode_capacity=65536,
                plan_limit=2,capture_gib=capture_gib,host_reserve_gib=host_reserve_gib,host_admission_gib=host_admission_gib,host_prefetch=False,shared_replay=False)
            seal_path=folder/'complete.json';lane=DistributedLanes if distributed_job else SessionLanes
            panel.begin_generation(1);panel.begin_evaluation('training',len(train['days']),len(chunk));panel.update(stage=f'Initial 3x screen: cohort {offset//population_size+1}/3',validation_status='Not opened during screening')
            if seal_path.exists():
                sealed=json.loads(seal_path.read_bytes())
                if sealed['population_sha256']!=token:raise ValueError('Screen population changed')
                for day,h in sealed['session_receipts'].items():
                    if file_hash(folder/day/'receipt.json')!=h:raise ValueError('Screen receipt changed')
            else:
                with lane(cfg,session_workers,panel) as workers:
                    workers.generation(train['days'],members,1,token);workers.release_plans()
            ordered,hashes=read_panel(folder,train['days'],token)
            for day in train['days']:
                r=json.loads((folder/day/'receipt.json').read_bytes())
                if r['split']!='training' or r['validation_opened'] is not False or r['execution']!=execution:raise ValueError('Screen training contract changed')
            result=score(ordered,[count(m) for m in members],Objective())
            rows.extend(dict(index=offset+i,score=float(result['score'][i]),eligible=bool(result['feasible'][i])) for i in range(len(chunk)))
            write_json(seal_path,dict(population_sha256=token,session_receipts=hashes,validation_feedback=False))
            cohorts.append(dict(path=str(folder),seal_sha256=file_hash(seal_path),population_sha256=token))
            del ordered,result,members
        chosen=survivor_indices([r['score'] for r in rows],[r['eligible'] for r in rows],population_size)
        selected=dict(pool,population=[pool['population'][i] for i in chosen],sources=[pool['sources'][i] for i in chosen])
        selected['expert_count']=sum(s is not None for s in selected['sources']);selected['fresh_count']=population_size-selected['expert_count']
        selected['population_sha256']=digest(selected['population']);selected['screen']=dict(policy=POLICY,root=str(screen_root),wide_pool_sha256=pool['pool_sha256'],initial_count=len(pool['population']),selected_indices=chosen,cohorts=cohorts,scores_sha256=digest(rows),validation_feedback=False)
        selected.pop('pool_sha256',None);selected['pool_sha256']=digest(selected)
        write_json(screen_root/'scores.json',rows);write_json(screen_root/'selection.json',selected['screen'])
        # Copy numeric candidate rows from verified screening receipts. This is
        # G1's exact frozen population, so no second training replay is necessary.
        target=output/'training/generation-0001';target.mkdir(parents=True,exist_ok=True);hashes={}
        for day in train['days']:
            records=[json.loads((Path(c['path'])/day/'receipt.json').read_bytes()) for c in cohorts]
            first=records[0]
            if any(r['identity']!=first['identity'] or r['execution']!=first['execution'] for r in records):raise ValueError('Screen cohort data identity changed')
            metrics={k:[records[i//population_size]['metrics'][k][i%population_size] for i in chosen] for k in first['metrics']}
            receipt=dict(first,metrics=metrics,population_sha256=selected['population_sha256'],reused_candidates=population_size,evaluated_candidates=0,
                         screen_provenance=selected['screen'])
            write_json(target/day/'receipt.json',receipt);hashes[day]=file_hash(target/day/'receipt.json')
        write_json(target/'screen-reuse.json',dict(population_sha256=selected['population_sha256'],session_receipts=hashes,screen=selected['screen'],validation_feedback=False))
        panel.update(status='complete',stage='Selected 1,024 from 3,072 training candidates',completed_generations=1)
    return selected
