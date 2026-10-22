"""Finite replay architecture experiments; never launches or mutates a campaign.

One job owns one independent lane. An external controller may release multiple
ready lanes together using a shared start-file. Results include startup separately
from repeated replay, and retain complete financial outputs for comparison.
"""
from research.vectorized_backtest.v6.torch_backtest.compile_lifecycle import scoped_compile
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
for _key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'POLARS_MAX_THREADS'):
    os.environ.setdefault(_key, '1')
import argparse
import copy
import json
import time
from pathlib import Path
import psutil
import torch
from . import evaluator
from .network import NetworkPlan, outcome_values
from .genome import Individual
from .data import SessionData
from .io_pipeline import prepare_host
from .runtime import configure_caches
from .windows_process import quiet_compilers
from .run_search import digest
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime, write_json, file_hash


class HybridPlan:
    """GPU recurrent networks; CPU execution with explicit chronological handoff."""
    def __init__(self, members, gpu_data):
        self.gpu = NetworkPlan(members, 'cuda')
        self.total_nodes = self.gpu.total_nodes
        self.data = gpu_data
        self.graph = None
        self.transfer_seconds = 0.
        self.calls = 0
        self.outcomes = scoped_compile(outcome_values, fullgraph=True, dynamic=False)

    def prepare(self, b, u):
        self.gpu.prepare(b, u)
        if self.graph is not None:
            return
        self.position = torch.zeros((1, b, u, 9), device='cuda', dtype=torch.float64)
        self.opened = torch.zeros((1, b, u), device='cuda', dtype=torch.float64)
        self.previous = torch.ones((1, 1, u), device='cuda', dtype=torch.float64)
        self.index = torch.zeros((), device='cuda', dtype=torch.int64)
        self.output = torch.zeros((1, b, u), device='cuda', dtype=torch.int16)
        self.ratios = torch.empty_like(self.gpu.ratios, device='cpu', pin_memory=True)
        self.feedback = torch.empty_like(self.gpu.feedback, device='cpu', pin_memory=True)
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream), torch.inference_mode():
            for _ in range(2):
                self.gpu.signals(self.data, self.index, self.position, self.opened, self.previous, self.output)
            self.gpu.reset()
            self.graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(self.graph, stream=stream):
                self.gpu.signals(self.data, self.index, self.position, self.opened, self.previous, self.output)
            self.gpu.reset()
        torch.cuda.current_stream().wait_stream(stream)
        torch.cuda.synchronize()

    def reset(self):
        self.gpu.reset()
        self.feedback.zero_()
        self.ratios.fill_(1.)
        self.transfer_seconds = 0.
        self.calls = 0

    def signals(self, data, index, position, opened, previous, out):
        start = time.perf_counter()
        self.position.copy_(position)
        self.opened.copy_(opened)
        self.previous.copy_(previous)
        self.index.copy_(index)
        self.gpu.feedback.copy_(self.feedback)
        self.graph.replay()
        out.copy_(self.output)
        self.ratios.copy_(self.gpu.ratios)
        self.feedback.copy_(self.gpu.feedback)
        # Includes transfers, GPU evaluation and required synchronization.
        self.transfer_seconds += time.perf_counter() - start
        self.calls += 1
        return out

    def record_outcomes(self, before, after, price, signals):
        self.feedback.copy_(self.outcomes(before, after, price, signals, self.ratios, self.feedback))


