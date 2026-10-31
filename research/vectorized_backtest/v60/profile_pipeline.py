"""One bounded real generation: two training days, one validation day, resume.

Uses the production evolution/publication paths and 32 paired diagnostic mutants.
Saved V60 genomes are diagnostic inputs only; no old financial results are reused.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,copy,json,threading,time
from pathlib import Path
import numpy as np
from .global_runner import Global
from .global_contract import check
from .distributed_assets import sha
from .identity import digest
from .publication import write_json


class BoundaryReached(Exception):pass


class Qualification(Global):
    def evaluate(self,role,days,members,generation):
        if generation>=2:raise BoundaryReached('Stop before generation 2 GPU dispatch')
        return super().evaluate(role,days,members,generation)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--founders',type=Path,required=True)
    parser.add_argument('--founders-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();job=json.loads(args.job.read_bytes());check(job)
    if sha(args.founders)!=args.founders_sha256:raise ValueError('Diagnostic genomes changed')
    if args.output.exists():raise ValueError('Preserve qualification artifacts; use a new directory')
    args.output.mkdir(parents=True)
    train=['2026-08-13','2026-08-18'];valid=job['validation_days'][:1]
    if not set(train).issubset(job['training_days']):raise ValueError('Qualification outside training scope')
    local=copy.deepcopy(job);local.update(campaign=str(args.output),training_days=train,validation_days=valid,qualification_only=True)
    write_json(args.output/'job.json',local)
    state=json.loads(args.founders.read_bytes());indices=list(range(512))+list(range(3072,3584))
    state.update(population=[state['population'][i] for i in indices],ids=[state['ids'][i] for i in indices],completed=0,seals={})
    from .genome import Individual
    from .local_runner import CompactPedigree
    from .species import Species
    from .calibration import load_probe
    from .objectives import Objective
    from .full_controller import checkpoint_contract
    from .generation_flow import evolve
    members=[Individual.restore(p) for p in state['population']];ids=np.asarray(state['ids'])
    pedigree=CompactPedigree(3,state['pedigree'],state['next_serial'])
    rng=np.random.default_rng();rng.bit_generator.state=state['rng']
    c=Qualification(local);thread=threading.Thread(target=c.pump,daemon=True);thread.start()
    root=c.root/'global';root.mkdir(exist_ok=True);started=time.perf_counter()
    try:
        try:evolve(c,root,train,valid,checkpoint_contract(local,train,valid),state,members,ids,pedigree,rng,Species(),load_probe(job['initialization_probe'],job['training']),Objective(**job['objective']))
        except BoundaryReached:pass
        checkpoint=root/'checkpoint.json';before=sha(checkpoint)
        saved=json.loads(checkpoint.read_bytes())
        if saved['state']['completed']!=1 or digest(saved['state'])!=saved['state_sha256']:raise ValueError('Qualification checkpoint invalid')
        # Exercise the actual resume reader and seal checks. Stop before any next replay.
        try:c.global_run({},train,valid)
        except BoundaryReached:pass
        else:raise ValueError('Resume did not reach the expected saved generation')
        if sha(checkpoint)!=before:raise ValueError('Read-only resume changed the sealed checkpoint')
        folder=root/'generation-0001'
        timings=json.loads((folder/'generation-timings.json').read_bytes())
        transition=json.loads((folder/'transition-timings.json').read_bytes())
        rows=[]
        for role,days in (('training',train),('validation',valid)):
            for day in days:
                row=json.loads((c.root/'replays'/role/'generation-0001'/day/'timings.json').read_bytes())
                rows.append(dict(role=role,day=day,batches=row))
        report=dict(qualification_only=True,training_days=train,validation_days=valid,
            main_candidates=1024,paired_mutants=32,generation=timings,transition=transition,sessions=rows,
            resume_verified=True,checkpoint_sha256=before,total_qualification_seconds=time.perf_counter()-started)
        write_json(args.output/'pipeline-profile.json',report)
        c.status='complete';c.stage='Bounded qualification complete; campaign not launched';c.publish(completed_generations=1,active_sessions=[])
        print(json.dumps(report),flush=True)
    finally:
        c.halt.set();thread.join(5);c.close_lanes()


if __name__=='__main__':main()
