"""Isolated fixed-population full-session evaluator for V7/V8 comparison."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,importlib,json
from pathlib import Path
from time import perf_counter
import torch


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--engine',choices=('v7','v8'),required=True)
    p.add_argument('--days',nargs='+');p.add_argument('--repeats',type=int,default=2)
    a=p.parse_args();prefix='research.vectorized_backtest.'+a.engine
    search=importlib.import_module(prefix+'.run_search');genome=importlib.import_module(prefix+'.genome')
    lanes_module=importlib.import_module(prefix+'.session_lanes');terminal=importlib.import_module(prefix+'.terminal')
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
    saved=json.loads((a.campaign/'checkpoint.json').read_text(encoding='utf-8-sig'));contract=saved['contract']
    active=json.loads((a.campaign/'active.json').read_text(encoding='utf-8-sig'));arguments=active['arguments']
    arg=lambda name:arguments[arguments.index('--'+name)+1]
    population=[genome.Individual.restore(v) for v in saved['population']]
    token=search.digest([v.payload() for v in population])
    if token!=saved['population_sha256']:raise ValueError('Frozen population identity changed')
    days=a.days or list(contract['input_receipts'])
    if set(days)-set(contract['input_receipts']):raise ValueError('Profile uses training dates only')
    output=require_runtime(a.output);torch.set_num_threads(1)
    config=dict(inputs=arg('inputs'),history=arg('history'),feature_cache=arg('feature-cache'),output=str(output),
        device='cuda',backend='compile',batch_size=contract['batch_size'],execution=contract['execution'],
        maximum_data_gib=contract['maximum_data_gib'],maximum_gate_gib=contract['maximum_gate_gib'],
        source_sha256=search.source_hash(),split='training',episode_capacity=65536,plan_limit=2,
        reuse_results=False)
    receipts=[];times=[]
    with terminal.CampaignProgress(output,contract) as panel,lanes_module.SessionLanes(config,contract['session_workers'],panel) as lanes:
        for repeat in range(1,a.repeats+1):
            panel.begin_generation(repeat);began=perf_counter()
            timings=lanes.generation(days,population,repeat,token)
            elapsed=perf_counter()-began;times.append(elapsed)
            receipts.append({day:file_hash(output/f'generation-{repeat:04d}'/day/'receipt.json') for day in days})
            write_json(output/'progress.json',dict(repeat=repeat,seconds=elapsed,completed=len(days),timings=timings))
        panel.update(status='complete',stage='Fixed population comparison complete',completed_generations=a.repeats)
    write_json(output/'profile.json',dict(engine=a.engine,source_sha256=config['source_sha256'],population_sha256=token,
        days=days,repeats=a.repeats,wall_seconds=times,session_receipts=receipts,result_reuse=False))


if __name__=='__main__':main()
