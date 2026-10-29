"""Short same-genome GPU replay comparison; never advances a campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,queue,time,hashlib,importlib.util,sys,gc
from pathlib import Path
import numpy as np

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-job',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--clocks',type=int,default=512);parser.add_argument('--population',type=Path);parser.add_argument('--offset',type=int,default=5120);parser.add_argument('--baseline-kernel',type=Path);parser.add_argument('--full-receipt',type=Path);parser.add_argument('--blocks',default='');parser.add_argument('--full-only',action='store_true');parser.add_argument('--baseline-only',action='store_true');a=parser.parse_args()
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime
    output=require_runtime(a.output);output.mkdir(parents=True,exist_ok=True)
    import torch
    from .full_common import config
    from .session_lanes import Worker
    from .genome import sample
    from .templates import sample_templates
    from .evaluator import PopulationPrograms,replay_cohort,Execution
    from .identity import source_hash
    j=json.loads(a.source_job.read_bytes());j.update(source_sha256=source_hash(),campaign=str(output),objective=dict(drawdown_weight=1.,loss_weight=1.))
    cfg=config(j,'laptop');cfg.update(output=str(output/'replay'),compile_cache_root=str(output/'compiler'),host_prefetch=False,gpu_execution_gate=None)
    worker=Worker(cfg,queue.Queue(maxsize=1),0);program=None
    try:
        templates,_=sample_templates();members=templates[::12][:256]+sample(np.random.default_rng(2021),256)
        if a.population:
            from .genome import Individual
            members=[Individual.restore(v) for v in json.loads(a.population.read_bytes())['population'][a.offset:a.offset+1024]]
        item=worker.load('2026-08-13');worker.staging.activate([('2026-08-13',item)],torch.device('cuda'));torch.cuda.synchronize();full_clocks=item.clocks;item.clocks=a.clocks+1
        program=PopulationPrograms(members,'cuda');plan=program.evaluate(item)
        import research.vectorized_backtest.v57.network_cuda as active
        baseline_path=a.baseline_kernel or Path(__file__).parents[1]/'v56/network_cuda.py'
        spec=importlib.util.spec_from_file_location('research.vectorized_backtest.v57.profile_baseline',baseline_path);baseline=importlib.util.module_from_spec(spec);sys.modules[spec.name]=baseline;spec.loader.exec_module(baseline)
        optimized=baseline.signals if a.baseline_only else active.signals;reference=None;rows=[]
        cases=[('generic',baseline.signals,128),('feedforward',optimized,128)]
        if a.blocks:cases=[('tile-'+b,optimized,int(b)) for b in a.blocks.split(',')]
        for name,signals,block in ([] if a.full_only else cases):
            plan.cuda_block=block
            active.signals=signals
            ev=replay_cohort([item],members,[plan],execution=Execution(**j['execution']),backend='compile',trace=True,capture_only=True)
            torch.cuda.synchronize();ev.replay.reset();ev.replay.graph.replay();torch.cuda.synchronize();times=[]
            for _ in range(3):
                ev.replay.reset();torch.cuda.synchronize();start=time.perf_counter();ev.replay.enqueue();torch.cuda.synchronize();times.append(time.perf_counter()-start)
            result=ev.export()[0];serial={k:v.detach().cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}
            signature=hashlib.sha256(json.dumps(serial,sort_keys=True).encode()).hexdigest()
            if reference is None:reference=signature
            assert signature==reference,'Financial parity failed'
            rows.append(dict(mode=name,seconds=times,median_seconds=float(np.median(times)),metrics_sha256=signature,candidates=len(members),clocks=a.clocks));print(rows[-1],flush=True)
            del ev;gc.collect()
        active.signals=optimized
        result=dict(rows=rows,speedup=rows[0]['median_seconds']/rows[-1]['median_seconds'] if rows else None,exact_financial_parity=True,source_sha256=source_hash())
        if a.full_receipt:
            if rows:plan.cuda_block=int(min(rows,key=lambda r:r['median_seconds'])['mode'].split('-')[-1]) if a.blocks else 128
            item.clocks=full_clocks;start=time.perf_counter()
            raw=replay_cohort([item],members,[plan],execution=Execution(**j['execution']),backend='compile',trace=True)[0]
            full_seconds=time.perf_counter()-start
            actual=json.loads(json.dumps({k:v.detach().cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in raw.items()}))
            saved=json.loads(a.full_receipt.read_bytes())['metrics']
            expected={k:saved[k][a.offset:a.offset+len(members)] for k in actual}
            differences=[k for k in actual if actual[k]!=expected[k]]
            result.update(full_replay_seconds=full_seconds,full_clocks=full_clocks-1,full_parity_differences=differences,exact_financial_parity=not differences)
            examples={}
            for k in differences:
                samples=[]
                for i,(x,y) in enumerate(zip(actual[k],expected[k])):
                    if x!=y:
                        samples.append(dict(candidate=i,actual=str(x)[:180],expected=str(y)[:180]))
                    if len(samples)==3:break
                examples[k]=samples
            result['differences']=examples
            (output/'comparison.json').write_text(json.dumps(result,indent=2))
            print('Full replay',full_seconds,'differences',differences,flush=True)
            assert not differences,'Full saved financial replay differs'
        (output/'comparison.json').write_text(json.dumps(result,indent=2))
    finally:
        if program is not None:program.close()
        worker.close()

if __name__=='__main__':main()
