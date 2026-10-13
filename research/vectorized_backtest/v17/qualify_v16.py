"""Exact V16 accounting and matched 1024-member full-session speed gate."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,sys,subprocess,importlib
from pathlib import Path
from time import perf_counter
import numpy as np


def worker(a):
    sys.path.insert(0,str(a.code));os.chdir(a.code)
    prefix='research.vectorized_backtest.'+a.engine
    search=importlib.import_module(prefix+'.run_search');genome=importlib.import_module(prefix+'.genome')
    lanes=importlib.import_module(prefix+'.session_lanes');terminal=importlib.import_module(prefix+'.terminal')
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
    import torch
    torch.set_num_threads(1);data=search.bind_dataset(a.campaign/'training.json','training')
    frozen=json.loads(a.population.read_text());members=[genome.Individual.restore(v) for v in frozen['population']]
    token=search.digest([v.payload() for v in members]);assert token==frozen['population_sha256'] and len(members)==1024
    days=['2026-08-13','2026-08-27'];assert not set(days)-set(data['days'])
    out=require_runtime(a.output)
    contract=dict(population=1024,generations=2,batch_size=512,session_workers=2,input_receipts={d:data['receipts'][d] for d in days},
        execution=dict(entry_dollars=1000.,add_dollars=200.,cost_bps=0.,fee_per_share=.005,minimum_order_fee=1),session_execution='independent')
    config=dict(inputs=data['inputs'],history=data['history'],feature_cache=data.get('feature_cache'),indicator_root=data['indicator_root'],output=str(out),
        device='cuda',backend='compile',batch_size=512,execution=contract['execution'],maximum_data_gib=4.,maximum_gate_gib=8.,
        source_sha256=search.source_hash(),split='training',episode_capacity=65536,plan_limit=2,reuse_results=False,capture_gib=4.)
    times=[];hashes=[]
    with terminal.CampaignProgress(out,contract) as panel,lanes.SessionLanes(config,2,panel) as runner:
        for repeat in (1,2):
            panel.begin_generation(repeat);began=perf_counter()
            timings=runner.generation(days,members,repeat,token);elapsed=perf_counter()-began;times.append(elapsed)
            hashes.append({d:file_hash(out/f'generation-{repeat:04d}'/d/'receipt.json') for d in days})
            write_json(out/'progress.json',dict(repeat=repeat,seconds=elapsed,timings=timings))
        panel.update(status='complete',stage='Matched full-session profile complete',completed_generations=2)
    write_json(out/'profile.json',dict(engine=a.engine,source_sha256=search.source_hash(),population_sha256=token,days=days,
        wall_seconds=times,session_receipts=hashes,result_reuse=False))


def qualify(a):
    from .run_search import source_hash,digest,bind_dataset
    from .initial_population import initialize
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
    import pytest,torch
    stop=json.loads(a.stop_receipt.read_text());assert stop['workers_stopped'] and stop['lock_released']
    assert stop['campaign']==str(a.campaign) and file_hash(a.campaign/'checkpoint.json')==stop['checkpoint_sha256']
    out=require_runtime(a.output);code=Path(__file__).resolve().parents[3];torch.set_num_threads(1)
    files=['test_vector_v17.py','test_vector_v17_regressions.py','test_vector_v17_identity.py','test_vector_v17_literal_catalog.py','test_vector_v17_cuda.py']
    assert pytest.main([*(str(code/'tests'/f) for f in files),'-q','-p','no:cacheprovider','--basetemp='+str(out/'tests')])==0
    write_json(out/'tests.json',dict(status='passed',source_sha256=source_hash()))
    rng=np.random.default_rng(172237);members,report=initialize(rng,1024);pop=[m.payload() for m in members]
    frozen=dict(population=pop,population_sha256=digest(pop),initialization=report);write_json(out/'population.json',frozen)
    old_root=out/'v16'
    if a.baseline_profile:
        old_root=a.baseline_profile.parent
        baseline=json.loads(a.baseline_profile.read_text())
        assert baseline['source_sha256']==stop['active']['source_sha256']
        assert baseline['population_sha256']==frozen['population_sha256']
        assert baseline['days']==['2026-08-13','2026-08-27'] and baseline['result_reuse'] is False
        for generation,hashes in enumerate(baseline['session_receipts'],1):
            for day,h in hashes.items():assert file_hash(old_root/f'generation-{generation:04d}'/day/'receipt.json')==h
        print('PROFILE v16 reused: identical source, population, dates and verified receipts',flush=True)
    for engine,source in (('v16',a.baseline_code),('v17',code)):
        if engine=='v16' and a.baseline_profile:continue
        command=[sys.executable,'-B','-u',str(Path(__file__).resolve()),'--worker','--engine',engine,'--code',str(source),
            '--population',str(out/'population.json'),'--campaign',str(a.campaign),'--output',str(out/engine)]
        print('PROFILE',engine,flush=True)
        with (out/(engine+'.log')).open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    old=json.loads((old_root/'profile.json').read_text());new=json.loads((out/'v17/profile.json').read_text())
    assert old['population_sha256']==new['population_sha256'] and old['days']==new['days']
    ordered=[]
    for generation in (1,2):
        for day in old['days']:
            left=json.loads((old_root/f'generation-{generation:04d}'/day/'receipt.json').read_text())['metrics']
            right=json.loads((out/'v17'/f'generation-{generation:04d}'/day/'receipt.json').read_text())['metrics']
            for key in left:
                if left[key]!=right[key]:raise ValueError(f'Exact accounting mismatch: {generation}/{day}/{key}')
            if generation==2:ordered.append(right)
    ratio=new['wall_seconds'][1]/old['wall_seconds'][1]
    from .objectives import score,session_scores
    from .complexity import count
    from .lineage import Pedigree,Selection,next_population
    started=perf_counter();result=score(ordered,[count(m) for m in members]);values,eligible=session_scores(ordered,prepared=result)
    ped=Pedigree();ids=ped.birth(members,1,[(-1,-1)]*1024,['fresh']*1024)
    _,_,div=next_population(rng,members,ids,ped,np.argsort(-result['score']).tolist(),2,Selection(),session_values=values,session_eligible=eligible,global_values=result['score'])
    overhead=perf_counter()-started
    receipt=dict(status='passed' if ratio<=1.05 else 'speed_gate_failed',source_sha256=source_hash(),baseline_source_sha256=old['source_sha256'],baseline_checkpoint_sha256=stop['checkpoint_sha256'],
        population_sha256=frozen['population_sha256'],days=old['days'],exact_accounting_parity=True,warm_v16_seconds=old['wall_seconds'][1],warm_v17_seconds=new['wall_seconds'][1],ratio=ratio,
        maximum_ratio=1.05,objective_and_reproduction_seconds=overhead,mutation_requested=div['mutation_requested_fields'],mutation_applied=div['mutation_applied_fields'],
        baseline_profile_sha256=file_hash(old_root/'profile.json'),baseline_profile=str(old_root/'profile.json'),
        full_generation_with_validation_qualified=False,scope='1024 identical strategies; two complete training sessions; batch512/two workers; cold/warm; no result reuse')
    write_json(out/'qualification.json',receipt);print(json.dumps(receipt),flush=True)
    return 0 if receipt['status']=='passed' else 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('baseline-code','stop-receipt','code','population','baseline-profile'):p.add_argument('--'+name,type=Path)
    p.add_argument('--worker',action='store_true');p.add_argument('--engine',choices=('v16','v17'))
    a=p.parse_args()
    if a.worker:
        if not all((a.code,a.population,a.engine)):raise ValueError('Complete worker identity required')
        return worker(a)
    if not a.baseline_code or not a.stop_receipt:raise ValueError('Baseline authority and verified stop required')
    return qualify(a)


if __name__=='__main__':raise SystemExit(main() or 0)
