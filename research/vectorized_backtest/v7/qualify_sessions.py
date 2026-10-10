"""Compare actual checkpoint candidates on two complete training sessions."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys
from pathlib import Path
from time import perf_counter
import torch
from .run_search import source_hash,digest
from .genome import Individual
from .session_lanes import SessionLanes
from .terminal import CampaignProgress
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def qualify(campaign,output):
    output=require_runtime(output);campaign=Path(campaign)
    if (campaign/'owner.lock').exists() or (campaign/'controller.lock').exists():raise ValueError('Campaign must be stopped before GPU qualification')
    saved=json.loads((campaign/'checkpoint.json').read_text());contract=saved['contract']
    if saved['completed_generations']<1:raise ValueError('A sealed generation is required')
    active=json.loads((campaign/'active.json').read_text());args=active['arguments']
    arg=lambda name:args[args.index('--'+name)+1]
    days=list(contract['input_receipts'])[:contract['session_workers']]
    members=[Individual.restore(v) for v in saved['population']]
    if digest([v.payload() for v in members])!=saved['population_sha256']:raise ValueError('Checkpoint population changed')
    for day in days:
        for name,root in [('input',arg('inputs')),('history',arg('history')),('feature',arg('feature-cache'))]:
            if file_hash(Path(root)/day/'complete.json')!=contract[name+'_receipts'][day]:raise ValueError('Input receipt changed')
    torch.set_num_threads(1)
    old=Path(active['code'])
    env=dict(os.environ,PYTHONPATH=str(old),PYTHONDONTWRITEBYTECODE='1')
    # Safe-path prevents the sibling profile.py from shadowing stdlib profile
    # when Torch imports cProfile. PYTHONPATH explicitly selects the old code.
    subprocess.run([sys.executable,'-B','-P',str(Path(__file__).with_name('cohort_baseline.py')),
        '--campaign',str(campaign),'--output',str(output/'legacy')],cwd=old,env=env,check=True)
    legacy=json.loads((output/'legacy'/'baseline.json').read_text())
    expected=legacy['metrics'];baseline=legacy['seconds'];load_seconds=legacy['load_seconds']
    config=dict(inputs=arg('inputs'),history=arg('history'),feature_cache=arg('feature-cache'),output=str(output/'independent'),
        device='cuda',backend=contract['backend'],batch_size=contract['batch_size'],execution=contract['execution'],
        maximum_data_gib=contract['maximum_data_gib'],maximum_gate_gib=contract['maximum_gate_gib'])
    target=require_runtime(config['output']);concurrent=[];records=[]
    with CampaignProgress(target,contract) as panel,SessionLanes(config,contract['session_workers'],panel) as lanes:
        for repeat in range(2):
            panel.begin_generation(repeat+1);start=perf_counter()
            timing=lanes.generation(days,members,repeat+1,saved['population_sha256'])
            concurrent.append(perf_counter()-start)
            for day in days:
                receipt=json.loads((target/f'generation-{repeat+1:04d}'/day/'receipt.json').read_text())
                if any(receipt['metrics'].get(k)!=v for k,v in expected[day].items()):raise ValueError('Concurrent full-session metrics differ from legacy cohort')
                if any(w<0 or w>n for w,n in zip(receipt['metrics']['winning_positions'],receipt['metrics']['closed_positions'])):raise ValueError('Invalid winning-position counts')
                if not all(receipt['metrics']['terminal_valid']):raise ValueError('Terminal positions invalid')
            records.append(timing)
            write_json(output/'progress.json',dict(stage='independent complete',repeat=repeat,seconds=concurrent[-1]))
    # Bind unchanged financial/rule engines to the old deployed authority.
    old=Path(active['code']);new=Path(__file__).resolve().parents[3]
    names=['rule_plan.py','replay_graph.py','genome.py','features.py','data.py','feature_cache.py','swing_projection.py']
    for name in names:
        relative=Path('research/vectorized_backtest/v7')/name
        if file_hash(old/relative)!=file_hash(new/relative):raise ValueError('Qualification changed an accounting or rule engine')
    report=dict(status='passed',source_sha256=source_hash(),old_source_sha256=contract['source_sha256'],
        checkpoint_sha256=file_hash(campaign/'checkpoint.json'),population_sha256=saved['population_sha256'],
        candidates=len(members),sessions=days,batch_size=contract['batch_size'],session_workers=contract['session_workers'],
        execution=contract['execution'],full_session=True,validation_opened=False,exact_metrics=True,
        cohort_load_seconds=load_seconds,cohort_seconds=baseline,independent_seconds=concurrent,
        warm_speedup=baseline[-1]/concurrent[-1],records=records)
    write_json(output/'qualification.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(qualify(a.campaign,a.output)))


if __name__=='__main__':main()
