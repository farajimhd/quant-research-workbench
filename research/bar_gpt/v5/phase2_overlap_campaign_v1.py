"""New immutable overlap plans; old completed/stopped roots remain read-only."""
import signal
import subprocess
import sys
import time
from pathlib import Path
import psutil

from .phase2_build_campaign_v1 import read, prepare_plan, snapshot, totals
from .shards.contract import digest, write_json
from .phase2_ar_shards.overlap import POLICY


def run(args):
    import msvcrt
    root=args.output.resolve();base=args.base_root.resolve()
    if (root/'STOP').exists():raise InterruptedError('Preserve campaign STOP')
    if not args.prior_root:raise ValueError('Name all completed prior plans explicitly')
    parents=[base]+[p.resolve() for p in args.prior_root]
    if len(set(parents))!=len(parents) or any(p.parent!=root.parent or p==root for p in parents):
        raise ValueError('Invalid immutable parent roots')
    totals(parents)  # Terminal success required; old STOPs stay untouched.
    root.mkdir(exist_ok=True)
    lease=(root/'controller.lock').open('a+b');lease.write(b'0');lease.flush();lease.seek(0);msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
    owner=psutil.Process()
    from .run_phase2_memory_priority import set_memory_priority
    policy=set_memory_priority(owner.pid,owner.create_time(),psutil.virtual_memory().available,
                               0,64*1024**3,apply=True,normalize_io=True,controller=True)
    write_json(root/'coordinator-resource-policy.json',policy)
    identity=dict(policy=POLICY,stride=args.overlap_stride,parents=[dict(root=str(p),binding=read(p/'plan.json')['hash']) for p in parents],
                  target_bytes=args.target_bytes,validation_bytes=args.validation_bytes)
    identity['hash']=digest(identity)
    if (root/'contract.json').exists() and read(root/'contract.json')!=identity:
        raise ValueError('Overlap campaign contract changed')
    write_json(root/'contract.json',identity)
    stopped=False
    def stop(*_):
        nonlocal stopped;stopped=True
    handlers={sig:signal.signal(sig,stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        for number in range(1,257):
            initial=totals(parents)
            if args.target_bytes-initial['train_bytes']<2_000_000:
                write_json(root/'result.json',snapshot(root,parents,None,args,'capacity_reached'));return 0
            active=base.with_name(base.name+f'-overlap-{number:04d}')
            write_json(root/'campaign.json',dict(identity,base_root=str(base),active_root=str(active),plan_number=number))
            snapshot(root,parents,None,args,'preparing overlap plan')
            seed=read(base/'plan.json')['seed']+1000+number
            prepare_plan(base,active,parents,args.source_revision,seed,overlap_stride=args.overlap_stride)
            if args.prepare_only:return 0
            if stopped or (root/'STOP').exists():return 0
            if (active/'STOP').exists():raise InterruptedError('Preserve overlap plan STOP')
            terminal=read(active/'result.json') if (active/'result.json').exists() else {}
            if terminal.get('state') not in ('complete','inventory_exhausted','capacity_reached'):
                command=[sys.executable,'-B','-u','-m','research.bar_gpt.v5.phase2_ar_shards.run','--execute',
                    '--source-revision',args.source_revision,'--output',str(active),'--parent',read(base/'provenance.json')['parent'],
                    '--seed',str(seed),'--workers',str(args.workers),'--io-workers',str(args.io_workers),
                    '--query-memory-gib',str(args.query_memory_gib),'--query-max-memory-gib',str(args.query_max_memory_gib),
                    '--target-bytes',str(args.target_bytes-initial['train_bytes']),'--validation-bytes','1',
                    '--training-campaign',args.training_campaign]
                with (active/'controller.stdout.log').open('ab') as out,(active/'controller.stderr.log').open('ab') as err:
                    child=subprocess.Popen(command,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
                write_json(root/'child.json',dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),command=command,output=str(active)))
                while child.poll() is None:
                    if (root/'STOP').exists():stopped=True
                    if stopped and not (active/'STOP').exists():(active/'STOP').write_text('OVERLAP_CAMPAIGN_STOP')
                    snapshot(root,parents,active,args,'draining' if stopped else 'running');time.sleep(2)
                terminal=read(active/'result.json')
                if child.returncode or terminal.get('failure'):raise RuntimeError(terminal.get('failure') or child.returncode)
            if terminal.get('state')=='stopped':snapshot(root,parents,active,args,'stopped');return 0
            if terminal.get('state') not in ('complete','inventory_exhausted','capacity_reached') or terminal.get('failure'):
                raise ValueError('Overlap plan did not finish successfully')
            if terminal.get('capacity',{}).get('training',{}).get('blocked'):
                write_json(root/'result.json',snapshot(root,parents,active,args,'capacity_reached'));return 0
            parents.append(active)
        write_json(root/'result.json',snapshot(root,parents,None,args,'overlap_offsets_exhausted'))
        return 0
    finally:
        for sig,handler in handlers.items():signal.signal(sig,handler)
        lease.close()
