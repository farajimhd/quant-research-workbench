"""Finite, exact-output experiments with independent session CUDA graphs.

No campaign writes or evolution. Graph scheduling changes only; every session
retains its own chronological position, neural memory, feedback and ledger.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
for _key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ.setdefault(_key, '1')
import argparse
import gc
import json
import time
from pathlib import Path
import psutil
import torch
from . import evaluator
from .network import NetworkPlan
from .genome import Individual
from .data import SessionData
from .io_pipeline import prepare_host
from .runtime import configure_caches
from .windows_process import quiet_compilers
from .run_search import digest, bind_dataset
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime, write_json, file_hash


def export(prepared):
    values = prepared.export()[0]
    return {k: v.cpu().tolist() if isinstance(v, torch.Tensor) else v for k, v in values.items()}


def run(job):
    output = Path(job['output'])
    if output.exists():
        raise FileExistsError('Use a new benchmark output directory')
    output = require_runtime(output)
    quiet_compilers()
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    configure_caches(require_runtime(Path(job['cache'])), recompile_limit=128)
    os.environ['V39_NEURAL_BLOCK'] = '256'
    process = psutil.Process()
    write_json(output/'owner.json', dict(pid=process.pid, creation=process.create_time(), job=job))
    manifest = bind_dataset(Path(job['training']), 'training')
    checkpoint = json.loads(Path(job['checkpoint']).read_bytes())
    if digest(checkpoint['state']) != checkpoint['state_sha256']:
        raise ValueError('Checkpoint state changed')
    payload = checkpoint['state']['population']
    fixture_hashes = {job['checkpoint']:file_hash(job['checkpoint'])}
    for path in job.get('additional_checkpoints', []):
        additional=json.loads(Path(path).read_bytes())
        if digest(additional['state'])!=additional['state_sha256']:
            raise ValueError('Additional diagnostic checkpoint changed')
        payload += additional['state']['population']
        fixture_hashes[path]=file_hash(path)
    repeats = job.get('fixture_repeats', 1)
    if not 1 <= repeats <= 6:
        raise ValueError('Diagnostic fixture repeat bound')
    payload = payload * repeats  # Diagnostic only; never initialization or evolution.
    if job.get('candidate_limit'):
        payload = payload[:job['candidate_limit']]
    members = [Individual.restore(x) for x in payload]
    batch = job['batch']
    if not 1 <= batch <= 6144 or not 1 <= len(job['days']) <= 4:
        raise ValueError('Bounded batch/session experiment required')
    execution = evaluator.Execution(fee_per_share=.005, minimum_order_fee=1)
    items = []
    preparation = []
    for day in job['days']:
        if day not in manifest['days']:
            raise ValueError('Only pinned training sessions may be profiled')
        if psutil.virtual_memory().available < (job.get('host_reserve_gib',128)+job.get('host_admission_gib',32))*2**30:
            raise MemoryError('Host admission reserve reached')
        started = time.perf_counter()
        root = lambda key: Path(manifest[key])/day
        item = prepare_host(SessionData(root('inputs'), root('history'), device='cpu',
            feature_cache=root('feature_cache') if manifest.get('feature_cache') else None,
            indicator_root=root('indicator_root'), enriched_root=root('enriched_root')), resident=True)
        clocks = job.get('clocks', item.clocks)
        if not 2 <= clocks <= item.clocks:
            raise ValueError('Invalid bounded prefix')
        item.clocks = clocks
        item.activate('cuda'); item.resident_features.activate('cuda')
        torch.cuda.synchronize()
        items.append(item)
        preparation.append(dict(day=day, load_upload_seconds=time.perf_counter()-started))
    prepared = []
    for day, item in zip(job['days'],items):
        for offset in range(0,len(members),batch):
            if torch.cuda.mem_get_info()[0] < job.get('gpu_reserve_gib',8)*2**30:
                raise MemoryError('GPU reserve reached before graph admission')
            selected=members[offset:offset+batch]
            plan=NetworkPlan(selected,'cuda')
            started=time.perf_counter()
            result=evaluator.replay_cohort([item],selected,[plan],execution=execution,
                backend='compile',trace=True,episode_capacity=65536,capture_only=True,
                replay_block=job.get('block',32))
            result.plan=plan
            result.data=item
            prepared.append((day,offset,result))
            preparation.append(dict(day=day,offset=offset,count=len(selected),capture_seconds=time.perf_counter()-started))
            write_json(output/'preparation.json',preparation)
    graphs=[p.replay for _,_,p in prepared]
    counts=[len(range(0,g.total,g.block)) for g in graphs]
    streams=[torch.cuda.Stream() for _ in graphs]
    results=[]
    reference=None
    for mode in job.get('modes',['sequential','round_robin','streams','grouped','grouped_streams']):
        if mode not in ('sequential','round_robin','streams','grouped','grouped_streams','shared_market'):
            raise ValueError('Unknown graph scheduler')
        parent=None
        retained_projections=[]
        if mode.startswith('grouped') or mode=='shared_market':
            capture_started=time.perf_counter()
            if len(set(counts))!=1:
                raise ValueError('Grouped launch requires matching bounded clock counts')
            # Capture the already-compiled ticks directly. CUDA graph launches
            # themselves cannot be nested inside PyTorch stream capture.
            for g in graphs:g.reset()
            torch.cuda.synchronize()
            stream=torch.cuda.Stream()
            with torch.cuda.stream(stream), torch.inference_mode():
                for g in graphs:g.keepalive()
                for g in graphs:g.reset()
                parent=torch.cuda.CUDAGraph()
                groups={}
                for child,(_,_,p) in zip(streams,prepared):
                    groups.setdefault(id(p.data),[]).append((child,p))
                project_streams={key:torch.cuda.Stream() for key in groups}
                originals={id(p):p.plan.project for _,_,p in prepared}
                with torch.cuda.graph(parent,stream=stream):
                    if mode=='shared_market':
                        for projection_stream in project_streams.values():projection_stream.wait_stream(stream)
                        for _ in range(graphs[0].block):
                            for key,group in groups.items():
                                projection_stream=project_streams[key]
                                first=group[0][1]
                                with torch.cuda.stream(projection_stream):
                                    projected=originals[id(first)](first.data,first.replay.index.clamp_max(first.replay.total)-1)
                                # Retain all captured projections, including their
                                # cross-stream consumers, for the parent lifetime.
                                retained_projections.append(projected)
                                for child,p in group:
                                    child.wait_stream(projection_stream)
                                    p.plan.project=lambda data,index,value=projected:value
                                    with torch.cuda.stream(child):p.replay.keepalive()
                                for child,p in group:projection_stream.wait_stream(child)
                        for projection_stream in project_streams.values():stream.wait_stream(projection_stream)
                    elif mode=='grouped_streams':
                        for child in streams:child.wait_stream(stream)
                        for child,g in zip(streams,graphs):
                            with torch.cuda.stream(child):
                                for _ in range(g.block):g.keepalive()
                        for child in streams:stream.wait_stream(child)
                    else:
                        for g in graphs:
                            for _ in range(g.block):g.keepalive()
                for _,_,p in prepared:p.plan.project=originals[id(p)]
            torch.cuda.synchronize()
            preparation.append(dict(mode=mode,group_capture_seconds=time.perf_counter()-capture_started))
            write_json(output/'preparation.json',preparation)
        for repeat in range(job.get('repeats',2)):
            for g in graphs:g.reset()
            torch.cuda.synchronize()
            if psutil.virtual_memory().available < job.get('host_reserve_gib',128)*2**30:
                raise MemoryError('Host reserve reached')
            start_event=torch.cuda.Event(enable_timing=True)
            end_event=torch.cuda.Event(enable_timing=True)
            start=time.perf_counter();start_event.record()
            if mode=='sequential':
                for g in graphs:g.enqueue()
            elif mode=='round_robin':
                for i in range(max(counts)):
                    for g,n in zip(graphs,counts):
                        if i<n:g.graph.replay()
            elif mode=='streams':
                main=torch.cuda.current_stream()
                for child in streams:child.wait_stream(main)
                for i in range(max(counts)):
                    for child,g,n in zip(streams,graphs,counts):
                        if i<n:
                            with torch.cuda.stream(child):g.graph.replay()
                for child in streams:main.wait_stream(child)
            else:
                for _ in range(counts[0]):parent.replay()
            end_event.record();end_event.synchronize()
            seconds=time.perf_counter()-start
            values={}
            for day,offset,p in prepared:
                result=export(p)
                if day not in values:values[day]={k:[] for k in result}
                for key,value in result.items():values[day][key].extend(value)
            hashes={day:digest(value) for day,value in values.items()}
            if reference is None:reference=hashes
            if hashes!=reference:raise AssertionError('Scheduling changed financial or episode outputs')
            label=f'{mode}-{repeat}'
            write_json(output/(label+'-financial.json'),values)
            row=dict(mode=mode,repeat=repeat,seconds=seconds,gpu_seconds=start_event.elapsed_time(end_event)/1000,
                financial_sha256=hashes,gpu_allocated=torch.cuda.memory_allocated(),gpu_reserved=torch.cuda.memory_reserved(),
                gpu_peak_allocated=torch.cuda.max_memory_allocated(),available=psutil.virtual_memory().available,rss=process.memory_info().rss)
            results.append(row);write_json(output/'timings.json',results);print(json.dumps(row),flush=True)
    write_json(output/'complete.json',dict(job=job,results=results,preparation=preparation,
        checkpoint_sha256=file_hash(job['checkpoint']),population_sha256=digest(payload),training_sha256=file_hash(job['training']),
        fixture_checkpoint_hashes=fixture_hashes,
        source_files={name:file_hash(Path(__file__).with_name(name)) for name in ('benchmark_session_batches.py','evaluator.py','replay_graph.py')}))
    del prepared,graphs,items
    gc.collect();torch.cuda.empty_cache()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    args=parser.parse_args()
    job=json.loads(args.job.read_bytes())
    preexisting=Path(job['output']).exists()
    try:run(job)
    except BaseException as exc:
        output=Path(job['output'])
        if not preexisting and output.exists():write_json(output/('failure-'+str(time.time_ns())+'.json'),dict(error=repr(exc)))
        raise


if __name__=='__main__':main()
