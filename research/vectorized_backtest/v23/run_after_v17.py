"""Verified feature-ready handoff and protected first-generation monitoring."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,sys,time,subprocess,psutil,math
from pathlib import Path
from .run_train import read_json,predecessor_complete,predecessor_ready
from .run_search import source_hash,bind_dataset
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run


def features_ready(job):
    if job.get('dataset_pending') or not job.get('training') or not job.get('validation'):return False
    if not all(Path(job[k]).exists() for k in ('training','validation')):return False
    from .prepare_enriched import verify
    datasets={role:bind_dataset(job[role],role) for role in ('training','validation')}
    if max(datasets['training']['days'])>=min(datasets['validation']['days']):raise ValueError('Handoff split overlap')
    for role,dataset in datasets.items():
        for day in dataset['days']:verify(Path(dataset['enriched_root'])/day,dataset['receipts'][day]['inputs'],role)
    return True


def request_handoff(active,root):
    """Only invoked after the explicit job authorization and verified features."""
    c=Path(active['campaign']);request=root/'handoff-request.json'
    if not request.exists():
        processes=[]
        try:
            controller=psutil.Process(active['pid'])
            if abs(controller.create_time()-active['creation_time'])>=.01:raise ValueError('V18 controller identity changed')
            processes=[dict(pid=p.pid,creation_time=p.create_time()) for p in [controller,*controller.children(recursive=True)]]
        except psutil.NoSuchProcess:
            raise ValueError('V18 exited without a verified successful completion or authorized handoff')
        write_json(request,dict(authorization='user-requested-v23-ready-handoff',campaign=active['campaign'],processes=processes))
        (c/'STOP').write_text('User requested V23 handoff after certified features became ready\n')
    requested=read_json(request)
    if requested['campaign']!=active['campaign']:raise ValueError('Handoff controller changed')
    for owner in requested['processes']:
        try:
            if abs(psutil.Process(owner['pid']).create_time()-owner['creation_time'])<.01:return None
        except psutil.NoSuchProcess:pass
    generation=read_json(c/'checkpoint.json')['state']['completed'];proof=root/'predecessor-stop.json'
    record=dict(requested,generation=generation,active_sha256=file_hash(c/'active.json'),checkpoint_sha256=file_hash(c/'checkpoint.json'))
    for role in ('training','validation'):record[role+'_seal_sha256']=file_hash(c/role/f'generation-{generation:04d}'/'complete.json')
    write_json(proof,record)
    if not predecessor_ready(active,proof):raise ValueError('V18 did not stop at a valid sealed generation boundary')
    return proof


def inspect_first_generation(campaign,qualification):
    c=Path(campaign);g='generation-0001'
    if not all((c/role/g/'complete.json').exists() for role in ('training','validation')) or not (c/'training'/g/'wall.json').exists():return None
    rows=read_json(c/'training'/g/'ranking.json')
    if not rows:return dict(status='failed',reason='Empty first-generation training ranking')
    for row in rows:
        m=row['metrics']
        if not row['valid'] or not all(math.isfinite(m[k]) for k in ('total_pnl','maximum_position_risk','maximum_stop_distance')) or m['maximum_position_risk']>300.+1e-7 or m['maximum_stop_distance']>.20+1e-12:
            return dict(status='failed',reason='First-generation financial/risk invariants failed')
    wall=read_json(c/'training'/g/'wall.json')['full_generation_seconds']
    baseline=read_json(qualification)['full_generation_seconds']['v18'][0]
    if not math.isfinite(wall) or not math.isfinite(baseline) or baseline<=0:return dict(status='failed',reason='Invalid first-generation timing')
    ratio=wall/baseline
    return dict(status='passed' if ratio<=1.0 else 'failed',full_generation_seconds=wall,v18_cold_generation_seconds=baseline,ratio=ratio,maximum_ratio=1.0,scope='same bound dataset and production sizing; complete cold generation including selection and validation')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--job',type=Path,required=True);a=p.parse_args();root=require_runtime(a.job.parent)
    with owned_run(root,version='v23-after-sealed-v18-v1'):
        while True:
            if (root/'STOP').exists():write_json(root/'status.json',dict(status='cancelled'));return 130
            job=read_json(a.job);active=read_json(job['predecessor'])
            if job['source_sha256']!=source_hash():raise ValueError('Queued code changed')
            stop_proof=None
            if job.get('stop_when_features_ready'):
                if not features_ready(job):status='waiting_for_expanded_dataset' if job.get('dataset_pending') else 'waiting_for_enriched_shards'
                elif predecessor_complete(active):break
                else:
                    stop_proof=request_handoff(active,root)
                    if stop_proof:break
                    status='waiting_for_v18_sealed_handoff'
            elif not predecessor_complete(active):status='waiting_for_v18_success'
            elif not features_ready(job):status='waiting_for_expanded_dataset' if job.get('dataset_pending') else 'waiting_for_enriched_shards'
            else:break
            write_json(root/'status.json',dict(status=status,pid=os.getpid(),creation_time=psutil.Process().create_time(),updated_epoch=time.time(),predecessor=active['campaign']));time.sleep(30)
        command=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v23.qualify_v17','--training',job['training'],'--validation',job['validation'],'--output',job['qualification'],'--baseline-code',active['code'],'--predecessor',active['campaign']]
        if job.get('reuse_baseline'):command+=['--reuse-baseline',job['reuse_baseline']]
        write_json(root/'status.json',dict(status='qualifying',command=command,pid=os.getpid()))
        with (root/'qualification.log').open('w') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT).returncode
        if result:write_json(root/'status.json',dict(status='qualification_failed',exit_code=result));return result
        command=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v23.run_train','--training',job['training'],'--validation',job['validation'],'--output',job['campaign'],'--qualification',str(Path(job['qualification'])/'qualification.json'),'--predecessor',job['predecessor'],'--commit',job['commit'],'--generations',str(job.get('generations',50))]
        if stop_proof:command+=['--predecessor-stop',str(stop_proof)]
        write_json(root/'status.json',dict(status='launching',command=command))
        with (root/'campaign.log').open('w') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT);first=None
            while process.poll() is None:
                if first is None:
                    first=inspect_first_generation(job['campaign'],Path(job['qualification'])/'qualification.json')
                    if first is not None:
                        write_json(root/'first-generation.json',first)
                        if first['status']!='passed':(Path(job['campaign'])/'STOP').write_text('First-generation qualification failed; stop at sealed boundary for diagnosis\n')
                time.sleep(5)
            result=process.returncode
        if first is None:
            first=inspect_first_generation(job['campaign'],Path(job['qualification'])/'qualification.json')
            if first is not None:write_json(root/'first-generation.json',first)
        if first is None or first['status']!='passed':write_json(root/'status.json',dict(status='first_generation_failed',exit_code=result,campaign=job['campaign']));return result or 2
        write_json(root/'status.json',dict(status='complete' if result==0 else 'campaign_failed',exit_code=result,campaign=job['campaign']));return result
if __name__=='__main__':raise SystemExit(main() or 0)
