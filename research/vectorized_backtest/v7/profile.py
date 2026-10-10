"""Fixed-population full-wall measurement, with no selection or optimization."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json
from concurrent.futures import ThreadPoolExecutor
from .io_pipeline import Publisher,prepare_host,activate_nonblocking
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
import numpy as np
import torch
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,configure_caches,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.run_structure import training_days
from .data import SessionData
from .genome import sample
from .evaluator import PopulationPrograms,replay_cohort,Execution
from .run_search import source_hash,digest

def measure(inputs,history,cache,root,days,members,concurrency):
    with Publisher() as publisher:
        return _measure(inputs,history,cache,root,days,members,concurrency,publisher)

def _measure(inputs,history,cache,root,days,members,concurrency,publisher):
    root=require_runtime(root);started=perf_counter();rows=[];execution=Execution()
    torch.cuda.reset_peak_memory_stats();packing=perf_counter();programs=PopulationPrograms(members,'cuda');torch.cuda.synchronize()
    pack_seconds=perf_counter()-packing
    for first in range(0,len(days),concurrency):
        if (root.parent/'STOP').exists():raise InterruptedError('Profile stopped between durable cohorts')
        cohort=days[first:first+concurrency];data=[];gates=[];loading=perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=len(cohort),thread_name_prefix='v7-profile-load') as pool:
                data=list(pool.map(lambda day:prepare_host(SessionData(inputs/day,history/day,feature_cache=cache/day,device='cpu'),pin=True),cohort))
            for item in data:activate_nonblocking(item,'cuda')
            torch.cuda.synchronize();load_seconds=perf_counter()-loading
            rules=perf_counter()
            for item in data:
                gate_gib=len(members)*item.clocks*len(item.listing_ids)/1024**3
                gates.append(programs.evaluate(item,maximum_gate_gib=gate_gib))
            torch.cuda.synchronize();rule_seconds=perf_counter()-rules
            replay=perf_counter()
            def progress(clock,total):publisher.write(root/'status.json',dict(stage='position replay',sessions=cohort,clock=clock,total=total,validation_opened=False))
            metrics=replay_cohort(data,members,gates,backend='compile',progress=progress)
            replay_seconds=perf_counter()-replay;publication=perf_counter()
            for day,item,result in zip(cohort,data,metrics):
                publisher.write(root/(day+'.json'),dict(day=day,identity=item.identity,full_session=True,validation_opened=False,
                    feature_receipt_sha256=file_hash(cache/day/'complete.json'),metrics={k:v.cpu().tolist() for k,v in result.items()}))
            publisher.flush()
            publication_seconds=perf_counter()-publication
            rows.append(dict(sessions=cohort,load_seconds=load_seconds,feature_read_decode_assemble_transfer_seconds=None,
                rule_seconds=rule_seconds,signal_seconds_including_overlapped_io=rule_seconds,
                replay_including_compile_seconds=replay_seconds,publication_seconds=publication_seconds,
                peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),peak_gpu_reserved_bytes=torch.cuda.max_memory_reserved()))
            write_json(root/'timings.json',rows);print(json.dumps(rows[-1]),flush=True)
        finally:
            for item in data:
                if hasattr(item,'resident_features'):del item.resident_features
                item.close()
            del data,gates;gc.collect();torch.cuda.empty_cache()
    programs.close();publisher.close()
    record=dict(population=len(members),session_workers=concurrency,training_days=days,full_training_pass=len(days)==30,
        wall_seconds=perf_counter()-started,program_pack_seconds=pack_seconds,timings=rows,
        session_receipts={d:file_hash(root/(d+'.json')) for d in days},population_sha256=digest([m.payload() for m in members]),
        source_sha256=source_hash(),execution=asdict(execution),validation_opened=False,optimization_started=False)
    write_json(root/'complete.json',record);return record

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','history','feature-cache','qualification','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--options',nargs='+',required=True,help='Fixed candidate batch:concurrent sessions')
    a=p.parse_args();days=training_days(a.inputs);root=require_runtime(a.output);configure_caches(root/'cache');torch.set_num_threads(1)
    qualification=json.loads(a.qualification.read_text())
    if qualification.get('status')!='passed' or not qualification.get('full_session') or qualification.get('validation_opened') is not False or qualification.get('source_sha256')!=source_hash():
        raise ValueError('Same-code full-session qualification is required')
    options=[tuple(map(int,value.split(':'))) for value in a.options]
    if any(not 1<=b<=1024 or not 1<=c<=30 for b,c in options):raise ValueError('Bounded measurement options required')
    population=sample(np.random.default_rng(2237),max(b for b,c in options));started=perf_counter();pilots=[]
    with owned_run(root,version='v7-fixed-population-wall-profile-v1'):
        # All clocks and identities on the two largest prepared training days.
        sizes={d:len(json.loads((a.history/d/'complete.json').read_text())['listing_ids']) for d in days}
        pilot_days=sorted(days,key=lambda d:(-sizes[d],d))[:max(c for b,c in options)]
        for b,c in options:
            try:result=measure(a.inputs,a.history,a.feature_cache,root/f'pilot-{b}x{c}',pilot_days[:c],population[:b],c)
            except MemoryError as error:
                write_json(root/f'rejected-{b}x{c}.json',dict(population=b,session_workers=c,reason=str(error),status='memory-budget-rejected'))
                continue
            pilots.append(dict(population=b,session_workers=c,wall_seconds=result['wall_seconds'],sessions=len(result['training_days']),
                candidate_sessions_per_second=b*len(result['training_days'])/result['wall_seconds'],peak_gpu_bytes=max(v['peak_gpu_allocated_bytes'] for v in result['timings'])))
            write_json(root/'options.json',dict(measured=pilots,optimization_started=False,validation_opened=False))
        if not pilots:raise MemoryError("No measured configuration fit declared memory budgets")
        best=max(pilots,key=lambda v:v['candidate_sessions_per_second']);b=best['population'];c=best['session_workers']
        from .run_search import run
        full_root=root/f'all30-{b}x{c}';full_start=perf_counter()
        largest=max(sizes.values());clocks=max(json.loads((a.feature_cache/d/'complete.json').read_text())['identity']['clocks'] for d in days)
        gate_gib=b*clocks*largest/1024**3
        run(a.inputs,a.history,full_root,population_size=b,generations=1,batch_size=b,session_workers=c,
            feature_cache=a.feature_cache,maximum_gate_gib=gate_gib)
        full=dict(population=b,session_workers=c,full_training_pass=True,wall_seconds=perf_counter()-full_start,
            execution_path='run_search.run fixed single generation; no reproduction',receipt_sha256=file_hash(full_root/'generation-0001'/'complete.json'))
        from torch._dynamo.utils import compile_times
        (root/'compiler-timings.txt').write_text(compile_times())
        write_json(root/'complete.json',dict(pilots=pilots,selected_measurement=best,all30=full,
            qualification_sha256=file_hash(a.qualification),full_wall_seconds=perf_counter()-started,validation_opened=False,optimization_started=False))

if __name__=='__main__':main()
