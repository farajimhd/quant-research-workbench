"""Build successive immutable, disjoint-origin plans toward one byte budget."""

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('POLARS_MAX_THREADS','2')
os.environ.setdefault('OMP_NUM_THREADS','1')

from .shards.contract import digest,file_hash,write_json
from .quote_repair_v1 import verify_seal
from .phase2_ar_shards.contract import Geometry,BLOCK,BAR,ORIGIN
from .phase2_ar_shards.continuation import validate_parents,semantic_identity


def read(path):
    for attempt in range(20):
        try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except (PermissionError,json.JSONDecodeError):
            if attempt==19:raise
            time.sleep(.05)


def totals(roots):
    result=dict(bytes=0,train_bytes=0,validation_bytes=0,units=0,selected_origins=0)
    for root in roots:
        p=read(Path(root)/'result.json')
        if p.get('state') not in ('complete','inventory_exhausted','capacity_reached') or p.get('failure'):
            raise ValueError('Earlier plan is not successfully terminal')
        for key in result:result[key]+=p[key]
    return result


def prepare_plan(base, root, parents, revision, seed):
    """Copy SHA-verified frozen metadata, never requery or rewrite older shards."""
    import polars as pl
    from .phase2_ar_shards.inventory import verify_inventory
    from .phase2_ar_shards.source import describe
    base,root=Path(base),Path(root)
    original=read(base/'plan.json');verify_seal(original,'hash');verify_inventory(base,original)
    expected=[dict(root=str(Path(p).resolve()),binding=read(Path(p)/'plan.json')['hash']) for p in parents]
    if (root/'plan.json').exists():
        plan=read(root/'plan.json');verify_seal(plan,'hash');verify_inventory(root,plan)
        if plan.get('continuation',{}).get('parents')!=expected or plan['seed']!=seed:
            raise ValueError('Existing continuation plan has another ancestry')
        validate_parents(plan);return plan
    if root.exists() and any(root.iterdir()):
        # Only our sealed preparation marker may own interrupted metadata work.
        marker=read(root/'preparation.json')
        if marker!=dict(base_hash=original['hash'],parents=expected,seed=seed):
            raise ValueError('Foreign incomplete continuation root')
    root.mkdir(parents=True,exist_ok=True)
    write_json(root/'preparation.json',dict(base_hash=original['hash'],parents=expected,seed=seed))
    output=root/'inventory/plan';output.mkdir(parents=True,exist_ok=True)
    for part in original['partitions']:
        source=base/'inventory/plan'/part['name'];dest=output/part['name']
        if file_hash(source)!=part['sha256']:raise ValueError('Source inventory changed')
        if dest.exists():
            if file_hash(dest)!=part['sha256']:raise ValueError('Interrupted inventory copy changed')
        else:
            temporary=dest.with_suffix('.partial')
            with source.open('rb') as src,temporary.open('wb') as dst:
                shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
            if file_hash(temporary)!=part['sha256']:raise ValueError('Inventory temporary copy differs')
            temporary.replace(dest)
        if file_hash(dest)!=part['sha256']:raise ValueError('Inventory copy readback differs')
    units=pl.read_parquet(base/original['sample_units']['name'])
    # Existing validation is already too close to its whole-block byte limit.
    # Closed validation work is recorded rather than repeatedly queried/planned.
    closed_validation=units.filter(pl.col('split')==1).height
    units=units.filter(pl.col('split')==0)
    # A session with fewer source events than origins cannot yield a full block.
    # This is an exact upper bound, not an estimated trade-liquidity filter.
    bounds=(pl.scan_parquet([str(output/p['name']) for p in original['partitions']])
        .select('ticker','source_date','event_count')
        .with_columns(pl.col('source_date').str.slice(0,7).alias('month'))
        .group_by('ticker','month').agg(pl.col('event_count').max().alias('maximum_session_events'))
        .collect(engine='streaming'))
    units=units.join(bounds,on=['ticker','month'],how='left')
    if units['maximum_session_events'].null_count():raise ValueError('Month lacks frozen session bounds')
    excluded=units.filter(pl.col('maximum_session_events')<BLOCK)
    ep=root/'event-bound-exclusions.parquet';excluded.write_parquet(ep,compression='zstd')
    units=(units.filter(pl.col('maximum_session_events')>=BLOCK).drop('maximum_session_events')
        .with_columns(pl.struct('ticker','month').hash(seed=seed).alias('random_order')).sort('random_order'))
    path=root/'sample-units.parquet';units.write_parquet(path,compression='zstd')
    parent=Path(read(base/'provenance.json')['parent'])
    current,_=describe(parent,Geometry(tuple(original['geometry']['history']),original['geometry']['cutoff']))
    plan=copy.deepcopy(original);plan.pop('hash');plan.update(seed=seed,code=current['code'],
        sample_units=dict(name=path.name,sha256=file_hash(path),rows=len(units)),
        continuation=dict(version='disjoint-session-origin-ranks-v1',parents=expected,
            origin_policy='full frozen sessions minus previously published origin ranks',
            validation_units_closed=closed_validation,
            event_bound_exclusions=dict(name=ep.name,rows=len(excluded),sha256=file_hash(ep))))
    if semantic_identity(plan)!=semantic_identity(original):raise ValueError('Continuation changes semantic contract')
    plan['hash']=digest(plan);validate_parents(plan)
    write_json(output/'manifest.json',dict(jobs=plan['jobs'],partitions=plan['partitions'],hash=plan['hash']))
    write_json(root/'provenance.json',dict(source_revision=revision,parent=str(parent),binding=plan['hash'],campaign_parent=str(base)))
    write_json(root/'plan.json',plan)
    return plan


