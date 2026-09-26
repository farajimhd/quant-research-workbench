"""Read only certified V1 market banks; never load teacher arrays or eligibility."""
from pathlib import Path
import numpy as np
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from research.rl_trading.v2.io import read, REPO


def certified_plan(root):
    root = Path(root)
    plan,complete = read(root/'plan.json'),read(root/'complete.json')
    if (plan.get('plan_hash') != digest({k:v for k,v in plan.items() if k != 'plan_hash'})
            or complete.get('plan_hash') != plan['plan_hash']):
        raise ValueError(f'V1 certificate mismatch: {root}')
    return plan,complete


def base_root(root):
    """Resolve certified account/cost overlays without reading their labels."""
    seen = set()
    while True:
        root = Path(root).resolve()
        if root in seen or len(seen) >= 8:
            raise ValueError('V1 overlay cycle/depth exceeded')
        seen.add(root)
        plan,_ = certified_plan(root)
        if not plan.get('base_shard_root'):
            return root
        base = Path(plan['base_shard_root']).resolve()
        parent,_ = certified_plan(base)
        if (parent['plan_hash'] != plan['base_plan_hash']
                or file_hash(base/'complete.json') != plan['base_complete_hash']
                or parent['date'] != plan['date'] or parent['tickers'] != plan['tickers']):
            raise ValueError('V1 overlay market provenance changed')
        root = base


def discover(runtime, day):
    # Only the local configured runtime. Never probe the workstation implicitly.
    return sorted((runtime/'rl-trading-shards'/str(day)).glob('*/complete.json'))


def catalog(roots, *, source, day, listings):
    """Return checked per-listing descriptors, with explicit incompatibility reasons."""
    rows,report = {},[]
    expected = {str(x['listing_id']):x for x in listings}
    checked = set()
    for candidate in roots:
        root = base_root(candidate)
        if root in checked:
            continue
        checked.add(root)
        plan,complete = certified_plan(root)
        reason = None
        if plan.get('version') not in ('rl-trading-structural-shards-v3',
                'rl-trading-structural-shards-v4','rl-trading-structural-shards-v5'):
            reason = 'unsupported market bank version'
        elif plan.get('date') != str(day) or plan.get('market_build_id') != source['build_id']:
            reason = 'different date or source build'
        elif plan.get('feature_names') != list(FEATURE_NAMES):
            reason = 'different feature schema'
        else:
            for name in ('features.py','reference_features.py','arte_source.py','arte_sql.py',
                    'src/backend/fixed_v7_stream.py','src/backend/structural_v7_seed.py',
                    'src/market_engine/streaming_level_book.py','src/market_engine/v7_qmd.py'):
                path = REPO/name if name.startswith('src/') else REPO/'research/rl_trading/v1'/name
                if plan.get('code_hashes',{}).get(name) != file_hash(path):
                    reason = f'incompatible observation code: {name}'
                    break
        if reason:
            report.append(dict(root=str(root),status='incompatible',reason=reason))
            continue
        p2root = Path(plan['phase2_root'])
        p2,_ = certified_plan(p2root)
        p1root = Path(p2['phase1_root'])
        p1,_ = certified_plan(p1root)
        if (p2['plan_hash'] != plan['phase2_plan_hash'] or p1['plan_hash'] != plan['phase1_plan_hash']
                or p2['phase1_plan_hash'] != p1['plan_hash'] or p1['date'] != str(day)
                or p2['date'] != str(day) or p1['source_build_id'] != source['build_id']
                or p1['source_definition_hash'] != source['definition_hash']):
            raise ValueError('V1 market source chain differs from requested source')
        population = {x['ticker']:x for x in p2['selected']}
        if len(population) != len(p2['selected']) or len(set(plan['tickers'])) != len(plan['tickers']):
            raise ValueError('Duplicate V1 listing identity')
        progress = read(root/'progress.json')
        if progress['plan_hash'] != plan['plan_hash']:
            raise ValueError('V1 progress provenance mismatch')
        banks = {}
        for name,dtype,shape in (
                ('features',np.float32,(len(plan['tickers']),SECONDS,len(FEATURE_NAMES))),
                ('volume_60s',np.float64,(len(plan['tickers']),SECONDS))):
            path = root/(name+'.npy')
            if file_hash(path) != complete['files'][path.name]:
                raise ValueError(f'V1 market bank integrity failure: {path}')
            bank = np.load(path,mmap_mode='r',allow_pickle=False)
            if bank.shape != shape or bank.dtype != dtype:
                raise ValueError('V1 market bank shape/dtype mismatch')
            banks[name] = bank
        count = 0
        for index,ticker in enumerate(plan['tickers']):
            listing = population[ticker]
            identity = str(listing['listing_id'])
            if identity not in expected:
                continue
            if listing != expected[identity] or p1['source_units'][ticker] != source['units'][str(day)][ticker]:
                raise ValueError('V1 cached listing/source attempt mismatch')
            saved = progress['done'][ticker]
            descriptor = dict(root=str(root),index=index,plan_hash=plan['plan_hash'],
                hashes=dict(features=saved['features'],volume_60s=saved['volume']),
                reference=saved['reference'],listing=listing)
            if identity in rows and rows[identity]['hashes'] != descriptor['hashes']:
                raise ValueError('Conflicting certified V1 market banks')
            rows.setdefault(identity,descriptor)
            count += 1
        report.append(dict(root=str(root),status='compatible',listings=count,
            plan_hash=plan['plan_hash'],complete_hash=file_hash(root/'complete.json')))
    return rows,report


def copy_row(descriptor):
    from research.rl_trading.v2.build_data import bank_hash
    values = {}
    for name in ('features','volume_60s'):
        bank = np.load(Path(descriptor['root'])/(name+'.npy'),mmap_mode='r',allow_pickle=False)
        value = np.array(bank[descriptor['index']],copy=True)
        if bank_hash(value) != descriptor['hashes'][name] or not np.isfinite(value).all():
            raise ValueError('V1 cached row changed or is invalid')
        values[name] = value
    return values,descriptor['reference']
