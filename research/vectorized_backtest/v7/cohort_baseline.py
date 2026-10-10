"""Run by file path with the stopped deployment as PYTHONPATH for comparison."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,gc
from pathlib import Path
from time import perf_counter
import torch
from research.vectorized_backtest.v7.run_search import source_hash
from research.vectorized_backtest.v7.genome import Individual
from research.vectorized_backtest.v7.evaluator import PopulationPrograms,Execution,replay_cohort
from research.vectorized_backtest.v7.io_pipeline import prepare_host,TransferStaging
from research.vectorized_backtest.v7.data import SessionData
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,configure_caches


def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();output=require_runtime(a.output)
    saved=json.loads((a.campaign/'checkpoint.json').read_text());c=saved['contract']
    if source_hash()!=c['source_sha256']:raise ValueError('Baseline is not the original deployment')
    args=json.loads((a.campaign/'active.json').read_text())['arguments'];arg=lambda name:args[args.index('--'+name)+1]
    days=list(c['input_receipts'])[:c['session_workers']];members=[Individual.restore(v) for v in saved['population']]
    torch.set_num_threads(1);configure_caches(output/'cache',recompile_limit=128);started=perf_counter()
    data=[prepare_host(SessionData(Path(arg('inputs'))/d,Path(arg('history'))/d,device='cpu',
        maximum_gib=c['maximum_data_gib'],feature_cache=Path(arg('feature-cache'))/d),resident=True) for d in days]
    times=[];expected=None
    with TransferStaging() as staging:
        staging.activate(list(zip(days,data)),'cuda');torch.cuda.synchronize();load=perf_counter()-started
        for repeat in range(2):
            started=perf_counter();parts={d:[] for d in days}
            if repeat:
                for item in data:item.resident_features.deactivate();item.deactivate()
                gc.collect();torch.cuda.empty_cache()
                staging.activate(list(zip(days,data)),'cuda')
            for offset in range(0,len(members),c['batch_size']):
                batch=members[offset:offset+c['batch_size']];plan=PopulationPrograms(batch,'cuda')
                try:
                    gates=[plan.evaluate(v,maximum_gate_gib=c['maximum_gate_gib']) for v in data]
                    metrics=replay_cohort(data,batch,gates,execution=Execution(**c['execution']),backend=c['backend'])
                    for day,result in zip(days,metrics):parts[day].append({k:v.cpu().tolist() for k,v in result.items()})
                    del gates,metrics
                finally:plan.close()
            times.append(perf_counter()-started)
            current={d:{k:sum((p[k] for p in parts[d]),[]) for k in parts[d][0]} for d in days}
            if expected is not None and expected!=current:raise ValueError('Legacy repeat mismatch')
            expected=current
            write_json(output/'progress.json',dict(repeat=repeat,seconds=times[-1]))
    write_json(output/'baseline.json',dict(source_sha256=source_hash(),load_seconds=load,seconds=times,metrics=expected))


if __name__=='__main__':main()
