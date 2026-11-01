"""One complete production generation from pinned saved state, in an isolated runtime."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time,threading,copy
from pathlib import Path
import numpy as np
from .global_contract import check
from .global_runner import Global
from .full_controller import checkpoint_contract
from .publication import write_json
from .identity import digest
from .distributed_assets import sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--checkpoint-sha256',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();job=json.loads(a.job.read_bytes());check(job)
    if sha(a.checkpoint)!=a.checkpoint_sha256:raise ValueError('Pinned qualification checkpoint changed')
    saved=json.loads(a.checkpoint.read_bytes());state=saved['state']
    if digest(state)!=saved['state_sha256']:raise ValueError('Qualification state identity changed')
    expected=checkpoint_contract(job,job['training_days'],job['validation_days'])
    if dict(saved['contract'],source_sha256=expected['source_sha256'])!=expected:
        raise ValueError('Qualification may change implementation only')
    if state['completed']!=0:raise ValueError('This qualification starts from sealed initial replay')
    from .runtime import require_runtime
    output=require_runtime(a.output)
    if output.exists() and any(output.iterdir()):raise ValueError('Qualification output must be empty')
    output.mkdir(parents=True,exist_ok=True)
    local=dict(job,campaign=str(output));c=Global(local)
    from .genome import Individual
    from .local_runner import CompactPedigree
    from .species import Species
    from .objectives import Objective
    from .calibration import load_probe
    from .generation_flow import evolve
    members=[Individual.restore(v) for v in state['population']]
    ids=np.asarray(state['ids']);rng=np.random.default_rng();rng.bit_generator.state=state['rng']
    pedigree=CompactPedigree(3,state['pedigree'],state['next_serial'])
    probe=load_probe(job['initialization_probe'],job['training'])
    write_json(output/'qualification-input.json',dict(job=str(a.job),checkpoint=str(a.checkpoint),checkpoint_sha256=a.checkpoint_sha256,
        source=job['source_sha256'],population=len(members),training=job['training_days'],validation=job['validation_days'],production_campaign_modified=False))
    root=c.root/'global';root.mkdir(exist_ok=True)
    thread=threading.Thread(target=c.pump,daemon=True);thread.start();started=time.perf_counter()
    try:
        evolve(c,root,job['training_days'],job['validation_days'],expected,copy.deepcopy(state),members,ids,pedigree,rng,Species(state['species']),probe,Objective(**job['objective']),stop_after_generation=1)
        report=json.loads((root/'generation-0001/generation-timings.json').read_bytes())
        report['qualification_wall_seconds']=time.perf_counter()-started
        report['training_sessions']=len(job['training_days']);report['validation_sessions']=len(job['validation_days'])
        write_json(output/'profile.json',report);print(json.dumps(report),flush=True)
    finally:
        c.halt.set();thread.join(5);c.close_lanes()

if __name__=='__main__':main()
