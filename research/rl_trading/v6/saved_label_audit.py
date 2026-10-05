"""Read-only Research adapter for the published current teacher shards.

Resolve workstation paths explicitly; never substitute local experiment labels.
Only selected shard bytes are cached under runtimes and checked against receipts.
"""
from datetime import datetime
from functools import lru_cache
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
from threading import RLock
from zoneinfo import ZoneInfo

import polars as pl
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6 import opportunity_dataset as data
from research.rl_trading.v6 import price_action_opportunities as algorithm
from research.rl_trading.v6.label_timing import CONTRACT

LOCK = RLock()
DEFAULT_ROOT = r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'


def runtime():
    return Path(os.environ.get('RL_V6_LABEL_AUDIT_RUNTIME', DEFAULT_ROOT)).resolve()


def mapped(value):
    # Stored workstation D: paths resolve against the deployment's explicit root.
    relative = PureWindowsPath(value).relative_to(PureWindowsPath('D:/TradingML/runtimes'))
    path = runtime().joinpath(*relative.parts).resolve()
    if not path.is_relative_to(runtime()): raise ValueError('Label path escaped runtime')
    return path


def read_json(path, expected=None):
    raw = Path(path).read_bytes()
    from hashlib import sha256
    if expected and sha256(raw).hexdigest() != expected: raise ValueError('Label certificate hash changed')
    return json.loads(raw)


def published():
    active = read_json(runtime()/'rl-v6-active-labels.json')
    if active.get('algorithm') != algorithm.VERSION or active.get('version') != data.VERSION:
        raise ValueError('Active registry is not the new V6 labels')
    dataset = read_json(mapped(active['dataset']), active['sha256'])
    audit = read_json(mapped(dataset['publication_audit']), active['publication_audit_sha256'])
    entries = [dataset['context']] + dataset['days']
    if (dataset.get('hash') != digest({k:v for k,v in dataset.items() if k!='hash'}) or
        dataset.get('status') != 'audited_ready_for_training' or dataset.get('sealed_test_accessed') is not False or
        dataset.get('version') != data.VERSION or dataset.get('algorithm') != algorithm.VERSION or
        dataset.get('publication_audit_sha256') != active['publication_audit_sha256'] or
        audit.get('status') != 'passed' or audit.get('day_certificates') != {e['day']:e['teacher_sha256'] for e in entries} or
        audit.get('ranking') != dataset['ranking'] or audit.get('algorithm') != algorithm.VERSION):
        raise ValueError('Published label authority differs from its audit')
    return active, dataset


def catalog():
    active, dataset = published()
    return dict(version=data.VERSION, algorithm=algorithm.VERSION, dataset_sha256=active['sha256'],
        supports_combined=True,
        timing=CONTRACT, days=[dict(day=e['day'], role=e['role'], valid_rows=e['valid_rows'],
        invalid_price_rows=e['invalid_price_rows']) for e in [dataset['context']]+dataset['days']],
        sealed_test_accessed=False)


def session(day):
    active, dataset = published()
    entry = next((e for e in [dataset['context']]+dataset['days'] if e['day']==day), None)
    if entry is None: raise ValueError('Day not in published label dataset; holdout unavailable')
    root = mapped(entry['teacher_root'])
    proof = read_json(root/'complete.json', entry['teacher_sha256'])
    if (proof.get('version') != data.DAY_VERSION or proof.get('algorithm') != algorithm.VERSION or
        proof.get('status') != data.STATUS or proof.get('day') != day or proof.get('role') != entry['role'] or
        proof.get('bank_certificate_sha256') != entry['bank_certificate_sha256']):
        raise ValueError('Session label certificate mismatch')
    bank_root = mapped(entry['bank_root'])
    bank = read_json(bank_root/'complete.json', entry['bank_certificate_sha256'])
    return active, entry, proof, root, bank_root, bank


def verified_local(path, expected):
    """Content-addressed external cache; never write into source or publication."""
    root = Path('D:/TradingML/runtimes/rl-v6-app-label-cache')
    root.mkdir(parents=True, exist_ok=True)
    destination = root/(expected+'.parquet')
    with LOCK:
        if not destination.exists():
            temporary = destination.with_suffix('.tmp')
            shutil.copyfile(path, temporary)
            if file_hash(temporary) != expected:
                temporary.unlink(); raise ValueError('Saved label bytes differ from receipt')
            temporary.replace(destination)
        if file_hash(destination) != expected: raise ValueError('Local audit cache corrupted')
    return destination


@lru_cache(maxsize=24)
def identity_rows(path, expected):
    rows = pl.read_parquet(verified_local(Path(path),expected), columns=['listing_id','ticker']).unique()
    if rows['listing_id'].n_unique() != rows.height: raise ValueError('Ambiguous saved ticker identity')
    return rows.to_dicts()


