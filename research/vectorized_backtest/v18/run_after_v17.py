"""Durable conditional queue; failed or interrupted V17 never triggers V18."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,sys,time,subprocess,psutil
from pathlib import Path
from .run_train import read_json,predecessor_complete
from .run_search import source_hash
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--job',type=Path,required=True);a=p.parse_args();root=require_runtime(a.job.parent)
    with owned_run(root,version='v18-after-successful-v17-v1'):
        while True:
            if (root/'STOP').exists():write_json(root/'status.json',dict(status='cancelled'));return 130
            job=read_json(a.job);active=read_json(job['predecessor'])
            if job['source_sha256']!=source_hash():raise ValueError('Queued code changed')
            if not predecessor_complete(active):status='waiting_for_v17_success'
            elif job.get('dataset_pending') or not job.get('training') or not job.get('validation'):status='waiting_for_expanded_dataset'
            elif not all(Path(job[k]).exists() for k in ('training','validation')):status='waiting_for_enriched_shards'
            else:break
            write_json(root/'status.json',dict(status=status,pid=os.getpid(),creation_time=psutil.Process().create_time(),updated_epoch=time.time(),predecessor=active['campaign']));time.sleep(30)
        command=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v18.qualify_v17','--training',job['training'],'--validation',job['validation'],'--output',job['qualification'],'--baseline-code',active['code'],'--predecessor',active['campaign']]
        write_json(root/'status.json',dict(status='qualifying',command=command,pid=os.getpid()))
        with (root/'qualification.log').open('w') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT).returncode
        if result:write_json(root/'status.json',dict(status='qualification_failed',exit_code=result));return result
        command=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v18.run_train','--training',job['training'],'--validation',job['validation'],'--output',job['campaign'],'--qualification',str(Path(job['qualification'])/'qualification.json'),'--predecessor',job['predecessor'],'--commit',job['commit'],'--generations',str(job.get('generations',50))]
        write_json(root/'status.json',dict(status='launching',command=command))
        with (root/'campaign.log').open('w') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT).returncode
        write_json(root/'status.json',dict(status='complete' if result==0 else 'campaign_failed',exit_code=result,campaign=job['campaign']));return result
if __name__=='__main__':raise SystemExit(main() or 0)
