"""Launch an immutable, qualified V18 campaign with complete safe defaults."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,sys,time,psutil
from pathlib import Path
from .run_search import main as search,source_hash,bind_dataset
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash


def read_json(path):
    # Legacy PowerShell pointers may carry UTF-8/UTF-16 BOMs. json.loads(bytes)
    # detects these encodings while retaining strict malformed-input rejection.
    return json.loads(Path(path).read_bytes())


def predecessor_complete(active):
    c=Path(active['campaign']);checkpoint=read_json(c/'checkpoint.json');target=checkpoint['contract']['generations']
    if checkpoint['state']['completed']!=target or not (c/'exit.json').exists() or read_json(c/'exit.json')['exit_code']!=0 or (c/'owner.lock').exists():return False
    for role in ('training','validation'):
        if not (c/role/f'generation-{target:04d}'/'complete.json').exists():return False
    try:
        if abs(psutil.Process(active['pid']).create_time()-active['creation_time'])<.01:return False
    except psutil.NoSuchProcess:pass
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('training','validation','output','qualification','predecessor'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--generations',type=int,default=50)
    a=p.parse_args();root=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v18')
    out=require_runtime(a.output)
    if not out.is_relative_to(root):raise ValueError('V18 campaign must use its own runtime root')
    q=read_json(a.qualification);active=read_json(a.predecessor)
    if not predecessor_complete(active):raise ValueError('V17 has not completed successfully')
    if (q['status']!='passed' or q['source_sha256']!=source_hash() or not q['exact_accounting_parity']
            or not q.get('protected_native_generation_qualified',False)
            or not q['full_generation_with_validation_qualified'] or q['maximum_ratio']!=1.0
            or max(q['full_generation_ratio'],q['matched_accounting_ratio'])>1.0):raise ValueError('Strict V17 speed/accounting qualification required')
    if file_hash(Path(active['campaign'])/'checkpoint.json')!=q['predecessor_checkpoint_sha256']:raise ValueError('Predecessor changed')
    for role,path in (('training',a.training),('validation',a.validation)):
        if file_hash(path)!=q[role+'_sha256']:raise ValueError('Qualified manifest changed')
    for version in range(7,19):
        pointer=root.parent/f'torch_backtest_v{version}/active-campaign.json'
        if pointer.exists():
            old=read_json(pointer)
            if 'creation_time' not in old:continue
            try:
                process=psutil.Process(old['pid'])
                if abs(process.create_time()-old['creation_time'])<.01 and process.pid!=os.getpid():raise RuntimeError(f'Optimizer V{version} already alive')
            except psutil.NoSuchProcess:pass
    for path,role in ((a.training,'training'),(a.validation,'validation')):
        write_json(out/(role+'.json'),bind_dataset(path,role))
    argv=['--training',str(out/'training.json'),'--validation',str(out/'validation.json'),'--output',str(out),
        '--population','1024','--batch-size','512','--session-workers','2','--generations',str(a.generations),'--seed','182237',
        '--capture-gib','4','--maximum-gate-gib','8']
    record=dict(pid=os.getpid(),creation_time=psutil.Process().create_time(),code=str(Path(__file__).resolve().parents[3]),commit=a.commit,
        campaign=str(out),source_sha256=source_hash(),arguments=argv,qualification_sha256=file_hash(a.qualification),
        predecessor_sha256=file_hash(a.predecessor),validation_feedback=False,initialization='random-only-v1')
    write_json(out/'active.json',record);write_json(root/'active-campaign.json',record)
    print('COMMAND',sys.executable,'-B -u -m research.vectorized_backtest.v18.run_search',' '.join(argv),flush=True)
    result=1
    try:
        sys.argv=['v18.run_search',*argv];result=search() or 0
        return result
    finally:write_json(out/'exit.json',dict(exit_code=result,finished_epoch=time.time()))


if __name__=='__main__':raise SystemExit(main())
