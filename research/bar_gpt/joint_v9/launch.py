"""One finite owned GPU experiment, no scheduled training automation."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys,time
from pathlib import Path
import psutil
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--name',required=True)
    p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args();ledger=json.loads((a.root/'campaign.json').read_text())
    if (a.root/'STOP').exists():raise RuntimeError('Human STOP preserved')
    if time.time()>ledger['stage_deadline']:raise RuntimeError('Research stage deadline; review evidence before extending')
    if any(r['name']==a.name for r in ledger['experiments']):raise RuntimeError('Existing identity requires exact explicit engineering resume')
    record=dict(name=a.name,started=time.time(),state='running');ledger['experiments'].append(record)
    ledger.update(state='running',current_experiment=a.name);write_json(a.root/'campaign.json',ledger)
    args=a.args[1:] if a.args[:1]==['--'] else a.args
    command=[sys.executable,'-B','-u','-m','research.bar_gpt.joint_v9.train','--data',str(a.root/'dataset'),'--output',str(a.root/a.name),*args]
    print(subprocess.list2cmdline(command),flush=True)
    with (a.root/(a.name+'.stdout.log')).open('w') as out,(a.root/(a.name+'.stderr.log')).open('w') as err:
        child=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[3],env=dict(os.environ),stdout=out,stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        write_json(a.root/(a.name+'.owner.json'),dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),command=command))
        while child.poll() is None:
            if time.time()>min(record['started']+3540,ledger['stage_deadline']-60) or (a.root/'STOP').exists():
                stop=a.root/a.name/'STOP'
                if stop.parent.exists() and not stop.exists():stop.write_text('Controller deadline; preserve checkpoint')
                try:child.wait(timeout=45)
                except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
                break
            time.sleep(1)
    result=json.loads((a.root/a.name/'results.json').read_text()) if (a.root/a.name/'results.json').exists() else {}
    record.update(state=result.get('state','failed'),exit_code=child.returncode,ended=time.time(),elapsed=time.time()-record['started'])
    ledger['state']='awaiting evidence review';write_json(a.root/'campaign.json',ledger);return child.returncode


if __name__=='__main__':raise SystemExit(main())
