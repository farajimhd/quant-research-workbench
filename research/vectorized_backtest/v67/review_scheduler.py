"""Exclusive, persistent review scheduler; never controls optimizer liveness.

Readiness is not approval: an agent must interpret evidence and use review_contract.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys,time
from pathlib import Path
import psutil
from .publication import write_json
from .progress_sessions import read_verified


def alive(worker):
    try:return abs(psutil.Process(worker['pid']).create_time()-worker['creation_time'])<.01
    except psutil.Error:return False


def resource_headroom():
    available=psutil.virtual_memory().available/2**30
    free=float(subprocess.check_output(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).splitlines()[0])/1024
    return dict(host_available_gib=available,gpu_free_gib=free,admissible=available>=80 and free>=40)


def serve(active_path,review_root,initial_packet=None):
    from .agent_review_packet import export
    from .review_backlog import reconcile
    from .review_cohort import prepare
    from .agent_intervention import policy
    import msvcrt
    review_root=Path(review_root);review_root.mkdir(parents=True,exist_ok=True)
    lock=(review_root/'scheduler.lock').open('a+b');lock.seek(0)
    if lock.read(1)==b'':lock.write(b'1');lock.flush()
    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    status_path=review_root/'scheduler-state.json'
    state=read_verified(status_path) if status_path.exists() else dict(runs=[],skipped_epochs=[])
    state.update(pid=os.getpid(),creation_time=psutil.Process().create_time(),status='running')
    children=[];first=initial_packet
    try:
        while not (review_root/'SCHEDULER_STOP').exists():
            active=read_verified(active_path);job_path=active['job'];job=read_verified(job_path)
            if job.get('review_window'):
                raise RuntimeError('Replay review scheduler retired: optimizer owns bounded evidence-review windows')
            root=Path(job['campaign'])/'phase-2/global';checkpoint=read_verified(root/'checkpoint.json')['state']
            state.update(completed_generation=checkpoint['completed'],updated_epoch=time.time())
            for run in state['runs']:
                if run['status']!='running':continue
                states=[read_verified(Path(run['out'])/f'shard-{i}'/'state.json') if (Path(run['out'])/f'shard-{i}'/'state.json').exists() else {} for i in range(4)]
                live=any(alive(w) for w in run['workers'])
                run['sessions_completed']=sum(len(s.get('completed',[])) for s in states)
                if all(s.get('status')=='complete' for s in states) and not live:
                    run.update(status='ready_for_interpretation',finished_epoch=time.time())
                elif not live:run.update(status='failed',errors=[s.get('error','worker exited without completion') for s in states if s.get('status')!='complete'])
            if any(r['status']=='failed' for r in state['runs']):
                state['status']='review_failure_requires_investigation';write_json(status_path,state);time.sleep(10);continue
            for run in state['runs']:
                if run['status']=='ready_for_interpretation' and not (Path(run['out'])/'combined-results.json').exists():
                    from .review_cohort_results import combine
                    combine(run['out'],job_path)
            if checkpoint['completed']>=300:
                state['status']='campaign_complete';write_json(status_path,state);break
            consumed={int(e) for e in checkpoint.get('agent_reviews',{})};interval=policy(job)['interval']
            newest=checkpoint['completed']//interval*interval
            scheduled={r['review_epoch'] for r in state['runs']}
            pending=[]
            for p in (root/'agent-reviews/inbox').glob('*.json'):
                v=read_verified(p)
                if v['review_epoch'] not in consumed:pending.append(v['review_epoch'])
            if not any(r['status']=='running' for r in state['runs']) and newest>=interval and newest not in consumed|scheduled and not pending:
                state['resources']=resource_headroom()
                if not state['resources']['admissible']:
                    state['status']='waiting_for_diagnostic_headroom';write_json(status_path,state);time.sleep(10);continue
                state['status']='running'
                # Freeze immediately after a sealed epoch; never wait for the chat heartbeat.
                packet=Path(first) if first else root/'agent-reviews'/f'epoch-{newest:04d}'
                first=None
                if not (packet/'packet.json').exists():export(root,job,checkpoint['completed'],packet)
                meta=read_verified(packet/'packet.json');epoch=meta['review_epoch']
                if epoch in consumed|scheduled:raise ValueError('Review already scheduled/consumed')
                reconcile(packet,root/'agent-reviews/backlog',job_path)
                out=review_root/f'epoch-{epoch:04d}';plan=prepare(packet,job_path,out)
                # Four lanes keep existing admission reserves; no optimizer process actions.
                workers=[]
                for i in range(4):
                    folder=out/f'shard-{i}';folder.mkdir(parents=True,exist_ok=True)
                    stdout=(folder/'stdout.log').open('ab');stderr=(folder/'stderr.log').open('ab')
                    command=[sys.executable,'-B','-m','research.vectorized_backtest.v67.review_cohort','run','--job',job_path,
                        '--plan',str(out/'plan.json'),'--out',str(folder),'--shard-index',str(i),'--shard-count','4']
                    process=subprocess.Popen(command,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW,
                        env={**os.environ,'POLARS_MAX_THREADS':'2','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
                    stdout.close();stderr.close();children.append(process)
                    workers.append(dict(pid=process.pid,creation_time=psutil.Process(process.pid).create_time(),command=command))
                last=max(consumed|scheduled|{0})
                state['skipped_epochs']=sorted(set(state['skipped_epochs'])|set(range(last+interval,epoch,interval)))
                state['runs'].append(dict(review_epoch=epoch,source_generation=meta['source_generation'],out=str(out),
                    workers=workers,status='running',started_epoch=time.time(),parents=len(plan['records']),offspring=len(plan['members'])-len(plan['records'])))
            write_json(status_path,state)
            children=[p for p in children if p.poll() is None]
            time.sleep(10)
    finally:
        # Explicit scheduler stop requests graceful boundaries for its own lanes.
        if (review_root/'SCHEDULER_STOP').exists():
            for run in state['runs']:
                if run['status']=='running':(Path(run['out'])/'STOP').write_text('Review scheduler stop; optimizer unaffected')
            for child in children:child.wait()
        lock.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--active-run',required=True);p.add_argument('--out',required=True);p.add_argument('--initial-packet')
    a=p.parse_args();serve(a.active_run,a.out,a.initial_packet)
