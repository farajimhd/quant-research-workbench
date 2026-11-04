"""One immutable training cohort per review: shared session loads, bounded lanes.

Only qualified publication remains interactive; this runner never writes inboxes.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,queue,time
from pathlib import Path
import numpy as np
from .publication import write_json
from .progress_sessions import read_verified
from .distributed_assets import sha
from .identity import digest


def prepare(packet,job_path,out):
    from .genome import Individual
    from .lineage import program_key
    from .authority import construct
    from .review_repairs import simplify_boolean_decisions
    from .composition import mutation
    from .agent_intervention import evidence
    packet=Path(packet);out=Path(out);job=read_verified(job_path)
    meta=read_verified(packet/'packet.json');parents=read_verified(packet/'parents.json')
    if meta['scope']!='training' or meta['training_sha256']!=job['training_sha256']:raise ValueError('Training identity changed')
    folder,binding=evidence(Path(job['campaign'])/'phase-2/global',meta['source_generation'])
    if binding!=meta['evidence']:raise ValueError('Packet evidence changed')
    population=read_verified(folder/'population.json');lookup=dict(zip(population['ids'],population['population']))
    existing={program_key(Individual.restore(p)) for p in population['population']}
    members=[];records=[];repairs={};used=set(existing)
    for i,parent in enumerate(parents):
        member=Individual.restore(parent['payload']);key=program_key(member)
        if program_key(construct(member))!=key or program_key(Individual.restore(lookup[parent['serial']]))!=key:
            raise ValueError('Parent construction/source changed')
        members.append(parent['payload'])
        child,proof=simplify_boolean_decisions(member)
        if proof:repairs[i]=(child,proof)
        records.append(dict(serial=parent['serial'],parent_index=i,parent_sha256=key,child_index=None,
            decision='no_supported_repair',proof=[],control_attempts=[]))
    # Controls cover the existing diverse packet; no score-based retry/admission.
    candidates=[i for i in range(len(parents)) if i not in repairs]
    candidates += [i for i in range(len(parents)) if i in repairs]
    controls=set(candidates[:min(64,len(candidates))]);directed=0
    for i,parent in enumerate(parents):
        record=records[i];member=Individual.restore(parent['payload']);child=None
        if i in controls:
            for attempt in range(8):
                try:
                    proposal,_=mutation(np.random.default_rng(np.random.SeedSequence([meta['review_epoch'],parent['serial'],attempt])),member,1)
                    proposal=construct(proposal);key=program_key(proposal)
                    if key in used:raise ValueError('Duplicate canonical program')
                    child=proposal;record['decision']='control';break
                except ValueError as error:record['control_attempts'].append(dict(attempt=attempt,reason=str(error)))
        elif i in repairs and directed<192:
            proposal,proof=repairs[i];key=program_key(proposal)
            if key not in used:
                child=proposal;record.update(decision='boolean_simplification',proof=proof);directed+=1
            else:record['decision']='duplicate_repair_rejected'
        if child is not None:
            key=program_key(child);used.add(key);record.update(child_index=len(members),child_sha256=key)
            members.append(child.payload())
    plan=dict(version='v66-review-cohort-v1',scope='training',job_sha256=sha(job_path),
        training_sha256=job['training_sha256'],source_generation=meta['source_generation'],review_epoch=meta['review_epoch'],
        evidence=binding,parents_sha256=sha(packet/'parents.json'),packet=str(packet),members=members,records=records,
        days=job['training_days'],execution=job['execution'],validation_used=False,
        rationale='Exact mask-preserving Boolean wrapper removal; independent ordinary mutation controls. No profitability claim from semantic equivalence.',
        directed=directed,controls=sum(r['decision']=='control' for r in records),
        shortfall='No unsupported directed edits are generated to fill quota.')
    out.mkdir(parents=True,exist_ok=True);path=out/'plan.json'
    if path.exists() and digest(read_verified(path))!=digest(plan):raise ValueError('Immutable cohort plan changed')
    write_json(path,plan);return plan


def run(plan_path,job_path,out,shard_index,shard_count):
    from .full_common import config
    from .session_lanes import Worker
    from .compact_inference import Inference
    from .review_observer import Observer
    from .agent_intervention import evidence
    from .lineage import program_key
    from .genome import Individual
    from .runtime import code_hash
    if not 1<=shard_count<=4 or not 0<=shard_index<shard_count:raise ValueError('Invalid diagnostic shard')
    plan=read_verified(plan_path);job=read_verified(job_path);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if job.get('review_window'):raise ValueError('Separate cohort replay is disabled for bounded evidence reviews')
    if plan['job_sha256']!=sha(job_path) or plan['scope']!='training' or plan['validation_used']:raise ValueError('Cohort scope changed')
    if not 1<=len(plan['members'])<=512:raise ValueError('Cohort exceeds normal bounded review capacity')
    folder,binding=evidence(Path(job['campaign'])/'phase-2/global',plan['source_generation'])
    if binding!=plan['evidence']:raise ValueError('Source evidence changed')
    baseline=read_verified(folder/'training.json',binding['files']['training.json'])
    population=read_verified(folder/'population.json');lookup={serial:i for i,serial in enumerate(population['ids'])}
    keys=[program_key(Individual.restore(p)) for p in plan['members']];token=digest(plan['members'])
    spec=dict(plan_sha256=sha(plan_path),job_sha256=sha(job_path),code_sha256=code_hash(),
        shard_index=shard_index,shard_count=shard_count,days=plan['days'][shard_index::shard_count],population_sha256=token)
    if (out/'spec.json').exists() and read_verified(out/'spec.json')!=spec:raise ValueError('Cohort resume identity changed')
    write_json(out/'spec.json',spec)
    cfg=config(job,'workstation','training');cfg.update(output=str(out/'replay'),stop_roots=[str(out),str(out.parent)],
        batch_size=len(plan['members']),maximum_replay_batch_size=len(plan['members']),host_prepare_workers=1,
        host_prefetch=True,host_prefetch_depth=1,maximum_host_sessions=2,host_reserve_gib=64.,host_admission_gib=4.,lane_memory_bytes=8*2**30,
        retain_session_residency=False,reuse_results=False,prefetch_transfer=False,replay_transfer_mode='synchronous',
        replay_time_block=256,execution_diagnostics=True,compile_cache_root=str(out),indexed_results=True,
        diagnostic_feature_workers=4,
        review_feature_cache=str(Path(job['campaign'])/'phase-2/global/agent-reviews/feature-cache'))
    training=read_verified(job['training'],job['training_sha256']);manifest=read_verified(training['shard_manifest'],training['shard_manifest_sha256'])
    widths={s['day']:len(s['selected']) for s in manifest['sessions']}
    original=Inference.__call__;observer=Observer();worker=None;completed=[];started=time.time()
    def observed(engine,indices):
        value=original(engine,indices);observer.capture(engine,indices,value);return value
    Inference.__call__=observed
    try:
        worker=Worker(cfg,queue.Queue(maxsize=32),0)
        for day_index,day in enumerate(spec['days']):
            day_start=time.perf_counter();path=out/'days'/f'{day}.json'
            write_json(out/'state.json',dict(status='running',pid=os.getpid(),completed=completed,total=len(spec['days']),current_day=day,updated_epoch=time.time()))
            if path.exists():
                saved=read_verified(path)
                if saved['spec_sha256']!=sha(out/'spec.json'):raise ValueError('Session identity changed')
                receipt=read_verified(saved['receipt'],saved['receipt_sha256'])
            else:
                next_day=spec['days'][day_index+1] if day_index+1<len(spec['days']) else None
                observer=Observer();worker.run(day,next_day,plan['members'],1,token,widths[day])
                rp=out/'replay/generation-0001'/day/'receipt.json';receipt=read_verified(rp)
                saved=dict(spec_sha256=sha(out/'spec.json'),receipt=str(rp),receipt_sha256=sha(rp),endpoints=observer.finish())
            if receipt['split']!='training' or receipt['validation_opened'] or receipt['population_sha256']!=token or receipt['execution']!=job['execution']:
                raise ValueError('Replay boundary mismatch')
            prior=baseline['receipts'][day];prior=read_verified(prior['receipt'],prior['receipt_sha256'])
            key_lookup={k:i for i,k in enumerate(prior.get('program_keys',[]))}
            for record in plan['records']:
                i=record['parent_index'];j=key_lookup[keys[i]] if key_lookup else lookup[record['serial']]
                for metric in ('net_pnl','closed_positions','winning_positions','episode_profits','episode_listings','episode_entry_clocks','episode_exit_clocks','episode_branch'):
                    a=np.asarray(receipt['metrics'][metric][i]);b=np.asarray(prior['metrics'][metric][j])
                    if a.shape!=b.shape or not np.allclose(a,b,rtol=0,atol=1e-7):raise ValueError(f'Parent parity failed: {day} {record["serial"]} {metric}')
            saved.update(parent_parity=len(plan['records']),seconds=time.perf_counter()-day_start)
            if not path.exists():write_json(path,saved)
            completed.append(day)
            write_json(out/'state.json',dict(status='running',pid=os.getpid(),completed=completed,total=len(spec['days']),updated_epoch=time.time(),parent_parity=len(completed),seconds=time.time()-started))
            print(json.dumps(dict(day=day,completed=len(completed),seconds=saved['seconds'],parents=len(plan['records']))),flush=True)
        write_json(out/'state.json',dict(status='complete',pid=os.getpid(),completed=completed,total=len(spec['days']),updated_epoch=time.time(),parent_parity=len(completed),seconds=time.time()-started))
    except BaseException as error:
        write_json(out/'state.json',dict(status='failed',pid=os.getpid(),completed=completed,total=len(spec['days']),updated_epoch=time.time(),error=str(error)));raise
    finally:
        Inference.__call__=original
        if worker:worker.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','run'));p.add_argument('--job',required=True);p.add_argument('--out',required=True)
    p.add_argument('--packet');p.add_argument('--plan');p.add_argument('--shard-index',type=int,default=0);p.add_argument('--shard-count',type=int,default=4);a=p.parse_args()
    if a.mode=='prepare':
        result=prepare(a.packet,a.job,a.out);print(json.dumps({k:result[k] for k in ('directed','controls','source_generation')}))
    else:run(a.plan,a.job,a.out,a.shard_index,a.shard_count)
