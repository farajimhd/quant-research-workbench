"""Gate V18 on full-session parity and measured matched-population acceleration."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys
from pathlib import Path
import numpy as np
from .prepare_validation import require_finished
from .run_search import source_hash
from .objectives import Objective,score
from .genome import Individual
from .complexity import count
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--days',nargs='+');parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--stopped-v7-receipt',type=Path)
    parser.add_argument('--capture-gib',type=float,default=4.)
    a=parser.parse_args()
    if not .25<=a.capture_gib<=16.:raise ValueError("Capture cache must be between 0.25 and 16 GiB per worker")
    require_finished(a.campaign,a.stopped_v7_receipt)
    if not 2<=a.repeats<=5:raise ValueError('At least cold/warm measurements, maximum five repeats')
    output=require_runtime(a.output);active=json.loads((a.campaign/'active.json').read_text(encoding='utf-8-sig'))
    script=Path(__file__).with_name('profile_worker.py')
    saved=json.loads((a.campaign/'checkpoint.json').read_text(encoding='utf-8-sig'))
    for version,code in [('v7',Path(active['code'])),('v18',Path(__file__).resolve().parents[3])]:
        command=[sys.executable,'-B','-P',str(script),'--engine',version,'--campaign',str(a.campaign),
                 '--output',str(output/version),'--repeats',str(a.repeats)]
        if a.days:command+=['--days',*a.days]
        if version=='v18':command+=['--capture-gib',str(a.capture_gib)]
        env=dict(os.environ,PYTHONPATH=str(code),PYTHONDONTWRITEBYTECODE='1')
        print(' '.join(command),flush=True)
        with (output/(version+'.log')).open('w',encoding='utf-8') as stream:
            subprocess.run(command,cwd=code,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
    old=json.loads((output/'v7'/'profile.json').read_text(encoding='utf-8-sig'));new=json.loads((output/'v18'/'profile.json').read_text(encoding='utf-8-sig'))
    if old['population_sha256']!=new['population_sha256'] or old['days']!=new['days']:raise ValueError('Comparison workloads differ')
    objective_checks=[]
    for repeat in range(1,a.repeats+1):
        panel=[]
        for day in old['days']:
            left=json.loads((output/'v7'/f'generation-{repeat:04d}'/day/'receipt.json').read_text(encoding='utf-8-sig'))['metrics']
            right=json.loads((output/'v18'/f'generation-{repeat:04d}'/day/'receipt.json').read_text(encoding='utf-8-sig'))['metrics']
            for key in left:
                # V18 must preserve accounting exactly; do not relax for faster kernels.
                if left[key]!=right[key]:raise ValueError(f'Exact V7/V18 mismatch: {repeat}/{day}/{key}')
            panel.append(right)
        values=score(panel,[count(Individual.restore(m)) for m in saved['population']],Objective())
        objective_checks.append(dict(eligible=int(values['feasible'].sum()),positions=int(values['closed_positions'].sum())))
    old_warm=float(np.median(old['wall_seconds'][1:]));new_warm=float(np.median(new['wall_seconds'][1:]))
    result=dict(status='passed' if new_warm<old_warm else 'speed_gate_failed',source_sha256=source_hash(),
        population_sha256=new['population_sha256'],days=new['days'],exact_accounting_parity=True,capture_gib=a.capture_gib,
        v7_warm_seconds=old_warm,v17_warm_seconds=new_warm,speedup=old_warm/new_warm,
        full_training_panel=len(new['days'])==len(saved['contract']['input_receipts']),
        full_generation_with_validation_qualified=False,objective_checks=objective_checks,
        checkpoint_sha256=file_hash(a.campaign/'checkpoint.json'))
    write_json(output/'qualification.json',result)
    print(json.dumps(result),flush=True)
    return 0 if result['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
