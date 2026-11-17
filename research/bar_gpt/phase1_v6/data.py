"""Frozen, receipt-verified research subsets; never modify source records."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import numpy as np
from ..v5.shards.phase1_contract import FRAMES, CHANNELS, digest, file_hash, write_json
from ..v5.data_v3 import dataset_identity, validate_complete_market


def resolve(path, remote):
    return Path(str(path).replace('D:\\TradingML', str(remote)).replace('D:/TradingML', str(remote)))


def prepare(split_path, output, remote, shards_per_frame=64, rows_per_shard=64):
    started = time.time()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(split_path.read_text())
    assert digest({k:v for k,v in manifest.items() if k != 'manifest_hash'}) == manifest['manifest_hash']
    identity = dataset_identity(resolve(manifest['dataset'], remote))
    for key in ('dataset_binding', 'dataset_version', 'cohort_hash', 'market_channels'):
        assert identity[key] == manifest[key], key
    assert [v['binding'] for v in identity['dataset_members']] == [v['binding'] for v in manifest['dataset_members']]
    cutoff = int(datetime(2025, 1, 1, 4, tzinfo=timezone.utc).timestamp()*1e6)
    rng = np.random.default_rng(20261117)
    selected = defaultdict(list)
    evidence = defaultdict(list)
    reasons = Counter()
    checked = {}
    seen = set()
    spans = defaultdict(list)
    for partition in ('train', 'validation'):
        entries = manifest['splits'][partition]
        owners = set()
        for frame in FRAMES:
            eligible = [i for i,e in enumerate(entries) if e['frames'].get(str(frame), 0)]
            # Stratify by chronological manifest position; random choice within strata.
            for chunk in np.array_split(eligible, min(len(eligible), shards_per_frame)):
                owners.add(int(rng.choice(chunk)))
        for number, owner in enumerate(sorted(owners)):
            entry = entries[owner]
            path = resolve(entry['path'], remote)
            if str(path) not in checked:
                assert path.stat().st_size == entry['bytes'] and file_hash(path) == entry['sha256'], path
                receipt = json.loads((path.parent/'receipt.json').read_text())
                assert digest({k:v for k,v in receipt.items() if k != 'receipt_hash'}) == entry['receipt_hash']
                assert any(v['name'] == path.name and v['sha256'] == entry['sha256'] for v in receipt['files'])
                checked[str(path)] = entry['sha256']
            source = np.load(path, mmap_mode='r', allow_pickle=False)
            indices = np.linspace(entry['start'], entry['stop']-1, min(rows_per_shard,entry['stop']-entry['start']), dtype=np.int64)
            rows = source[indices].copy()
            validate_complete_market(rows)
            group = entry['path'].replace('\\','/').split('/jobs/')[1].split('/')[0]
            for row, index in zip(rows, indices):
                key = (entry['sha256'], int(index))
                if key in seen:
                    reasons['duplicate_identity'] += 1
                    continue
                seen.add(key)
                start, end = int(row['end_us'][0]-row['timeframe_us']), int(row['end_us'][-1])
                assert (end < manifest['cutoff_us']) if partition == 'train' else (start >= manifest['cutoff_us'])
                if partition == 'train': name = 'train'
                elif end < cutoff: name = 'development'
                elif start >= cutoff: name = 'audit'
                else:
                    reasons['development_audit_crossing_purged'] += 1
                    continue
                # Reduce redundant overlapping contexts within each identity/frame group.
                span_key = (name, group, int(row['timeframe_us']))
                if any(start <= b and end >= a for a,b in spans[span_key]):
                    reasons['redundant_overlapping_context'] += 1
                    continue
                spans[span_key].append((start,end))
                selected[name].append(row)
                evidence[name].append(dict(path=entry['path'], sha256=entry['sha256'], receipt_hash=entry['receipt_hash'],
                    index=int(index), group=group, start_us=start, end_us=end, sampling_weight=1.0))
            if number % 20 == 0:
                write_json(output/'progress.json', dict(stage='sampling',partition=partition,done=number,total=len(owners),counts={k:len(v) for k,v in selected.items()},elapsed=time.time()-started))
    coverage = {}
    for name in ('train','development','audit'):
        rows = np.array(selected[name], dtype=source.dtype)
        assert len(rows)
        coverage[name] = {}
        for frame in FRAMES:
            part = rows[rows['timeframe_us']==frame]
            assert len(part), (name,frame)
            p = part['values'][:,-1,3]
            bands = np.digitize(p,[.1,1,5,20,100])
            coverage[name][str(frame)] = dict(count=len(part),price_bands=np.bincount(bands,minlength=6).tolist(),
                min_price=float(p.min()),max_price=float(p.max()),valid_counts=part['valid'].sum((0,1)).tolist(),
                sparse_quotes=int((part['valid'][...,6:].mean((1,2))<.5).sum()),
                activity_quantiles=np.quantile(part['values'][...,4],[0,.1,.5,.9,1]).tolist(),
                years=dict(Counter(str(np.datetime64(int(v//1000000),'s'))[:4] for v in part['end_us'][:,-1])))
        np.save(output/f'{name}.npy', rows, allow_pickle=False)
        write_json(output/f'{name}-identities.json', evidence[name])
    train = np.load(output/'train.npy',allow_pickle=False)
    tiny = np.concatenate([np.flatnonzero(train['timeframe_us']==f)[:4] for f in FRAMES])
    np.save(output/'tiny.npy',train[tiny],allow_pickle=False)
    result = dict(version='bargpt-experiment-subset-v6-1',created=time.time(),source_split_hash=manifest['manifest_hash'],
        source_split_file_sha256=file_hash(split_path),identity=identity,seed=20261117,audit_cutoff_us=cutoff,
        policy='Stratified source-entry discovery, evenly spaced rows, greedy nonoverlap per source job/frame; uniform selected-row weights. Not an unbiased population estimate. All valid selected rows preserved.',
        calibration='training subset only; uniform rows and valid observations; no inverse population weighting',
        coverage=coverage,rejections=dict(reasons),verified_source_files=len(checked),elapsed_seconds=time.time()-started,
        tiny_train_indices=tiny.tolist(),files={p.name:file_hash(p) for p in output.iterdir() if p.suffix=='.npy' or p.name.endswith('-identities.json')})
    result['hash']=digest(result)
    write_json(output/'manifest.json',result)
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--split',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('D:/TradingML'))
    p.add_argument('--shards-per-frame',type=int,default=64)
    a=p.parse_args()
    print(json.dumps(prepare(a.split,a.output,a.root,a.shards_per_frame),indent=2))
