"""Current V6 label authority: all saved bank listings, no legacy targets.

Immutable bounded listing shards are certified before atomic publication.
Only decoded certified OHLC/MACD are consumed; candidate tables are never read.
"""
from dataclasses import asdict, replace
from datetime import date
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import json
import os
from pathlib import Path
import time

import numpy as np
import polars as pl

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.price_action_opportunities import Config, calculate, VERSION as ALGORITHM
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.bank import open_bank
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT, CONTEXT_ONLY, role

VERSION = 'rl-v6-swing-opportunity-dataset-v2'
DAY_VERSION = 'rl-v6-swing-opportunity-shards-v2'
STATUS = 'certified_swing_opportunities'
FILES = ('labels', 'episodes', 'pairs', 'trades')


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def verify_shard(root, binding=None):
    root = Path(root)
    proof = json.loads((root/'complete.json').read_text())
    if (proof.get('version') != DAY_VERSION or proof.get('algorithm') != ALGORITHM or
        proof.get('status') != STATUS or (binding is not None and proof.get('binding') != binding)):
        raise ValueError('Obsolete or mismatched opportunity shard')
    for name in FILES:
        record = proof['files'][name]; path = root/(name+'.parquet')
        if record['sha256'] != file_hash(path) or pl.scan_parquet(path).select(pl.len()).collect().item() != record['rows']:
            raise ValueError('Opportunity shard bytes/count changed')
    if proof['activity_rows'] != proof['valid_rows']+proof['invalid_price_rows']:
        raise ValueError('Source row accounting does not reconcile')
    return proof


def decoded_bars(values):
    raw = values.scalar
    valid = (raw[:,35] == 1) & (raw[:,36] == 1)
    if np.any(raw[valid, SCALAR_NAMES.index('indicator_available')] != 1):
        raise ValueError('Valid price candle lacks certified MACD')
    data = {'time_us': values.close_us[valid]}
    for name in ('open','high','low','close'):
        data[name] = np.exp(raw[valid, SCALAR_NAMES.index('log_'+name)].astype(np.float64))
    for name in ('line','signal'):
        data['macd_'+name] = raw[valid, SCALAR_NAMES.index('macd_'+name+'_rel')].astype(np.float64)*data['close']
    return pl.DataFrame(data), int((~valid).sum())


def build_shard(task):
    bank_root, output, identities, binding, config = task
    output = Path(output)
    if (output/'complete.json').is_file():
        return verify_shard(output, binding)
    # Parent verifies all bank bytes once per day before dispatching workers.
    # Every worker checks the pinned certificate; memmaps are read-only.
    if file_hash(Path(bank_root)/'complete.json') != binding['bank_manifest_sha256']:
        raise ValueError('Bank certificate changed during campaign')
    bank = open_bank(Path(bank_root), verify_hashes=False)
    products = {name:[] for name in FILES}
    activity = invalid = 0; empty = []
    for identity in identities:
        values = bank.listing(identity); activity += len(values.close_us)
        bars, rejected = decoded_bars(values); invalid += rejected
        if not bars.height:
            empty.append(identity); continue
        frames = calculate(bars, Config(**config))
        for name, frame in zip(FILES, frames):
            if frame.width:
                products[name].append(frame.with_columns(pl.lit(identity).alias('listing_id')))
    # Explicit empty schemas make all-invalid/all-short shards readable.
    sample = pl.DataFrame(dict(time_us=[1_000_000],open=[1.],high=[1.],low=[1.],close=[1.],macd_line=[1.],macd_signal=[0.]))
    templates = dict(zip(FILES, calculate(sample, Config(**config))))
    output.mkdir(parents=True, exist_ok=True); files = {}
    for name in FILES:
        frames = products[name]
        frame = pl.concat(frames, how='diagonal_relaxed') if frames else templates[name].head(0).with_columns(pl.lit(None,dtype=pl.String).alias('listing_id'))
        if name == 'labels':
            if frame.height != activity-invalid or frame.select('listing_id','time_us').n_unique() != frame.height:
                raise ValueError('Labels do not cover every valid source candle exactly once')
            if frame.filter(~pl.col('label_value').is_between(0,1) | ~pl.col('entry_gain').is_finite()).height:
                raise ValueError('Invalid raw/normalized target')
        path = output/(name+'.parquet'); frame.write_parquet(path)
        files[name] = dict(sha256=file_hash(path), rows=frame.height)
    proof = dict(version=DAY_VERSION, algorithm=ALGORITHM, status=STATUS, binding=binding,
        identities=identities, files=files, activity_rows=activity, valid_rows=activity-invalid,
        invalid_price_rows=invalid, all_invalid_listings=empty)
    write_json(output/'complete.json', proof)
    return proof


