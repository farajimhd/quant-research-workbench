"""Prepare an explicitly versioned solver continuation from a verified prefix.

Does not modify the original campaign or relax its code/runtime pins. Only the
Student-t numerical solver may differ. Existing books retain their original
checkpoint hashes and their source-code provenance in inherited_prefix.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import subprocess
import uuid

from research.level_book.v7 import campaign as c
from src.market_engine.reaction_center import SOLVER_VERSION

SOLVER_FILE='src/market_engine/reaction_center.py'
OLD_SOLVER_HASH='e40bbef6108de4eea9ec5330bdef5ddc703825b6312873a95c02a435c16ec2eb'


def verify_parent(plan):
    if plan.get('version')!=c.VERSION or plan.get('plan_hash')!=c.digest({k:v for k,v in plan.items() if k!='plan_hash'}):
        raise ValueError('Original plan integrity mismatch')
    current=c.hashes()
    changed={k for k in set(current)|set(plan['source_files']) if current.get(k)!=plan['source_files'].get(k)}
    if changed!={SOLVER_FILE} or plan['source_files'][SOLVER_FILE]!=OLD_SOLVER_HASH:
        raise ValueError('Recovery permits only the reviewed original-to-analytic solver change')
    if plan['software']!=dict(python=sys.version,numpy=c.np.__version__,scipy=c.scipy.__version__):
        raise ValueError('Pinned numerical runtime changed')


def verified_prefix(parent,plan,ticker):
    source=c.read(c.paths(parent,ticker)/'source-plan.json')
    if source['plan_hash']!=plan['plan_hash']:
        raise ValueError('Original source plan belongs to another campaign')
    root=c.paths(parent,ticker);previous=None;files=[];sessions=[]
    for metadata in source['days']:
        day=metadata['source_date'];path=root/'receipts'/f'{day}.json'
        if not path.exists():
            break
        receipt=c.read(path)
        if receipt['source_hash']!=c.source_hash(metadata,plan['rules']) or receipt['parent_hash']!=previous:
            raise ValueError('Inherited source/parent chain mismatch: '+day)
        if receipt['state']=='complete':
            book_path=root/'books'/f'{day}.json.gz';book=c.verified_book(book_path)
            if book['checkpoint_hash']!=receipt['checkpoint_hash'] or book['ticker']!=ticker or book['session']!=day:
                raise ValueError('Inherited checkpoint/receipt mismatch: '+day)
            previous=book['checkpoint_hash'];files.append(book_path)
        elif receipt['state']!='empty':
            raise ValueError('Invalid inherited receipt state: '+day)
        files.append(path);sessions.append(day)
    if not sessions or len(sessions)==len(source['days']):
        raise ValueError('Recovery requires a nonempty, unfinished verified prefix')
    if {p.stem for p in (root/'receipts').glob('*.json')}!=set(sessions):
        raise ValueError('Noncontiguous receipts; refusing to skip completed data')
    return source,files,sessions,previous


def copy_immutable(source,destination):
    raw=source.read_bytes()
    if destination.exists():
        if destination.read_bytes()!=raw:
            raise ValueError('Recovery copy differs: '+str(destination))
        return
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name('.'+destination.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    if destination.read_bytes()!=raw:
        raise ValueError('Recovery copy verification failed')


def prepare(parent,destination,ticker):
    parent=parent.resolve();destination=destination.resolve()
    if destination==parent or destination.is_relative_to(parent):
        raise ValueError('Recovery must be a separate campaign directory')
    plan=c.read(parent/'plan.json');verify_parent(plan)
    if (destination/'plan.json').exists():
        recovered=c.checked_plan(destination)
        if recovered.get('parent_plan_hash')!=plan['plan_hash'] or [r['ticker'] for r in recovered['rows']]!=[ticker]:
            raise ValueError('Existing recovery belongs to another campaign')
        print('Verified existing recovery:',destination,flush=True)
        return recovered
    row=next(r for r in plan['rows'] if r['ticker']==ticker)
    if row['status']=='deferred':
        raise ValueError('Identity-deferred instruments cannot use solver recovery')
    progress=c.read(c.paths(parent,ticker)/'progress.json')
    if progress['state']!='failed':
        raise ValueError('Original ticker must be stopped in failed state')
    with c.exclusive(c.paths(parent,ticker)/'worker.lock'):
        source,files,sessions,last_hash=verified_prefix(parent,plan,ticker)
        evidence=dict(solver_version=SOLVER_VERSION,parent_plan_hash=plan['plan_hash'],
            parent_source_files=plan['source_files'],inherited_sessions=sessions,
            last_inherited_checkpoint_hash=last_hash,
            policy='Verified old-solver prefix retained byte-for-byte; analytic solver applies only to subsequent sessions')
        recovered={k:v for k,v in plan.items() if k not in ('plan_hash','rows')}
        recovered.update(created_at=c.now(),rows=[dict(row,status='queued',reason='')],
            source_files=c.hashes(),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=c.REPO,text=True).strip(),
            parent_plan_hash=plan['plan_hash'],inherited_prefix=evidence)
        recovered['plan_hash']=c.digest(recovered)
        pending=destination/'recovery-plan.pending.json'
        if pending.exists():
            saved=c.read(pending)
            if (saved.get('plan_hash')!=c.digest({k:v for k,v in saved.items() if k!='plan_hash'})
                or saved.get('parent_plan_hash')!=plan['plan_hash'] or saved.get('source_files')!=c.hashes()
                or saved.get('inherited_prefix')!=evidence or saved.get('rows')!=recovered['rows']):
                raise ValueError('Pending recovery provenance changed')
            recovered=saved
        else:
            c.write(pending,recovered)
        target=c.paths(destination,ticker)
        for path in files:
            copy_immutable(path,target/path.relative_to(c.paths(parent,ticker)))
        c.write(target/'source-plan.json',dict(source,plan_hash=recovered['plan_hash']))
        c.write(target/'progress.json',dict(ticker=ticker,state='interrupted',stage='verified prefix inherited',
            completed=len(sessions),resumed=len(sessions),total=len(source['days']),
            session=sessions[-1],updated_at=recovered['created_at'],retried=0,reason=''))
        # Commit the new plan last, after the complete verified prefix is present.
        c.write(destination/'plan.json',recovered)
        c.checked_plan(destination)
    print(f'Ready: {ticker}, {len(sessions)} inherited sessions, {len(source["days"])-len(sessions)} remaining',flush=True)
    print('Recovery:',destination,flush=True)
    return recovered


def main():
    local=os.environ.get('COMPUTERNAME','').upper()=='DESKTOP-SAAI85T'
    base=Path('D:/TradingML/runtimes/level-book-v7') if local else c.WORKSTATION_RUNTIME_ROOT/'level-book-v7'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,default=base/'all-tradable-20250101-20260912-mle-v1')
    parser.add_argument('--runtime',type=Path,default=base/'urg-analytic-solver-recovery-v1')
    parser.add_argument('--ticker',default='URG')
    args=parser.parse_args()
    if not base.is_dir() or not args.runtime.resolve().is_relative_to(base.resolve()):
        parser.error('Recovery must use the available workstation level-book runtime root')
    with c.exclusive(args.runtime/'prepare.lock'):
        prepare(args.parent,args.runtime,args.ticker)


if __name__=='__main__':
    main()