def run(job):
    if Path(job['output']).exists():
        raise FileExistsError('Use a new benchmark output directory')
    output = require_runtime(Path(job['output']))
    quiet_compilers()
    torch.set_num_threads(job.get('cpu_threads', 1))
    torch.set_num_interop_threads(1)
    process = psutil.Process()
    if 'cpu_core' in job:
        process.cpu_affinity([job['cpu_core']])
    configure_caches(require_runtime(Path(job['cache'])))
    os.environ['V41_NEURAL_BLOCK'] = '256'
    started = time.perf_counter()
    manifest = json.loads(Path(job['training']).read_bytes())
    day = job['day']
    assert day in manifest['days'] and manifest['role'] == 'training'
    checkpoint = json.loads(Path(job['checkpoint']).read_bytes())
    assert digest(checkpoint['state']) == checkpoint['state_sha256']
    payload = checkpoint['state']['population'][job['offset']:job['offset'] + job['population']]
    assert len(payload) == job['population']
    members = [Individual.restore(x) for x in payload]
    def root(key):
        return Path(manifest[key]) / day
    data = prepare_host(SessionData(root('inputs'), root('history'), device='cpu',
        feature_cache=root('feature_cache') if manifest.get('feature_cache') else None,
        indicator_root=root('indicator_root'), enriched_root=root('enriched_root')), resident=True)
    assert 2 <= job['clocks'] <= data.clocks
    data.clocks = job['clocks']
    gpu_data = copy.copy(data)
    gpu_data.resident_features = copy.copy(data.resident_features)
    gpu_data.resident_features.column_plans = {}
    gpu_data.activate('cuda')
    gpu_data.resident_features.activate('cuda')
    assert torch.cuda.mem_get_info()[0] > job.get('gpu_reserve_gib', 3) * 2**30
    mode = job['mode']
    if mode == 'gpu':
        plan = NetworkPlan(members, 'cuda')
        replay_data = gpu_data
        backend = 'compile'
    elif mode == 'hybrid':
        plan = HybridPlan(members, gpu_data)
        replay_data = data
        backend = 'eager'
        evaluator.step = scoped_compile(evaluator.step, fullgraph=True, dynamic=False)
    else:
        raise ValueError('Unknown benchmark mode')
    execution = evaluator.Execution(fee_per_share=.005, minimum_order_fee=1)
    rows = []
    def once(label):
        torch.cuda.synchronize()
        start = time.perf_counter()
        result = evaluator.replay_cohort([replay_data], members, [plan], execution=execution,
            backend=backend, trace=True, episode_capacity=65536)[0]
        torch.cuda.synchronize()
        seconds = time.perf_counter() - start
        values = {k: v.cpu().tolist() if isinstance(v, torch.Tensor) else v for k, v in result.items()}
        row = dict(label=label, seconds=seconds, epoch=time.time(), financial_sha256=digest(values),
            closed_positions=sum(values['closed_positions']), filled_batches=sum(values['filled_batches']),
            gpu_allocated_bytes=torch.cuda.memory_allocated(), rss_bytes=process.memory_info().rss,
            available_bytes=psutil.virtual_memory().available)
        if mode == 'hybrid':
            row.update(handoff_and_neural_seconds=plan.transfer_seconds, handoffs=plan.calls)
        write_json(output / (label + '-financial.json'), values)
        rows.append(row)
        write_json(output / 'timings.json', rows)
        print(json.dumps(row), flush=True)
    once('cold')
    write_json(output / 'ready.json', dict(pid=os.getpid(), creation=process.create_time(),
        startup_seconds=time.perf_counter() - started))
    if job.get('start_file'):
        deadline=time.monotonic()+300
        while not Path(job['start_file']).exists():
            if time.monotonic()>deadline:raise TimeoutError('Benchmark start barrier expired')
            time.sleep(.05)
    for i in range(job.get('repeats', 2)):
        once('warm-' + str(i))
    assert len({r['financial_sha256'] for r in rows}) == 1, 'Repeated replay changed results'
    write_json(output / 'complete.json', dict(job=job, results=rows,
        checkpoint_sha256=file_hash(job['checkpoint']), population_sha256=digest(payload),
        training_sha256=file_hash(job['training']), benchmark_sha256=file_hash(__file__)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    args = parser.parse_args()
    run(json.loads(args.job.read_bytes()))


if __name__ == '__main__':
    main()
