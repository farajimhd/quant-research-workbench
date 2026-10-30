"""One complete, isolated generation from a saved pilot checkpoint, no campaign writes."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,queue,shutil,time,sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--code',type=Path,required=True);p.add_argument('--score-module',type=Path)
    p.add_argument('--generations',type=int,default=1,choices=(1,2))
    a=p.parse_args();sys.path.insert(0,str(a.code))
    from research.vectorized_backtest.v59.pilot import Pilot
    from research.vectorized_backtest.v59.full_common import config
    from research.vectorized_backtest.v59.session_lanes import Worker
    from research.vectorized_backtest.v59.identity import source_hash,digest
    from research.vectorized_backtest.v42.publication import write_json
    from research.vectorized_backtest.v42.distributed_assets import sha
    if a.score_module:
        import importlib.util
        import research.vectorized_backtest.v59.robust_score as score
        spec=importlib.util.spec_from_file_location('benchmark_score',a.score_module);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        score.combine=module.combine
    job=json.loads(a.job.read_bytes());original=Path(job['campaign']);output=a.output
    if output.exists():raise ValueError('Benchmark output already exists')
    output.mkdir(parents=True)
    checkpoint=json.loads((original/'global/checkpoint.json').read_bytes());done=checkpoint['state']['completed']
    # Copy metadata; original immutable replay receipts remain referenced read-only.
    shutil.copytree(original/'global',output/'global',copy_function=os.link)
    job=dict(job,campaign=str(output),source_sha256=source_hash())
    from research.vectorized_backtest.v59.full_controller import checkpoint_contract
    checkpoint['contract']=checkpoint_contract(job,job['training_days'],[])
    write_json(output/'global/checkpoint.json',checkpoint)
    class Finished(Exception):pass
    # Instrument the controller's actual generation-start clock, including
    # population encoding before its first artifact and the final checkpoint.
    import inspect,types
    import research.vectorized_backtest.v59.full_controller as implementation
    target=implementation.Controller.global_run
    if getattr(Pilot,'pipeline_enabled',False):
        import research.vectorized_backtest.v59.generation_flow as implementation
        target=implementation.evolve
    lines,first=inspect.getsourcelines(target)
    start_line=first+next(i for i,line in enumerate(lines) if 'started=time.perf_counter()' in line)
    clocks={}
    def measured_clock():
        value=time.perf_counter();frame=sys._getframe(1)
        if frame.f_code is target.__code__ and frame.f_lineno==start_line:clocks.setdefault('start',value)
        return value
    implementation.time=types.SimpleNamespace(perf_counter=measured_clock,time=time.time)
    class Bench(Pilot):
        def publish(self,**extra):
            self.view.update(extra)
            if ('training_ranking' in extra and max(extra.get('completed_generations',0),extra.get('publication_pending_generation',0) or 0)>=done+a.generations):
                clocks['end']=time.perf_counter();raise Finished()
    c=Bench(job);cfg=config(job,'laptop');cfg.update(output=str(output/'replays/training'),compile_cache_root=str(original/'compiler-cache'))
    os.environ.update(V42_REPRODUCTION_WORKERS='1',NUMBA_CACHE_DIR=str(output/'numba-cache'))
    import torch
    cfg['lane_memory_bytes']=torch.cuda.mem_get_info()[0]*.8
    c.worker=Worker(cfg,queue.Queue(maxsize=1),0)
    try:
        day=job['training_days'][0];item=c.worker.load(day)
        c.worker.staging.activate([(day,item)],torch.device('cuda'))
        cache=json.loads((original/'replays/training/result-cache'/f'{day}.json').read_bytes())
        cache['identity']['source']=job['source_sha256'];c.worker.resident_results=(cache['identity'],cache['results']);c.worker.last_result_day=day
        started=time.perf_counter()
        try:c.global_run({},job['training_days'],[])
        except Finished:pass
        folder=output/'global'/f'generation-{done+a.generations:04d}'
        seal=json.loads((folder/'complete.json').read_bytes())
        transition=json.loads((folder/'transition-timings.json').read_bytes())
        timing_path=folder/'generation-timings.json'
        full_time=json.loads(timing_path.read_bytes())['full_generation_seconds'] if timing_path.exists() else clocks['end']-clocks['start']
        result=dict(generation=done+a.generations,generations_measured=a.generations,wall_seconds=seal['wall_seconds'],full_generation_seconds=full_time,critical_path_seconds=clocks['end']-clocks['start'],driver_seconds=time.perf_counter()-started,
                    transition=transition,checkpoint_input_sha256=sha(original/'global/checkpoint.json'),
                    successor=json.loads((folder/'successor.json').read_bytes())['sha256'],
                    source_sha256=job['source_sha256'],score_override=str(a.score_module),warm_market=True)
        write_json(output/'comparison.json',result);print(json.dumps(result),flush=True)
    finally:
        c.worker.close()
        if getattr(c,'fresh_prefetch',None):c.fresh_prefetch.close()


if __name__=='__main__':main()