def verify_day(root, bank_hash=None, *, verify_files=True):
    root = Path(root); proof = json.loads((root/'complete.json').read_text())
    if (proof.get('version') != DAY_VERSION or proof.get('algorithm') != ALGORITHM or
        proof.get('status') != STATUS or proof.get('sealed_test_accessed') is not False or
        (bank_hash is not None and proof['bank_certificate_sha256'] != bank_hash)):
        raise ValueError('Old labels cannot be used by current V6')
    seen = []; totals = dict(activity_rows=0,valid_rows=0,invalid_price_rows=0)
    for shard in proof['shards']:
        folder = root/shard['path']
        if not folder.resolve().is_relative_to(root.resolve()) or file_hash(folder/'complete.json') != shard['sha256']:
            raise ValueError('Shard receipt escaped root or changed')
        receipt = verify_shard(folder, proof['binding']) if verify_files else json.loads((folder/'complete.json').read_text())
        seen.extend(receipt['identities'])
        for key in totals: totals[key] += receipt[key]
    if seen != proof['identities'] or len(set(seen)) != len(seen) or any(proof[k] != v for k,v in totals.items()):
        raise ValueError('Day label population/count mismatch')
    return proof


def require_dataset(path, *, runtime_root):
    path, runtime = Path(path).resolve(), Path(runtime_root).resolve()
    if not path.is_relative_to(runtime): raise ValueError('Dataset escaped runtime')
    data = json.loads(path.read_text())
    if (data.get('version') != VERSION or data.get('algorithm') != ALGORITHM or
        data.get('status') != 'audited_ready_for_training' or data.get('sealed_test_accessed') is not False or
        data.get('hash') != digest({k:v for k,v in data.items() if k != 'hash'}) or
        [e['day'] for e in data['days']] != list(map(str, TRAIN+DEVELOPMENT))):
        raise ValueError('Current V6 requires the complete new opportunity dataset; legacy labels rejected')
    for entry in data['days']:
        for key in ('bank_root','previous_root','teacher_root'):
            if not Path(entry[key]).resolve().is_relative_to(runtime): raise ValueError('Dataset input escaped runtime')
        if (file_hash(Path(entry['bank_root'])/'complete.json') != entry['bank_certificate_sha256'] or
            file_hash(Path(entry['teacher_root'])/'complete.json') != entry['teacher_sha256']):
            raise ValueError('Dataset source or labels changed')
        proof = verify_day(entry['teacher_root'], entry['bank_certificate_sha256'])
        if proof['day'] != entry['day'] or proof['role'] != entry['role']:
            raise ValueError('Dataset role/day differs from labels')
    return data


