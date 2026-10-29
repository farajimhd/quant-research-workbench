"""Rescore sealed V57 founder trades and reproduce V58 generation one without GPU replay."""
import argparse,copy,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from .identity import digest,source_hash
from .robust_score import VERSION,COEFFICIENT
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json

ENGINE_FILES=('genome.py','identity_cache.py','config.py','data.py','evaluator.py','financial.py',
    'network.py','network_cuda.py','network_pack.py','replay_graph.py','protected_transition.py',
    'entry_sharing.py','operators.py','mask_genome.py')


def engine_hashes(code,version):
    import hashlib
    root=Path(code)/'research/vectorized_backtest'/version
    return {name:hashlib.sha256((root/name).read_text(encoding='utf-8-sig').replace('v58','v57').replace('V58','V57').encode()).hexdigest() for name in ENGINE_FILES}


def verified(path,checksum):
    if sha(path)!=checksum:raise ValueError('Founder source changed: '+str(path))
    return json.loads(Path(path).read_bytes())


def verify_binding(job):
    binding=job['founder_restart']
    manifest=verified(binding['manifest'],binding['sha256'])
    old=verified(manifest['job'],manifest['job_sha256'])
    allowed={'version','score_version','concentration_coefficient','source_sha256','code','commit','campaign','transport',
             'machines','resume_transition','result_cache_transition','founder_restart'}
    if any(job.get(k)!=v for k,v in old.items() if k not in allowed):raise ValueError('Founder restart changed campaign semantics')
    machine=copy.deepcopy(job['machines']);machine['workstation']['code']=old['machines']['workstation']['code']
    if machine!=old['machines']:raise ValueError('Founder restart changed hardware or session ownership')
    if Path(job['campaign']).resolve()==Path(old['campaign']).resolve():raise ValueError('V58 requires a separate runtime')
    if engine_hashes(job['code'],'v58')!=manifest['engine_hashes']:raise ValueError('Founder replay numerical engine changed')
    return manifest


def prepare(predecessor,root,code):
    from .runtime_path import require_runtime
    from .global_contract import check
    root=require_runtime(root);code=Path(code);predecessor=Path(predecessor)
    if (root/'global-job.json').exists():raise ValueError('Existing V58 job must be resumed, not overwritten')
    old=json.loads(predecessor.read_bytes());origin=Path(old['campaign'])/'phase-2/global'
    seal=origin/'generation-0001/complete.json';cert=json.loads(seal.read_bytes())
    replay_job=old
    while replay_job['source_sha256']!=cert['source_sha256']:
        transition=replay_job.get('resume_transition',{})
        replay_job=verified(transition['predecessor_job'],transition['predecessor_job_sha256'])
    engine=engine_hashes(replay_job['code'],'v57')
    if engine_hashes(code,'v58')!=engine:raise ValueError('Cannot reuse founders after a numerical engine change')
    training=origin/'generation-0001/training.json';verified(training,cert['training_sha256'])
    population=origin/'generation-0001/population.json'
    if sha(population)!=cert['population_sha256']:raise ValueError('Founder population seal mismatch')
    initial=origin/'initial-pool.json'
    manifest=dict(job=str(predecessor),job_sha256=sha(predecessor),seal=str(seal),seal_sha256=sha(seal),
        training=str(training),training_sha256=sha(training),population=str(population),population_sha256=sha(population),
        initial=str(initial),initial_sha256=sha(initial),validation_used=False,founder_count=6144,engine_hashes=engine)
    path=root/'founder-source.json';write_json(path,manifest)
    job=copy.deepcopy(old)
    for name in ('resume_transition','result_cache_transition'):job.pop(name,None)
    job.update(version='v58-global-v1',score_version=VERSION,concentration_coefficient=COEFFICIENT,
        source_sha256=source_hash(),code=str(code),commit=json.loads((code/'deployment.json').read_bytes())['commit'],
        campaign=str(root),transport=str(root/'asset-bus'),founder_restart=dict(manifest=str(path),sha256=sha(path)))
    job['machines']['workstation']['code']=str(code)
    check(job);write_json(root/'global-job.json',job)
    # Reuse only readiness metadata; ensure_day still validates size/mtime and inventory.
    import shutil
    if (Path(old['campaign'])/'data-ready').exists():shutil.copytree(Path(old['campaign'])/'data-ready',root/'data-ready',dirs_exist_ok=True)
    return job