def listings(day):
    active, entry, proof, root, bank_root, bank = session(day)
    symbols = saved_symbols(str(bank_root),entry['bank_certificate_sha256'])
    plan = read_json(bank_root/'plan.json')
    empty=empty_price_listings(str(root),entry['teacher_sha256'])
    return dict(day=day, role=entry['role'], dataset_sha256=active['sha256'],
        valid_rows=proof['valid_rows'], invalid_price_rows=proof['invalid_price_rows'],
        listings=sorted([dict(listing_id=i, ticker=symbols[i], venue=i.split(':')[-2],
            activity_rows=plan['census'][i],has_price_targets=i not in empty) for i in proof['identities']],key=lambda r:(r['ticker'],r['venue'])))


@lru_cache(maxsize=24)
def empty_price_listings(root,certificate_hash):
    root=Path(root);proof=read_json(root/'complete.json',certificate_hash);empty=set()
    for shard in proof['shards']:
        receipt=read_json(root/shard['path']/'complete.json',shard['sha256'])
        if receipt['binding']!=proof['binding']: raise ValueError('Empty listing receipt binding changed')
        empty.update(receipt['all_invalid_listings'])
    if not empty<=set(proof['identities']): raise ValueError('Unknown empty price listing')
    return frozenset(empty)


@lru_cache(maxsize=24)
def saved_symbols(root, certificate_hash):
    """Resolve even zero-episode symbols from the original compiler receipts."""
    from hashlib import sha256
    root=Path(root); bank=read_json(root/'complete.json',certificate_hash)
    plan=read_json(root/'plan.json')
    if plan['hash']!=bank['plan_hash'] or digest({k:v for k,v in plan.items() if k!='hash'})!=plan['hash']:
        raise ValueError('Saved identity plan changed')
    episode_receipt=bank['outputs']['episodes']
    symbols=({r['listing_id']:r['ticker'] for r in identity_rows(str(root/'episodes.parquet'),episode_receipt['sha256'])}
             if episode_receipt.get('rows',1)>0 else {})
    for identity,size in plan['census'].items():
        if identity in symbols: continue
        fragment=read_json(root/'fragments'/sha256(identity.encode()).hexdigest()[:24]/'complete.json')
        if (fragment['listing_id']!=identity or fragment['report']['candles']!=size or
                not fragment['ticker'] or fragment['ticker'].startswith('listing:')):
            raise ValueError('Saved compiler symbol receipt mismatch')
        symbols[identity]=fragment['ticker']
    return symbols


@lru_cache(maxsize=4)
def selected_frames(folder, receipt_hash, identity):
    folder = Path(folder); receipt = read_json(folder/'complete.json',receipt_hash)
    if receipt.get('version') != data.DAY_VERSION or receipt.get('algorithm') != algorithm.VERSION or receipt.get('status') != data.STATUS or identity not in receipt['identities']:
        raise ValueError('Selected shard identity/version mismatch')
    frames = {}
    for name in data.FILES:
        record = receipt['files'][name]
        path = verified_local(folder/(name+'.parquet'), record['sha256'])
        scan = pl.scan_parquet(path)
        if scan.select(pl.len()).collect().item() != record['rows']: raise ValueError('Saved label count changed')
        frames[name] = scan.filter(pl.col('listing_id')==identity).collect()
    return frames


def product(day, listing_id):
    active, entry, proof, root, bank_root, bank = session(day)
    if listing_id not in proof['identities']: raise ValueError('Listing absent from published labels')
    for shard in proof['shards']:
        folder = (root/shard['path']).resolve()
        if not folder.is_relative_to(root): raise ValueError('Shard escaped root')
        receipt = read_json(folder/'complete.json',shard['sha256'])
        if receipt.get('binding') != proof['binding']: raise ValueError('Shard source binding mismatch')
        if listing_id in receipt['identities']:
            frames = selected_frames(str(folder),shard['sha256'],listing_id)
            break
    else: raise ValueError('Listing missing from certified shards')
    symbols = saved_symbols(str(bank_root),entry['bank_certificate_sha256'])
    begin = int(datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1e6)+1_000_000
    finish = begin+16*3600_000_000
    labels = frames['labels']; trades = frames['trades']
    flat = algorithm.classify(labels,proof['config']['quality_threshold'],'flat')
    held = labels.filter(pl.col('exit_gain').is_not_null()).with_columns(
        pl.when((pl.col('exit_gain')>0)&(pl.col('exit_quality')>=proof['config']['quality_threshold']))
        .then(pl.lit('EXIT')).otherwise(pl.lit('HOLD')).alias('action'))
    metadata = dict(day=day, ticker=symbols[listing_id], listing_id=listing_id, session='Saved extended session',
        version=algorithm.VERSION, config=proof['config'], begin_us=begin, finish_us=finish,
        dataset_sha256=active['sha256'], certificate_sha256=entry['teacher_sha256'], shard_sha256=shard['sha256'], timing=CONTRACT,
        observed_price_candles=labels.height, consumed_activity_rows=None, omitted_invalid_price_rows=None,
        absent_second_slots=16*3600-labels.height, approximate_volume=None,
        trades=trades.height, total_price_pnl=float(trades['price_pnl'].sum()),
        both_opportunities=int(labels['both_opportunities'].sum()), actions=labels.group_by('action').len().sort('action').to_dicts(),
        teacher_actions=[dict(branch=branch,**row) for branch,frame in [('flat',flat),('held',held)]
                         for row in frame.group_by('action').len().sort('action').to_dicts()],
        pairs=frames['pairs'].to_dicts(), price_source=str(folder/'labels.parquet'),
        semantics='Published teacher targets; fixed 90% hard threshold; saved raw $/share gains; no fills/fees')
    # Exact activity census supplies rejected rows per identity without reading feature tensors.
    plan = read_json(bank_root/'plan.json')
    if plan.get('hash') != bank['plan_hash'] or digest({k:v for k,v in plan.items() if k!='hash'}) != plan['hash']:
        raise ValueError('Source activity census changed')
    metadata['consumed_activity_rows'] = plan['census'][listing_id]
    metadata['omitted_invalid_price_rows'] = plan['census'][listing_id]-labels.height
    metadata['reporting'] = next(r for r in plan['reporting_coverage']['current'] if r['source_date'] == day)
    return metadata, frames


