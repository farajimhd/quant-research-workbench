"""Start V51 reproduction from V50's sealed 6,144-founder financial receipts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
import numpy as np
from .identity import digest,source_hash
from .objectives import Objective
from .full_common import ranking
from .reproduction_resume import read,stopped,training,reproduce
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint


def rescore(panel):
    """Preserve financial measures and eligibility; replace only the score."""
    result={k:list(v) for k,v in panel.items()}
    if len({len(v) for v in result.values()})!=1:raise ValueError('Unaligned saved financial panel')
    for key in ('net_pnl','gross_losses','drawdown'):
        if not np.isfinite(result[key]).all():raise ValueError('Nonfinite saved financial measure')
    if any(x<0 for key in ('gross_losses','drawdown') for x in result[key]):raise ValueError('Negative risk measure')
    objective=Objective().validate()
    result['score']=(np.asarray(result['net_pnl'])-objective.loss_weight*np.asarray(result['gross_losses'])
                     -objective.drawdown_weight*np.asarray(result['drawdown'])).tolist()
    return result


def verify_replay_sources(old_code,new_code,expected):
    """Compare financial/neural engines after the mechanical version rename."""
    from research.vectorized_backtest.v42.run_search import source_hash as base_hash
    h=hashlib.sha256(base_hash().encode())
    for version in ('v6','v7','v27','v42'):
        relative=Path('research/vectorized_backtest')/version
        before={p.relative_to(old_code/relative):p for p in (old_code/relative).rglob('*.py')}
        after={p.relative_to(new_code/relative):p for p in (new_code/relative).rglob('*.py')}
        if before.keys()!=after.keys():raise ValueError('Replay dependency inventory changed')
        for name in before:
            if before[name].read_bytes().replace(b'\r\n',b'\n')!=after[name].read_bytes().replace(b'\r\n',b'\n'):
                raise ValueError('Replay dependency changed: '+str(relative/name))
    old=old_code/'research/vectorized_backtest/v50';new=new_code/'research/vectorized_backtest/v51'
    allowed={'objectives.py','full_common.py','full_controller.py','full_launch.py','full_seed.py',
             'initial_population.py','initial_reuse.py','lineage.py','progress.py','reproduction_resume.py'}
    checked=[]
    for path in sorted(old.glob('*.py')):
        raw=path.read_bytes().replace(b'\r\n',b'\n');h.update(path.name.encode());h.update(raw)
        if path.name in allowed:continue
        current=(new/path.name).read_text().replace('v51','v50').replace('V51','V50')
        if current!=raw.decode():raise ValueError('Financial/neural replay source changed: '+path.name)
        checked.append(path.name)
    if h.hexdigest()!=expected:raise ValueError('Original V50 deployment source hash mismatch')
    return checked


def verify_generation(root,job):
    folder=root/'global/generation-0001';seal=read(folder/'complete.json')
    if seal['source_sha256']!=job['source_sha256']:raise ValueError('Founder seal source mismatch')
    for name in ('population','training','validation','successor'):
        if sha(folder/(name+'.json'))!=seal[name+'_sha256']:raise ValueError('Founder generation seal mismatch')
    initial=read(root/'global/initial-pool.json');pop=read(folder/'population.json')
    if (len(initial['population'])!=6144 or len(set(initial['ids']))!=6144
            or initial['ids']!=pop['ids'] or digest(initial['population'])!=digest(pop['population'])
            or initial['source_sha256']!=job['source_sha256']):raise ValueError('Original 6x founder identity mismatch')
    return initial,pop


def prepare(source,output,code,stopped_v50):
    from .full_common import check_job
    from research.vectorized_backtest.v42.calibration import load_probe
    started=time.time();stopped(source);stopped(stopped_v50)
    if output.exists():raise ValueError('V51 output exists; inspect instead of overwriting')
    old=read(source/'full-job.json')
    if old['version']!='v50-full-v1' or (old['population'],old['initial_multiplier'])!=(1024,6):raise ValueError('Original V50 full founders required')
    for key in ('training','validation','initialization_probe'):
        if sha(Path(old[key]))!=old[key+'_sha256']:raise ValueError('Original input contract changed: '+key)
    checked=verify_replay_sources(Path(old['code']),code,old['source_sha256'])
    initial,pop=verify_generation(source,old)
    saved,verified_pop,ordered=training(source,1,old)
    if digest(pop)!=digest(verified_pop):raise ValueError('Founder population changed during audit')
    repriced=dict(saved,panel=rescore(saved['panel']))
    repriced['ranking']=ranking({k:np.asarray(v) for k,v in repriced['panel'].items()},np.asarray(pop['ids']))
    job=dict(old,version='v51-full-v1',campaign=str(output),code=str(code),source_sha256=source_hash(),
        source_job=str(source/'full-job.json'),source_job_sha256=sha(source/'full-job.json'),
        objective=dict(drawdown_weight=.05,loss_weight=.05),commit=read(code/'deployment.json')['commit'])
    job['machines']={k:dict(v,code=str(code)) for k,v in old['machines'].items()}
    probe=load_probe(old['initialization_probe'],old['training'])
    # Initial RNG/pedigree/species, never the V50 generation-1 successor state.
    state,telemetry=reproduce(initial,repriced,ordered,job,2,probe=probe,repaired=True)
    frozen={k:state[k] for k in ('population','ids','rng')};frozen_hash=digest(frozen)
    # Only now read validation values; they cannot select parents or consume RNG.
    validation=read(source/'global/generation-0001/validation.json')
    if (not validation['display_only'] or not validation['opened']
            or validation['successor_sha256_before']!=validation['successor_sha256_after']
            or validation['ids']!=pop['ids'] or validation['population_sha256']!=digest(pop['population'])
            or set(validation['receipts'])!=set(old['validation_days'])):raise ValueError('Original validation contract mismatch')
    for day,ref in validation['receipts'].items():
        if sha(Path(ref['receipt']))!=ref['receipt_sha256']:raise ValueError('Original validation receipt changed')
        print('verified original validation receipt '+day,flush=True)
    validation=dict(validation,panel=rescore(validation['panel']),successor_sha256_before=frozen_hash,
        successor_sha256_after=frozen_hash,reused_from=str(source/'global/generation-0001/validation.json'))
    validation['ranking']=ranking({k:np.asarray(v) for k,v in validation['panel'].items()},np.asarray(pop['ids']))
    if digest({k:state[k] for k in ('population','ids','rng')})!=frozen_hash:raise ValueError('Validation changed reproduction')
    folder=output/'global/generation-0001';folder.mkdir(parents=True)
    shutil.copy2(source/'global/initial-pool.json',output/'global/initial-pool.json')
    write_json(folder/'population.json',pop)
    write_json(folder/'training.json',dict(repriced,reused_from=str(source/'global/generation-0001/training.json')))
    write_json(folder/'successor.json',dict(frozen,sha256=frozen_hash,training_only=True))
    write_json(folder/'validation.json',validation)
    write_json(folder/'reproduction.json',dict(generation=1,successor_sha256=frozen_hash,training_only=True,telemetry=telemetry))
    write_json(folder/'complete.json',dict(source_sha256=job['source_sha256'],validation_independent=True,
        original_complete_sha256=sha(source/'global/generation-0001/complete.json'),market_replays=0,
        **{k+'_sha256':sha(folder/(k+'.json')) for k in ('training','validation','population','successor','reproduction')}))
    state.update(completed=1,seals={'1':sha(folder/'complete.json')})
    origin=dict(version='v51-v50-founder-rescore-v1',original_campaign=str(source),stopped_v50=str(stopped_v50),
        original_source_sha256=old['source_sha256'],original_job_sha256=sha(source/'full-job.json'),
        original_complete_sha256=sha(source/'global/generation-0001/complete.json'),
        original_initial_pool_sha256=sha(source/'global/initial-pool.json'),
        original_population_sha256=digest(pop['population']),original_training_sha256=sha(source/'global/generation-0001/training.json'),
        new_source_sha256=job['source_sha256'],objective=job['objective'],execution=job['execution'],
        training_sha256=job['training_sha256'],validation_sha256=job['validation_sha256'],
        generation1_complete_sha256=sha(folder/'complete.json'),checkpoint_state_sha256=digest(state),
        population=6144,initial_market_replays=0,first_new_replay_generation=2,validation_feedback=False,
        checked_replay_modules=checked,successor_sha256=frozen_hash,telemetry=telemetry,preparation_seconds=time.time()-started)
    write_json(output/'initial-reuse.json',origin)
    job['initial_reuse_sha256']=sha(output/'initial-reuse.json')
    contract=dict(source_sha256=job['source_sha256'],objective=job['objective'],training_sha256=job['training_sha256'],
        validation_sha256=job['validation_sha256'],generations=job['phase2_generations'],population=job['population'],
        initial_multiplier=job['initial_multiplier'],training_days=job['training_days'],validation_days=job['validation_days'],
        validation='display-only; successor and RNG frozen before opening',initial_reuse_sha256=job['initial_reuse_sha256'])
    write_checkpoint(output/'global/checkpoint.json',contract,state)
    if (source/'data-ready').exists():shutil.copytree(source/'data-ready',output/'data-ready')
    write_json(output/'full-job.json',job);check_job(job)
    write_json(output/'manifest.json',dict(version=job['version'],commit=job['commit'],source_sha256=job['source_sha256'],
        objective=job['objective'],original_campaign=str(source),initial_reuse_sha256=job['initial_reuse_sha256']))
    print(json.dumps(dict(elites=telemetry['elites'],crossovers=telemetry['crossovers'],parent_mutations=telemetry['parent_mutations'],
        fresh=telemetry['fresh_candidates'],positive_scores=sum(e and v>0 for e,v in zip(repriced['panel']['eligible'],repriced['panel']['score'])),initial_market_replays=0)),flush=True)


def validate_import(job):
    if job['version']!='v51-full-v1':return
    root=Path(job['campaign']);path=root/'initial-reuse.json'
    if not job.get('initial_reuse_sha256') or sha(path)!=job['initial_reuse_sha256']:raise ValueError('Sealed V50 founder reuse is required')
    origin=read(path)
    if (origin['new_source_sha256']!=job['source_sha256'] or origin['population']!=6144
            or origin['initial_market_replays']!=0 or origin['first_new_replay_generation']!=2 or origin['validation_feedback']):
        raise ValueError('Invalid V50 founder reuse contract')
    for key in ('objective','execution','training_sha256','validation_sha256'):
        if origin[key]!=job[key]:raise ValueError('Founder reuse contract changed: '+key)
    cp=read(root/'global/checkpoint.json')
    if (digest(cp['state'])!=cp['state_sha256'] or cp['state']['completed']<1
            or cp['state']['seals'].get('1')!=origin['generation1_complete_sha256']
            or cp['contract'].get('initial_reuse_sha256')!=job['initial_reuse_sha256']):raise ValueError('Founder checkpoint binding changed')
    if cp['state']['completed']==1 and cp['state_sha256']!=origin['checkpoint_state_sha256']:raise ValueError('Initial successor changed')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Original V50 full campaign with 6144-founder receipts')
    p.add_argument('--stopped-v50',type=Path,required=True,help='Most recent V50 continuation, now stopped')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--code',type=Path,required=True)
    a=p.parse_args();prepare(a.source,a.output,a.code,a.stopped_v50)


if __name__=='__main__':main()
