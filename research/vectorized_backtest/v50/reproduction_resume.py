"""Receipt-only V50 generation-2 reproduction migration; never runs market replay.

Run export-context under the original immutable deployment, then prepare under
the repaired deployment. The original campaign and its seals are never edited.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('V42_REPRODUCTION_WORKERS','1')
import argparse
import json
import shutil
from pathlib import Path
import numpy as np
from research.vectorized_backtest.v42.distributed_assets import sha
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
from research.vectorized_backtest.v50.identity import digest,source_hash


def read(path):return json.loads(Path(path).read_bytes())


def verify_frozen(state,successor):
    """Compare serialized identity, not Python tuples against JSON lists."""
    frozen={k:state[k] for k in ('population','ids','rng')}
    expected={k:successor[k] for k in frozen}
    if digest(frozen)!=successor['sha256'] or digest(expected)!=successor['sha256']:
        raise ValueError(f"Original CPU reproduction identity mismatch: actual={digest(frozen)}, expected={successor['sha256']}")


def stopped(root):
    import psutil
    owner=read(root/'owner.json')
    if (root/'owner.lock').exists():raise ValueError('Original owner lock still held')
    if psutil.pid_exists(owner['pid']):
        try:
            if abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01:
                raise ValueError('Original optimizer is still alive')
        except psutil.NoSuchProcess:pass
    if not (root/'STOP').exists():raise ValueError('Explicit original stop receipt required')


def seals(root):
    result={}
    for g in (1,2):
        folder=root/'global'/f'generation-{g:04d}';seal=read(folder/'complete.json')
        for name in ('population','training','validation','successor'):
            if sha(folder/(name+'.json'))!=seal[name+'_sha256']:raise ValueError('Original generation seal mismatch')
        validation=read(folder/'validation.json')
        if not validation['display_only'] or validation['successor_sha256_before']!=validation['successor_sha256_after']:
            raise ValueError('Original validation independence mismatch')
        result[str(g)]=sha(folder/'complete.json')
    return result


def training(root,g,job):
    """Read one receipt at a time; keep only fields required by reproduction."""
    from research.vectorized_backtest.v50.full_common import aggregate
    from research.vectorized_backtest.v50.objectives import Objective
    folder=root/'global'/f'generation-{g:04d}'
    saved=read(folder/'training.json');pop=read(folder/'population.json')
    if saved['ids']!=pop['ids'] or saved['population_sha256']!=digest(pop['population']):raise ValueError('Training population mismatch')
    if set(saved['receipts'])!=set(job['training_days']):raise ValueError('Incomplete training receipts')
    ordered=[];parts=[]
    keys=('net_pnl','closed_positions','episode_entry_clocks','episode_exit_clocks',
          'episode_listings','capital_dollar_seconds','turnover')
    for day in job['training_days']:
        ref=saved['receipts'][day];path=Path(ref['receipt'])
        if sha(path)!=ref['receipt_sha256']:raise ValueError('Training receipt hash mismatch')
        receipt=read(path)
        if (receipt['population_sha256']!=saved['population_sha256'] or not receipt['full_session']
                or receipt['split']!='training' or receipt['validation_opened']
                or receipt['execution']!=job['execution']):raise ValueError('Training receipt contract mismatch')
        m=receipt['metrics'];parts.append(aggregate([m],Objective(**job['objective'])))
        ordered.append({k:m[k] for k in keys})
        print(f'verified generation {g} training {day}',flush=True)
    # Independently reconcile recorded financial totals and eligibility.
    for key,expected in saved['panel'].items():
        arrays=np.asarray([p[key] for p in parts])
        actual=(arrays.all(axis=0) if key=='eligible' else arrays.max(axis=0)
                if key=='maximum_position_risk' else arrays.sum(axis=0))
        if not np.allclose(actual,expected,rtol=1e-10,atol=1e-7):raise ValueError('Saved global panel differs from session receipts: '+key)
    return saved,pop,ordered


def reproduce(state,saved,ordered,job,generation,*,probe=None,repaired=False):
    from research.vectorized_backtest.v50.genome import Individual
    from research.vectorized_backtest.v50.local_runner import CompactPedigree
    from research.vectorized_backtest.v50.species import Species
    from research.vectorized_backtest.v50.lineage import next_population,Selection
    from research.vectorized_backtest.v50.validation_cache import validation_scope
    members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids'])
    pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);species=Species(state['species'])
    rng=np.random.default_rng();rng.bit_generator.state=state['rng']
    panel={k:np.asarray(v) for k,v in saved['panel'].items()}
    kwargs=dict(probe=probe) if repaired else {}
    with validation_scope():
        members,ids,telemetry=next_population(rng,members,ids,pedigree,[],generation,Selection(**job['selection']),
            session_values=panel['score'][None],session_eligible=panel['eligible'][None],
            global_values=panel['score'],global_eligible=panel['eligible'],target_size=job['population'],
            species=species,ordered=ordered,case_values=None,case_ids=None,**kwargs)
    pedigree.retain(ids)
    return dict(population=[m.payload() for m in members],ids=ids.tolist(),rng=rng.bit_generator.state,
        pedigree=list(pedigree.records.values()),next_serial=pedigree.next_serial,species=species.state),telemetry


def export_context(root,output):
    """Reconstruct generation-2 ancestry/RNG using original CPU reproduction only."""
    from research.vectorized_backtest.v50.full_common import check_job
    stopped(root);job=read(root/'full-job.json');check_job(job);links=seals(root)
    binding=dict(job_sha256=sha(root/'full-job.json'),seals=links,source_sha256=source_hash())
    if output.exists():
        prior=read(output)
        if prior['binding']!=binding or digest(prior['state'])!=prior['state_sha256']:raise ValueError('Existing context changed')
        return
    state=read(root/'global/initial-pool.json');saved,pop,ordered=training(root,1,job)
    if state['population']!=pop['population'] or state['ids']!=pop['ids']:raise ValueError('Founder identity mismatch')
    state,_=reproduce(state,saved,ordered,job,2)
    successor=read(root/'global/generation-0001/successor.json')
    verify_frozen(state,successor)
    pop2=read(root/'global/generation-0002/population.json')
    if digest(pop2['population'])!=digest(state['population']) or pop2['ids']!=state['ids']:raise ValueError('Generation-2 population mismatch')
    output.parent.mkdir(parents=True,exist_ok=True)
    write_json(output,dict(binding=binding,state=state,state_sha256=digest(state),market_replays=0))


def unchanged_replay(old_code,new_code):
    allowed={'lineage.py','initial_population.py','full_controller.py','full_common.py','reproduction_resume.py'}
    for version in ('v6','v7','v27','v42','v50'):
        relative=Path('research/vectorized_backtest')/version
        before={p.relative_to(old_code/relative):p for p in (old_code/relative).rglob('*.py')}
        after={p.relative_to(new_code/relative):p for p in (new_code/relative).rglob('*.py')}
        for name in before.keys()|after.keys():
            if version=='v50' and str(name) in allowed:continue
            if name not in before or name not in after or before[name].read_bytes().replace(b'\r\n',b'\n')!=after[name].read_bytes().replace(b'\r\n',b'\n'):
                raise ValueError('Replay/dependency source changed: '+str(relative/name))


def prepare(root,output,context,code):
    from research.vectorized_backtest.v50.full_common import check_job
    from research.vectorized_backtest.v42.calibration import load_probe
    stopped(root);old=read(root/'full-job.json');links=seals(root);export=read(context)
    if export['binding']!=dict(job_sha256=sha(root/'full-job.json'),seals=links,source_sha256=old['source_sha256']) or digest(export['state'])!=export['state_sha256']:
        raise ValueError('Original reproduction context binding mismatch')
    if output.exists():raise ValueError('Continuation directory already exists; inspect rather than overwrite')
    unchanged_replay(Path(old['code']),code)
    saved,pop,ordered=training(root,2,old)
    if any(export['state'][k]!=pop[k] for k in ('population','ids')):raise ValueError('Context population mismatch')
    probe=load_probe(old['initialization_probe'],old['training'])
    state,telemetry=reproduce(export['state'],saved,ordered,old,3,probe=probe,repaired=True)
    state.update(completed=2,seals=links)
    job=dict(old,campaign=str(output),code=str(code),source_sha256=source_hash(),
             commit=read(code/'deployment.json')['commit'])
    job['machines']={k:dict(v,code=str(code)) for k,v in old['machines'].items()}
    output.mkdir(parents=True);(output/'global').mkdir()
    for g in (1,2):shutil.copytree(root/'global'/f'generation-{g:04d}',output/'global'/f'generation-{g:04d}')
    if (root/'data-ready').exists():shutil.copytree(root/'data-ready',output/'data-ready')
    transition=dict(version='v50-generation2-reproduction-repair-v1',original_campaign=str(root),
        original_job_sha256=sha(root/'full-job.json'),old_source_sha256=old['source_sha256'],new_source_sha256=job['source_sha256'],
        original_seals=links,context=str(context),context_sha256=sha(context),
        generation2_training_sha256=sha(root/'global/generation-0002/training.json'),
        successor_sha256=digest({k:state[k] for k in ('population','ids','rng')}),
        checkpoint_state_sha256=digest(state),telemetry=telemetry,market_replays=0,
        resume_after_generation=2,first_new_replay_generation=3,validation_feedback=False,
        training_sha256=job['training_sha256'],validation_sha256=job['validation_sha256'],
        objective=job['objective'],execution=job['execution'])
    write_json(output/'reproduction-transition.json',transition)
    job['reproduction_transition']=str(output/'reproduction-transition.json')
    job['reproduction_transition_sha256']=sha(output/'reproduction-transition.json')
    contract=read(root/'global/checkpoint.json')['contract']
    contract.update(source_sha256=job['source_sha256'],reproduction_transition_sha256=job['reproduction_transition_sha256'])
    write_checkpoint(output/'global/checkpoint.json',contract,state)
    write_json(output/'full-job.json',job);check_job(job)
    write_json(output/'manifest.json',dict(version=job['version'],continuation_of=str(root),commit=job['commit'],
        source_sha256=job['source_sha256'],resume_after_generation=2,market_replays_during_migration=0))
    print(json.dumps(telemetry),flush=True)


def validate_transition(job):
    if 'reproduction_transition' not in job:return
    path=Path(job['reproduction_transition'])
    if sha(path)!=job['reproduction_transition_sha256']:raise ValueError('Reproduction transition changed')
    transition=read(path)
    if (transition['new_source_sha256']!=job['source_sha256'] or transition['market_replays']!=0
            or transition['resume_after_generation']!=2 or transition['validation_feedback']):raise ValueError('Invalid reproduction transition')
    for key in ('training_sha256','validation_sha256','objective','execution'):
        if transition[key]!=job[key]:raise ValueError('Transition financial/input contract changed')
    cp=read(Path(job['campaign'])/'global/checkpoint.json')
    if cp['state']['completed']<2 or cp['contract'].get('reproduction_transition_sha256')!=sha(path):raise ValueError('Checkpoint lost transition binding')
    if cp['state']['completed']==2 and cp['state_sha256']!=transition['checkpoint_state_sha256']:raise ValueError('Migrated successor changed')
    if any(cp['state']['seals'].get(k)!=v for k,v in transition['original_seals'].items()):raise ValueError('Original seals changed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['export-context','prepare'])
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--context',type=Path);parser.add_argument('--code',type=Path)
    args=parser.parse_args()
    if args.mode=='export-context':export_context(args.source,args.output)
    else:
        if args.context is None or args.code is None:parser.error('prepare requires --context and --code')
        prepare(args.source,args.output,args.context,args.code)


if __name__=='__main__':main()