def metadata(day, listing_id):
    return product(day,listing_id)[0]


def chart(day, listing_id, start_us=None, seconds=900, view='combined', candle_offset=None):
    proof, frames = product(day,listing_id)
    if view not in ('combined','flat','held','reference'): raise ValueError('Select teacher opportunities, a conditional branch, or reference')
    if candle_offset is None:
        result = algorithm.chart_frames(proof,frames,start_us,seconds,proof['config']['quality_threshold'],view)
    else:
        result = model_chart(day,listing_id,proof,frames,start_us,candle_offset,view)
    if view=='held':
        # Training has held supervision only where a saved exit gain is defined.
        for row in result['labels']:
            if row['action']=='CONTEXT': continue
            if row['exit_gain'] is None: row.update(action='UNLABELLED',label_value=None)
            else:
                row['action']='EXIT' if row['exit_gain']>0 and row['exit_quality']>=proof['config']['quality_threshold'] else 'HOLD'
                row['label_value']=row['exit_quality'] if row['action']=='EXIT' else 1-row['exit_quality']
    result.update(dataset_sha256=proof['dataset_sha256'],timing=CONTRACT)
    return result


def model_chart(day,identity,proof,frames,start_us,offset,view):
    from research.rl_trading.v6.model_candle_audit import certified_bank,select_window,project
    active,entry,receipt,root,bank_root,certificate=session(day)
    bank=certified_bank(str(bank_root),entry['bank_certificate_sha256'])
    previous=None
    if entry['previous_root']:
        previous_root=mapped(entry['previous_root'])
        _,dataset=published()
        previous_entry=next(e for e in [dataset['context']]+dataset['days'] if mapped(e['bank_root'])==previous_root)
        previous_bank=certified_bank(str(previous_root),previous_entry['bank_certificate_sha256'])
        if identity in previous_bank.manifest['offsets']: previous=previous_bank.listing(identity)
    raw,window=select_window(bank.listing(identity),previous,offset=offset,start_us=start_us)
    candles,overlays,oscillators=project(raw)
    session_clocks=[r['time_us'] for r in raw if r['part']=='session']
    classified=algorithm.classify(frames['labels'].filter(pl.col('time_us').is_in(session_clocks)),proof['config']['quality_threshold'],view)
    rows={r['time_us']:r for r in classified.to_dicts()}
    labels=[]
    for r in raw:
        label=rows.get(r['time_us']) if r['part']=='session' else None
        if r['part']=='session' and label is None: raise ValueError('Model price candle lacks certified teacher target')
        if label is None:
            label=dict(time_us=r['time_us'],close=candles[len(labels)]['close'],action='CONTEXT',label_value=None,
                entry_gain=0.,entry_quality=0.,exit_gain=None,exit_quality=None,reference_action='CONTEXT',
                episode_id=None,pair_id=None,both_opportunities=False,entry_basis=None,carried_next_pair_value=0.)
        labels.append(dict(**label,model_features=r))
    start=raw[0]['time_us'] if raw else proof['begin_us'];end=raw[-1]['time_us']+1 if raw else start
    regions=[dict(start=r['start_us']//1_000_000-1,end=r['end_us']//1_000_000-1,
        color='var(--success)' if r['direction']==1 else 'var(--danger)',label='')
        for r in frames['episodes'].filter((pl.col('start_us')<end)&(pl.col('end_us')>start)).to_dicts()]
    window.update(bank_file_sha256=bank.manifest['files_sha256'],previous_context_available=previous is not None)
    return dict(ticker=proof['ticker'],version=algorithm.VERSION,candles=candles,labels=labels,oscillator_series=oscillators,
        overlay_series=overlays,regions=regions,start_us=start,end_us=end,view=view,
        quality_threshold=proof['config']['quality_threshold'],**window)
