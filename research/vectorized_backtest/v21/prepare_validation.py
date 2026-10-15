"""Prepare authorized display-only validation after V7 releases workstation."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,subprocess,sys
from pathlib import Path
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def require_finished(campaign,stopped_receipt=None):
    campaign=Path(campaign)
    status=json.loads((campaign/'status.json').read_text(encoding='utf-8-sig'))
    checkpoint=json.loads((campaign/'checkpoint.json').read_text(encoding='utf-8-sig'))
    import psutil
    if stopped_receipt is not None:
        receipt=json.loads(Path(stopped_receipt).read_text(encoding='utf-8-sig'))
        generation=checkpoint['completed_generations'];seal=campaign/f'generation-{generation:04d}'/'complete.json'
        if (receipt.get('version')!='v8-authorized-v7-stop-v1' or Path(receipt['campaign']).resolve()!=campaign.resolve()
                or receipt['checkpoint_sha256']!=file_hash(campaign/'checkpoint.json') or receipt['generation']!=generation
                or generation<1 or receipt['seal_sha256']!=file_hash(seal) or status.get('status')!='interrupted'):
            raise RuntimeError('Authorized stopped checkpoint identity mismatch')
        sealed=json.loads(seal.read_text(encoding='utf-8-sig'))
        if set(sealed['session_receipts'])!=set(checkpoint['contract']['input_receipts']):raise RuntimeError('Incomplete sealed panel')
        for day,h in sealed['session_receipts'].items():
            if file_hash(seal.parent/day/'receipt.json')!=h:raise RuntimeError('Stopped session receipt changed')
        for owner in receipt['processes']:
            try:
                if abs(psutil.Process(owner['pid']).create_time()-owner['created'])<.01:raise RuntimeError('Stopped V7 process still alive')
            except psutil.NoSuchProcess:pass
    elif status.get('status')!='complete' or checkpoint['completed_generations']!=checkpoint['contract']['generations']:
        raise RuntimeError('Wait for V7 to complete before workstation work')
    active=json.loads((campaign/'active.json').read_text(encoding='utf-8-sig'))
    pid=active.get('pid');created=active.get('creation_time')
    if pid and psutil.pid_exists(pid) and (created is None or abs(psutil.Process(pid).create_time()-created)<.01):
        raise RuntimeError('V7 worker is still alive')
    if (campaign/'owner.lock').exists() or (campaign/'controller.lock').exists():
        raise RuntimeError('V7 ownership has not been released')


def bind_manifest(inputs,history,days,role,path,feature_cache=None):
    from .sparse_replay import verify_sparse_receipt
    receipts={}
    for day in days:
        record=verify_sparse_receipt(Path(inputs)/day)
        actual='validation' if record.get('validation_opened') else 'training'
        if actual!=role or record['identity']['session']['day']!=day:raise ValueError('Split/day binding mismatch')
        history_record=json.loads((Path(history)/day/'complete.json').read_text(encoding='utf-8-sig'))
        if history_record.get('status')!='complete' or history_record.get('input_receipt_sha256')!=file_hash(Path(inputs)/day/'complete.json'):
            raise ValueError('History does not bind input')
        receipts[day]={k:file_hash(Path(root)/day/'complete.json') for k,root in [('inputs',inputs),('history',history)]}
    value=dict(version='v8-split-manifest-v1',role=role,days=days,inputs=str(inputs),history=str(history),receipts=receipts)
    if feature_cache:value['feature_cache']=str(feature_cache)
    write_json(path,value)
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('v7-campaign','sessions','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--workers',type=int,default=8)
    p.add_argument('--stopped-v7-receipt',type=Path)
    p.add_argument('--ranking-before-features',action='store_true')
    a=p.parse_args();require_finished(a.v7_campaign,a.stopped_v7_receipt)
    if not 1<=a.workers<=8:raise ValueError('Bounded preparation workers 1..8 required')
    root=require_runtime(a.output);spec=json.loads(a.sessions.read_text(encoding='utf-8-sig'))
    spec['validation_mode']='display_only'
    paths={}
    for item in spec['validation']:
        day=item['day'];old=item['feature_root'];paths[old]=str(root/'features'/day)
        item['feature_root']=paths[old];item['identity_map']=str(root/'features'/day/'identity_map.json')
        item['split_certificate']=str(root/'split_evidence'/(day+'.json'))
        item['execution_root']=str(root/'unused-broker-input'/day)
    for item in spec['validation']:
        prior=item.get('previous_feature_root')
        if prior in paths:
            item['previous_feature_root']=paths[prior]
            item['previous_split_certificate']=str(root/'split_evidence'/(Path(prior).name+'.json'))
    sessions=root/'sessions.json';authorization=root/'display-authorization.json'
    if sessions.exists() and json.loads(sessions.read_text(encoding='utf-8-sig'))!=spec:raise ValueError('Validation preparation plan changed')
    write_json(sessions,spec)
    write_json(authorization,dict(version='v8-validation-display-authorization-v1',sessions_sha256=file_hash(sessions),selection_feedback=False))
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    def execute(module,arguments,log):
        command=[sys.executable,'-B','-u','-m','research.vectorized_backtest.v21.'+module,*map(str,arguments)]
        print(' '.join(command),flush=True)
        with (root/(log+'.log')).open('a',encoding='utf-8') as stream:
            subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
    days=[v['day'] for v in spec['validation']]
    scope_arguments=[]
    if a.ranking_before_features:
        execute('materialize',['--sessions',sessions,'--output',root/'market','--workers',2,
            '--ranking-before-features'],'market')
        from .opening_basis import feature_scope
        write_json(root/'feature-scope.json',feature_scope(root/'market',days,file_hash(sessions)))
        scope_arguments=['--listing-scope',root/'feature-scope.json']
    for item in spec['validation']:
        day=item['day'];previous=Path(item['previous_feature_root']).name
        write_json(root/'status.json',dict(stage='Features and split evidence',day=day,completed=days.index(day),total=len(days),validation_feedback=False))
        execute('extract_features',['--sessions',sessions,'--display-authorization',authorization,
            '--manifest',item['source_manifest'],'--ledger',item['source_ledger'],'--date',day,
            '--previous-date',previous,'--output',item['feature_root'],'--workers',a.workers,*scope_arguments],day+'-features')
        execute('certify_splits',['--sessions',sessions,'--display-authorization',authorization,'--date',day],day+'-splits')
    if not a.ranking_before_features:
        execute('materialize',['--sessions',sessions,'--output',root/'market','--workers',2], 'market')
    execute('compact_prepare',['--sessions',sessions,'--market-roots',root/'market','--output',root/'compact','--workers',2], 'compact')
    for day in days:
        if (root/'history'/day/'complete.json').exists():
            from .data import SessionData
            data=SessionData(root/'compact'/day,root/'history'/day);data.close()
        else:execute('materialize_history',['--inputs',root/'compact'/day,'--output',root/'history'/day,'--workers',a.workers],day+'-history')
    bind_manifest(root/'compact',root/'history',days,'validation',root/'validation.json')
    write_json(root/'status.json',dict(status='complete',stage='Validation inputs certified',completed=len(days),total=len(days),validation_feedback=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
