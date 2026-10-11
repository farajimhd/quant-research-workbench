"""Actual 1024-candidate full-session A/B and bounded selection-overhead gate."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('OMP_NUM_THREADS','1')
import argparse,copy,json,subprocess,sys
from pathlib import Path
from time import perf_counter

def baseline(campaign,output):
    import torch
    from research.vectorized_backtest.v7.genome import Individual
    from research.vectorized_backtest.v7.session_lanes import SessionLanes
    from research.vectorized_backtest.v7.terminal import CampaignProgress
    from research.vectorized_backtest.v7.run_search import source_hash
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json
    torch.set_num_threads(1);campaign=Path(campaign);output=require_runtime(output)
    if (campaign/'owner.lock').exists() or (campaign/'controller.lock').exists():raise ValueError('Stop campaign before profiling')
    saved=json.loads((campaign/'checkpoint.json').read_text());contract=saved['contract']
    args=json.loads((campaign/'active.json').read_text())['arguments'];arg=lambda name:args[args.index('--'+name)+1]
    days=list(contract['input_receipts'])[:contract['session_workers']]
    members=[Individual.restore(v) for v in saved['population']]
    config=dict(inputs=arg('inputs'),history=arg('history'),feature_cache=arg('feature-cache'),output=str(output),
        device=contract['device'],backend=contract['backend'],batch_size=contract['batch_size'],execution=contract['execution'],
        maximum_data_gib=contract['maximum_data_gib'],maximum_gate_gib=contract['maximum_gate_gib'])
    seconds=[]
    with CampaignProgress(output,contract) as panel,SessionLanes(config,contract['session_workers'],panel) as lanes:
        for repeat in range(2):
            panel.begin_generation(repeat+1);start=perf_counter()
            lanes.generation(days,members,repeat+1,saved['population_sha256'])
            seconds.append(perf_counter()-start)
    write_json(output/'baseline.json',dict(seconds=seconds,source_sha256=source_hash(),days=days))

def qualify(campaign,output):
    import numpy as np
    from research.vectorized_backtest.v7.run_search import source_hash,digest
    from research.vectorized_backtest.v7.genome import Individual
    from research.vectorized_backtest.v7.diversity import summarize,next_population,program_key,VERSION
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash,write_json
    campaign=Path(campaign);output=require_runtime(output)
    saved=json.loads((campaign/'checkpoint.json').read_text());c=saved['contract']
    active=json.loads((campaign/'active.json').read_text());old=Path(active['code']);new=Path(__file__).resolve().parents[3]
    names=['session_lanes.py','evaluator.py','rule_plan.py','replay_graph.py','genome.py','features.py','data.py','resident_features.py','swing_projection.py','io_pipeline.py']
    unchanged={name:file_hash(new/'research/vectorized_backtest/v7'/name) for name in names}
    if any(file_hash(old/'research/vectorized_backtest/v7'/name)!=checksum for name,checksum in unchanged.items()):raise ValueError('GPU execution changed')
    for label,code in [('old',old),('new',new)]:
        subprocess.run([sys.executable,'-B','-P',str(Path(__file__)),'--campaign',str(campaign),
            '--output',str(output/label),'--baseline'],cwd=code,
            env=dict(os.environ,PYTHONPATH=str(code),PYTHONDONTWRITEBYTECODE='1'),check=True)
    reports={label:json.loads((output/label/'baseline.json').read_text()) for label in ('old','new')}
    if reports['old']['source_sha256']!=c['source_sha256']:raise ValueError('Old source changed')
    for repeat in (1,2):
        for day in reports['old']['days']:
            read=lambda label:json.loads((output/label/f'generation-{repeat:04d}'/day/'receipt.json').read_text())['metrics']
            if read('old')!=read('new'):raise ValueError('Full-session metric mismatch')
    population=[Individual.restore(v) for v in saved['population']]
    if digest(saved['population'])!=saved['population_sha256']:raise ValueError('Population changed')
    last=campaign/f"generation-{saved['completed_generations']:04d}"
    ordered=[json.loads((last/day/'receipt.json').read_text())['metrics'] for day in c['input_receipts']]
    rank=[v['candidate'] for v in json.loads((last/'ranking.json').read_text())]
    # Timing workload only: real saved 1024 programs and real all30 metrics.
    # These are different generations, so generated offspring are NOT deployed.
    samples=[]
    for _ in range(5):
        rng=np.random.default_rng();rng.bit_generator.state=copy.deepcopy(saved['rng_state']);start=perf_counter()
        reps,sizes,statistics=summarize(population,ordered,rank)
        generated,selection=next_population(rng,population,reps)
        samples.append(perf_counter()-start)
        if len({program_key(v) for v in generated})!=len(population):raise ValueError('Duplicate offspring')
    overhead=max(samples);old_wall=reports['old']['seconds'][-1];new_wall=reports['new']['seconds'][-1]
    # Account for small wall-time noise; unchanged GPU engine is separately bound.
    no_slowdown=overhead<1. and new_wall<=old_wall*1.03
    result=dict(status='passed' if no_slowdown else 'failed',no_slowdown=no_slowdown,
        source_sha256=source_hash(),old_source_sha256=c['source_sha256'],checkpoint_sha256=file_hash(campaign/'checkpoint.json'),
        population_sha256=saved['population_sha256'],candidates=c['population'],batch_size=c['batch_size'],session_workers=c['session_workers'],
        execution=c['execution'],full_session=True,exact_metrics=True,validation_opened=False,diversity_version=VERSION,
        unchanged_gpu_engines=unchanged,old_seconds=reports['old']['seconds'],new_seconds=reports['new']['seconds'],
        selection_seconds=samples,wall_noise_tolerance=.03,selection_benchmark_scope='actual saved population and prior all30 metrics; offspring discarded',
        maximum_selection_seconds=overhead)
    write_json(output/'qualification.json',result)
    if not no_slowdown:raise ValueError('Performance gate failed; successor launch forbidden')
    print(json.dumps(result),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline',action='store_true')
    a=p.parse_args();(baseline if a.baseline else qualify)(a.campaign,a.output)

if __name__=='__main__':main()