def load_teacher(root, session, *, runtime_root, audit_development=False):
    """Conditional flat and unit-held branches; raw gain never quality-as-value.

    All valid flat candles included. Held branch uses strictly later long
    candles from each reference entry, including negative raw exits. No fees.
    """
    from research.rl_trading.v6.training import TeacherDecision, _validate
    root, runtime = Path(root).resolve(), Path(runtime_root).resolve()
    if not root.is_relative_to(runtime) or session.role not in (('train','development') if audit_development else ('train',)):
        raise ValueError('Opportunity teacher role/root mismatch')
    proof = verify_day(root, session.source_certificate_sha256)
    if proof['day'] != str(session.day) or proof['role'] != session.role: raise ValueError('Session identity differs')
    n = len(session.listings); identities = {s:i for i,s in enumerate(session.listings)}
    if tuple(proof['identities']) != session.listings: raise ValueError('Label population differs from bank')
    labels = []; cash0 = 10_000.; threshold = proof['config']['quality_threshold']
    enter_masks={}
    empty_index=np.empty(0,np.int64); empty_features=np.zeros((0,11),np.float32); empty_allowed=np.empty(0,bool)
    flat_account=np.array([cash0,cash0,0,0,0,0,0],np.float32); no_entries=np.zeros(n,bool)
    for shared in (empty_index,empty_features,empty_allowed,flat_account,no_entries): shared.setflags(write=False)
    for shard in proof['shards']:
        folder = root/shard['path']; frame = pl.read_parquet(folder/'labels.parquet')
        pairs = pl.read_parquet(folder/'pairs.parquet')
        entries = {(r['listing_id'],r['pair_id']):r for r in pairs.iter_rows(named=True)} if pairs.height else {}
        weights = frame.group_by('listing_id','pair_id').len()
        counts = {(r['listing_id'],r['pair_id']):r['len'] for r in weights.iter_rows(named=True)}
        for row in frame.iter_rows(named=True):
            i = identities[row['listing_id']]; uid = f"{session.day}:{row['listing_id']}:pair:{row['pair_id']}"
            if i not in enter_masks:
                mask=np.zeros(n,bool); mask[i]=True; mask.setflags(write=False); enter_masks[i]=mask
            enter=enter_masks[i]
            q = float(row['entry_quality']); gain = float(row['entry_gain'])
            labels.append(TeacherDecision(row['time_us'],0,1+i if gain>0 and q>=threshold else 0,
                flat_account,empty_index,empty_features,
                enter,empty_allowed,empty_allowed,empty_allowed,
                sample_weight=1/counts[(row['listing_id'],row['pair_id'])], soft_tokens=(0,1+i),
                soft_probabilities=(1-q,q),episode_uid=uid,opportunity_value_bps=gain/row['close']*10000,
                label_version=ALGORITHM,raw_entry_gain=gain,raw_exit_gain=None))
            if row['exit_gain'] is None: continue
            pair = entries[(row['listing_id'],row['pair_id'])]; price = row['entry_basis']; mark = row['close']
            if price >= cash0: raise ValueError('Reference unit position exceeds teacher bankroll')
            age = (row['time_us']-pair['reference_entry_us'])/1e6; q = float(row['exit_quality'])
            held = np.array([i],np.int64); features = np.array([[1,price,age,(mark-price)/price,0,0,0,0,0,0,0]],np.float32)
            cash = cash0-price; equity = cash+mark
            labels.append(TeacherDecision(row['time_us'],0,1+n if row['exit_gain']>0 and q>=threshold else 1+n+3,
                np.array([cash,equity,0,mark/equity,age,0,0],np.float32),held,features,
                no_entries,np.ones(1,bool),np.zeros(1,bool),np.zeros(1,bool),
                sample_weight=1/counts[(row['listing_id'],row['pair_id'])],soft_tokens=(1+n,1+n+3),soft_probabilities=(q,1-q),
                episode_uid=uid,opportunity_value_bps=row['exit_gain']/price*10000,
                label_version=ALGORITHM,raw_entry_gain=None,raw_exit_gain=row['exit_gain']))
    labels.sort(key=lambda d:(d.close_us,d.episode_uid,len(d.held_index)))
    result=[]; previous=None; order=0
    for item in labels:
        if item.close_us != previous: previous=item.close_us; order=0
        result.append(replace(item,order_index=order)); order+=1
    _validate(tuple(result),(),n,wait_hold=True,ticker_heads=True)
    return tuple(result),()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--listings-per-shard',type=int,default=32)
    parser.add_argument('--canary',action='store_true')
    parser.add_argument('--source-commit',help='Exact pushed commit for an immutable archive snapshot')
    args = parser.parse_args(argv)
    runtime=args.runtime_root.resolve(); output=args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or not 1<=args.workers<=16 or not 1<=args.listings_per_shard<=64:
        raise ValueError('Invalid runtime or bounded concurrency')
    source=json.loads(args.source_manifest.read_text()); roots=source['day_roots']
    expected=list(map(str,CONTEXT_ONLY+TRAIN+DEVELOPMENT))
    if source.get('version')!='rl-trading-v6-forward-candle-day-roots' or sorted(roots)!=expected:
        raise ValueError('Inventory must contain all 19 saved forward banks; sealed holdout excluded')
    config=asdict(Config()); output.mkdir(parents=True,exist_ok=True)
    state_path=output/'progress.json'; started=time.time(); records=[]; failed=[]
    from research.mlops.manifest import write_run_manifest
    write_run_manifest(output/'manifest.json', repo_root=Path(__file__).resolve().parents[3],model_family='rl_trading',
        version=VERSION,job_type='replace_all_saved_labels',run_name=output.name,args=vars(args),config=config,
        data_roots={'source_manifest':str(args.source_manifest)},output_root=output,secret_keys=())
    manifest=json.loads((output/'manifest.json').read_text())
    if args.source_commit:
        if len(args.source_commit)!=40 or any(c not in '0123456789abcdef' for c in args.source_commit):
            raise ValueError('Source snapshot requires an exact 40-character pushed commit')
        if manifest['git_commit'] not in ('unknown',args.source_commit): raise ValueError('Source commit differs from checkout')
        manifest['git_commit']=args.source_commit
    if manifest['git_commit']=='unknown': raise ValueError('Pass --source-commit for an archive snapshot')
    manifest['producer_files_sha256']={name:file_hash(Path(__file__).parent/name) for name in
        ('opportunity_dataset.py','price_action_opportunities.py','price_action_labels.py','run_prepare_labels.py')}
    write_json(output/'manifest.json',manifest)
    def progress(**more):
        state=dict(version=VERSION,algorithm=ALGORITHM,elapsed_seconds=time.time()-started,completed_days=len(records),failed=failed,**more)
        write_json(state_path,state); print(json.dumps(state),flush=True)
    previous=None
    for day in expected[:1] if args.canary else expected:
        root=Path(roots[day]).resolve()
        if not root.is_relative_to(runtime): raise ValueError('Bank escaped runtime')
        progress(status='verifying_bank',day=day,active=0,queued=0,completed=0)
        session=open_session(root,runtime_root=runtime,previous_root=Path(roots[previous]) if previous else None)
        bank_hash=file_hash(root/'complete.json'); config_hash=digest(config)
        binding=dict(day=day,bank_certificate_sha256=bank_hash,bank_manifest_sha256=file_hash(root/'bank'/'complete.json'),config_hash=config_hash,
            producer_sha256={name:file_hash(Path(__file__).parent/name) for name in ('opportunity_dataset.py','price_action_opportunities.py','price_action_labels.py')})
        identities=list(session.listings); original_identities=identities
        if args.canary: identities=identities[:2]
        del session
        folder=output/day; tasks=[]
        for index,left in enumerate(range(0,len(identities),args.listings_per_shard)):
            tasks.append((str(root/'bank'),str(folder/'shards'/f'{index:05d}'),identities[left:left+args.listings_per_shard],binding,config))
        receipts=[]; queued=iter(tasks); pending={}; completed=0
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            def submit():
                try: task=next(queued)
                except StopIteration: return
                pending[pool.submit(build_shard,task)]=task
            for _ in range(min(args.workers,len(tasks))): submit()
            while pending:
                finished,_=wait(pending,timeout=5,return_when=FIRST_COMPLETED)
                for future in finished:
                    task=pending.pop(future)
                    try: receipt=future.result()
                    except Exception as error:
                        failed.append(dict(day=day,shard=task[1],error=str(error)))
                        progress(status='failed',day=day,active=len(pending),queued=len(tasks)-completed-len(pending),completed=completed)
                        for remaining in pending: remaining.cancel()
                        raise
                    receipts.append(dict(path=str(Path(task[1]).relative_to(folder)),sha256=file_hash(Path(task[1])/'complete.json'),receipt=receipt))
                    completed+=1; submit()
                progress(status='generating',day=day,active=len(pending),queued=len(tasks)-completed-len(pending),completed=completed,total=len(tasks))
        receipts.sort(key=lambda r:r['path'])
        totals={k:sum(r['receipt'][k] for r in receipts) for k in ('activity_rows','valid_rows','invalid_price_rows')}
        proof=dict(version=DAY_VERSION,algorithm=ALGORITHM,status=STATUS,day=day,role=role(date.fromisoformat(day)),
            bank_certificate_sha256=bank_hash,binding=binding,config=config,identities=identities,
            shards=[{k:v for k,v in r.items() if k!='receipt'} for r in receipts],sealed_test_accessed=False,
            raw_value_units='dollars_per_share',value_head_conversion='entry_gain/current_close*10000; exit_gain/reference_entry*10000; no fees',
            scope='all saved observed 04:00-20:00 activity; invalid price rows counted and unlabelled',**totals)
        write_json(folder/'complete.json',proof); verify_day(folder,bank_hash)
        records.append(dict(day=day,role=proof['role'],bank_root=str(root),previous_root=str(roots[previous]) if previous else None,
            bank_certificate_sha256=bank_hash,teacher_root=str(folder),teacher_sha256=file_hash(folder/'complete.json'),**totals))
        previous=day
    if args.canary:
        progress(status='canary_complete',active=0,queued=0,completed=len(records)); return
    data=dict(version=VERSION,algorithm=ALGORITHM,status='audited_ready_for_training',sealed_test_accessed=False,
        days=[r for r in records if r['role']!='context_only'],context=records[0],ranking=dict(top_r=1000,sort_secs=15,market_tokens=8,heads=4),
        label_root=str(output),config=config,raw_value_units='dollars_per_share',source_manifest_sha256=file_hash(args.source_manifest))
    data['hash']=digest(data); write_json(output/'dataset.json',data)
    require_dataset(output/'dataset.json',runtime_root=runtime)
    write_json(runtime/'rl-v6-active-labels.json',dict(version=VERSION,algorithm=ALGORITHM,dataset=str(output/'dataset.json'),sha256=file_hash(output/'dataset.json')))
    progress(status='complete',active=0,queued=0,completed=len(records),totals={k:sum(r[k] for r in records) for k in ('activity_rows','valid_rows','invalid_price_rows')})


if __name__=='__main__':
    main()