def bootstrap(job,probe,progress=None):
    from .controller_metrics import compact
    from .full_common import aggregate,serializable,ranking
    from .genome import Individual
    from .local_runner import CompactPedigree
    from .lineage import next_population,Selection
    from .species import Species
    from .ticker_selection import ticker_scores
    from .validation_cache import validation_scope
    from .reproduction_prefetch import FreshPrefetch
    from .allocation import budgets
    started=time.perf_counter();manifest=verify_binding(job)
    folder=Path(job['campaign'])/'founder-bootstrap';folder.mkdir(exist_ok=True)
    state_path=folder/'state.json';done=folder/'complete.json'
    if done.exists():
        cert=json.loads(done.read_bytes())
        if cert['job_sha256']!=digest(job):raise ValueError('Bootstrap job changed')
        return verified(state_path,cert['state_sha256'])
    seal=verified(manifest['seal'],manifest['seal_sha256'])
    if seal['training_sha256']!=manifest['training_sha256'] or seal['population_sha256']!=manifest['population_sha256']:raise ValueError('Founder seal binding mismatch')
    training=verified(manifest['training'],manifest['training_sha256'])
    initial=verified(manifest['initial'],manifest['initial_sha256'])
    population=verified(manifest['population'],manifest['population_sha256'])
    if population['population']!=initial['population'] or population['ids']!=initial['ids'] or training['ids']!=initial['ids']:raise ValueError('Founder ID/genome mismatch')
    token=digest(initial['population']);del population
    if token!=training['population_sha256'] or len(initial['ids'])!=6144:raise ValueError('6144 sealed founders required')
    if set(training['receipts'])!=set(job['training_days']):raise ValueError('Founder training scope mismatch')
    market=json.loads(Path(job['training']).read_bytes());ordered=[]
    for index,day in enumerate(job['training_days']):
        ref=training['receipts'][day];r=verified(ref['receipt'],ref['receipt_sha256'])
        from .entry_sharing import VERSION as ENTRY_VERSION
        if r.get('result_identity',{}).get('entry_sharing')!=ENTRY_VERSION:raise ValueError('Founder entry-sharing contract differs')
        if (r['day']!=day or r['split']!='training' or r['validation_opened'] or not r['full_session']
            or r['population_sha256']!=token or r['execution']!=job['execution']
            or len(r['case_owners'])!=6144 or any(v is not None for v in r['case_owners'])):raise ValueError('Founder replay contract mismatch')
        m=compact(r['metrics'])
        if any(len(v)!=6144 for v in m.values()):raise ValueError('Founder metric alignment changed')
        input_path=Path(market['inputs'])/day/'complete.json'
        receipt=verified(input_path,r['identity']['input_sha256'])
        item=SimpleNamespace(receipt=receipt,listing_ids=np.asarray(r['listing_ids']))
        # Never import V57 ticker fitness: derive specialist scores from raw trades.
        m['ticker_score']=ticker_scores(m,item);ordered.append(m);del r
        write_json(folder/'progress.json',dict(stage='Rescore saved founder receipts',completed=index+1,total=24,gpu_replays=0))
        if progress:progress(index+1,24)
    panel=aggregate(ordered)
    write_json(folder/'training.json',dict(ids=initial['ids'],panel=serializable(panel),ranking=ranking(panel,np.asarray(initial['ids'])),receipts=training['receipts'],score_version=VERSION,coefficient=COEFFICIENT))
    members=[Individual.restore(p) for p in initial['population']];ids=np.asarray(initial['ids'])
    pedigree=CompactPedigree(3,initial['pedigree'],initial['next_serial']);rng=np.random.default_rng();rng.bit_generator.state=initial['rng'];species=Species()
    fresh_count=budgets(1024)['fresh'];fresh_count-=int(fresh_count*job['selection'].get('profit_fresh_fraction',0.))
    prefetch=FreshPrefetch(rng,1,fresh_count,probe)
    write_json(folder/'progress.json',dict(stage='Reproduce first 1024 under V58 score',completed=24,total=24,gpu_replays=0))
    try:
        with validation_scope(capacity=32768):
            successor,newids,telemetry=next_population(rng,members,ids,pedigree,[],1,Selection(**job['selection']),
                session_values=panel['score'][None],session_eligible=panel['eligible'][None],global_values=panel['score'],
                global_eligible=panel['eligible'],target_size=1024,species=species,ordered=ordered,probe=probe,fresh_prefetch=prefetch)
    finally:prefetch.close()
    pedigree.retain(newids)
    state=dict(population=[m.payload() for m in successor],ids=newids.tolist(),pedigree=list(pedigree.records.values()),
        next_serial=pedigree.next_serial,rng=rng.bit_generator.state,validation_opened=False,source_sha256=job['source_sha256'])
    write_json(folder/'reproduction.json',telemetry);write_json(state_path,state)
    write_json(done,dict(job_sha256=digest(job),state_sha256=sha(state_path),source_binding=job['founder_restart'],
        founders_rescored=6144,population=1024,gpu_replays=0,validation_used=False,seconds=time.perf_counter()-started))
    return state


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--predecessor',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--code',type=Path,required=True)
    a=p.parse_args();job=prepare(a.predecessor,a.root,a.code);print(Path(job['campaign'])/'global-job.json')

if __name__=='__main__':main()
