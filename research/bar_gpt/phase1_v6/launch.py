"""Hidden-window finite experiment launcher; explicit wall clock and campaign bounds."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys,time
from pathlib import Path
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--name',required=True);p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
    ledger=json.loads((a.root/'campaign.json').read_text())
    if time.time()>=ledger['deadline'] or len(ledger['experiments'])>=8:raise RuntimeError('Campaign limit reached')
    if (a.root/'STOP').exists():raise RuntimeError('Human STOP preserved')
    if any(e['name']==a.name for e in ledger['experiments']):raise RuntimeError('Identity already recorded; use explicit engineering resume')
    run=a.root/a.name
    ledger.update(state='running',current_experiment=a.name)
    record=dict(name=a.name,started=time.time(),state='running')
    ledger['experiments'].append(record);write_json(a.root/'campaign.json',ledger)
    args=a.args[1:] if a.args[:1]==['--'] else a.args
    command=[sys.executable.replace('pythonw.exe','python.exe'),'-B','-u','-m','research.bar_gpt.phase1_v6.train',
        '--data',str(a.root/'dataset'),'--output',str(run),*args]
    print(subprocess.list2cmdline(command),flush=True)
    with (a.root/(a.name+'.stdout.log')).open('w') as out,(a.root/(a.name+'.stderr.log')).open('w') as err:
        child=subprocess.Popen(command,stdout=out,stderr=err,cwd=Path(__file__).resolve().parents[3],env=dict(os.environ),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        import psutil
        write_json(a.root/(a.name+'.owner.json'),dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),command=command))
        # Hard ceiling includes startup; training reserves its own evaluation time.
        hard=min(record['started']+3540,ledger['deadline']-60)
        while child.poll() is None:
            if time.time()>hard or (a.root/'STOP').exists():
                if run.exists() and not (run/'STOP').exists():(run/'STOP').write_text('Controller deadline or campaign STOP')
                try:child.wait(timeout=45)
                except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
                break
            time.sleep(1)
        code=child.wait()
    result=json.loads((run/'results.json').read_text()) if (run/'results.json').exists() else {}
    record.update(ended=time.time(),exit_code=code,state=result.get('state','failed'),elapsed=time.time()-record['started'])
    ledger.update(state='awaiting analysis',latest_evaluation=result.get('evaluations',{}).get('development_full',{}).get('balanced_price_relative_mean'))
    write_json(a.root/'campaign.json',ledger)
    return code


if __name__=='__main__':raise SystemExit(main())