def snapshot(campaign, completed, active, args, state='running',failure=None):
    from .phase2_ar_shards.run import capacity_status
    base=totals(completed);p={};e={}
    if active is not None:
        if (active/'progress.json').exists():p=read(active/'progress.json')
        if (active/'execution.json').exists():e=read(active/'execution.json')
    aggregate={key:value+p.get(key,0) for key,value in base.items()}
    reserves={i:p.get('capacity',{}).get(name,{}).get('reserved_bytes',0) for i,name in ((0,'training'),(1,'validation'))}
    progress=dict(p,**aggregate,state=state,updated=time.time(),failure=failure,
        campaign_plan_number=len(completed)+int(active is not None),completed_plans=len(completed),
        campaign=True,capacity=capacity_status({0:aggregate['train_bytes'],1:aggregate['validation_bytes']},reserves,
            {0:args.target_bytes,1:args.validation_bytes},{0:0,1:0},{0:0,1:0},args.io_workers*2))
    execution=dict(e,workers=args.workers,io_workers=args.io_workers,target_bytes=args.target_bytes,
        validation_bytes=args.validation_bytes,worker_root=active.name if active else completed[-1].name,
        source_revision=args.source_revision)
    write_json(campaign/'execution.json',execution);write_json(campaign/'progress.json',progress)
    return progress


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-root',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase2-ar-sampled-v2'))
    parser.add_argument('--output',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase2-ar-campaign-v1'))
    parser.add_argument('--source-revision',required=True)
    parser.add_argument('--workers',type=int,default=32);parser.add_argument('--io-workers',type=int,default=8)
    parser.add_argument('--chunk-events',type=int,default=2_000_000)
    parser.add_argument('--query-memory-gib',type=float,default=1)
    parser.add_argument('--query-max-memory-gib',type=float,default=4)
    parser.add_argument('--engineering-proof',type=Path)
    parser.add_argument('--eligibility-cache')
    parser.add_argument('--target-bytes',type=int,default=4_000_000_000_000)
    parser.add_argument('--validation-bytes',type=int,default=250_000_000)
    parser.add_argument('--training-campaign',default='D:/TradingML/runtimes/bar_gpt/encoder-v2-zscore-all18-large-20261110')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args(argv);root=args.output.resolve();base=args.base_root.resolve()
    authority=Path('D:/TradingML/runtimes/bar_gpt').resolve()
    if not root.is_relative_to(authority) or root==base or root.parent!=base.parent:
        raise ValueError('Campaign and immutable base must be sibling native runtime roots')
    if (root/'STOP').exists() or (base/'STOP').exists():raise InterruptedError('Preserve human STOP')
    root.mkdir(exist_ok=True)
    import msvcrt
    lease=(root/'controller.lock').open('a+b');lease.write(b'0');lease.flush();lease.seek(0);msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
    stopped=False;child=None
    def stop(*_):
        nonlocal stopped;stopped=True
    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        original=read(base/'plan.json');verify_seal(original,'hash');done=[base]
        active=base.with_name(base.name+'-plan-0002')
        initial=totals(done)
        minimum=1_000_000+BLOCK*(BAR.itemsize+ORIGIN.itemsize)
        if args.validation_bytes-initial['validation_bytes']>=minimum:
            raise ValueError('Validation continuation requires an explicit shared-quota plan; current validation must already be closed')
        manifest=dict(version='bargpt-phase2-build-campaign-v1',base_root=str(base),base_binding=original['hash'],
            target_bytes=args.target_bytes,validation_bytes=args.validation_bytes,active_root=str(active),
            semantic_identity=semantic_identity(original),policy='disjoint origins, overlapping causal context only')
        manifest['hash']=digest(manifest)
        if (root/'campaign.json').exists() and read(root/'campaign.json')!=manifest:
            raise ValueError('Campaign budget or source ancestry changed')
        write_json(root/'campaign.json',manifest)
        snapshot(root,done,None,args,'preparing next plan')
        prepare_plan(base,active,done,args.source_revision,original['seed']+1)
        if args.prepare_only:return 0
        if (active/'STOP').exists():raise InterruptedError('Preserve continuation STOP')
        # A completed full uncovered pass exhausts every eligible unique origin.
        if (active/'result.json').exists() and read(active/'result.json')['state'] in ('complete','inventory_exhausted','capacity_reached'):
            child=None
        else:
            parent=read(active/'provenance.json')['parent']
            command=[sys.executable,'-B','-u','-m','research.bar_gpt.v5.phase2_ar_shards.run',
                '--execute','--source-revision',args.source_revision,'--output',str(active),'--parent',parent,
                '--seed',str(original['seed']+1),'--workers',str(args.workers),'--io-workers',str(args.io_workers),
                '--chunk-events',str(args.chunk_events),'--query-memory-gib',str(args.query_memory_gib),
                '--query-max-memory-gib',str(args.query_max_memory_gib),
                '--target-bytes',str(args.target_bytes-initial['train_bytes']),
                '--validation-bytes',str(max(1,args.validation_bytes-initial['validation_bytes'])),
                '--training-campaign',args.training_campaign]
            if args.engineering_proof:command.extend(['--engineering-proof',str(args.engineering_proof)])
            if args.eligibility_cache:command.extend(['--eligibility-cache',args.eligibility_cache])
            with (active/'controller.stdout.log').open('ab') as stdout,(active/'controller.stderr.log').open('ab') as stderr:
                child=subprocess.Popen(command,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
            import psutil
            write_json(root/'child.json',dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),command=command,output=str(active)))
        while child is not None and child.poll() is None:
            if (root/'STOP').exists() or (base/'STOP').exists():stopped=True
            if stopped and not (active/'STOP').exists():
                (active/'STOP').write_text('CAMPAIGN_STOP: preserve until explicit recovery')
            snapshot(root,done,active,args,'draining' if stopped else 'running')
            time.sleep(2)
        result=read(active/'result.json')
        if result.get('failure') or (child is not None and child.returncode):
            raise RuntimeError(result.get('failure') or f'Generator exited {child.returncode}')
        if result['state']=='stopped':snapshot(root,done,active,args,'stopped');return 0
        if result['state'] not in ('complete','inventory_exhausted','capacity_reached'):
            raise ValueError('Unexpected generator terminal state')
        final=snapshot(root,done,active,args,'capacity_reached' if result.get('capacity',{}).get('training',{}).get('blocked') else 'origin_coverage_exhausted')
        write_json(root/'result.json',final)
        return 0
    except Exception as error:
        snapshot(root,[base],None,args,'failed',f'{type(error).__name__}: {error}')
        raise
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
        lease.close()


if __name__=='__main__':raise SystemExit(main())
