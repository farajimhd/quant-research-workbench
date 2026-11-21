"""Labelled training-only split-equivalent augmentation; source stays immutable."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
import numpy as np
from ..phase1_v11.data import verify
from ..v5.shards.phase1_contract import file_hash,write_json,digest

PRICES=(0,1,2,3,6,7,8,9,12,13,14,15)
QUANTITIES=(4,10,16)


def augment(rows,powers):
    result=rows.copy()
    for columns,sign in ((PRICES,1),(QUANTITIES,-1)):
        old=rows['values'][...,list(columns)];valid=rows['valid'][...,list(columns)].astype(bool)
        shift=np.asarray(powers,dtype=np.int32)[:,None,None]*sign
        adjusted=np.where(valid,np.ldexp(old,shift),old)
        if not np.isfinite(adjusted[valid]).all():raise ValueError('Augmentation overflow or nonfinite valid input')
        restored=np.ldexp(adjusted,-shift)
        if not np.array_equal(restored[valid],old[valid]):raise ValueError('Augmentation is not exactly reversible')
        result['values'][...,list(columns)]=adjusted
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--pool',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    root=Path('D:/TradingML/runtimes/bar_gpt');base=root/'phase1-v8-learned-mae-20261117/dataset'
    if a.output.exists() or not a.output.resolve().is_relative_to(root.resolve()):raise ValueError('New runtime identity required')
    started=time.perf_counter();original=verify(base,parts=('train',));pool=json.loads((a.pool/'manifest.json').read_text())
    if digest({k:v for k,v in pool.items() if k!='hash'})!=pool['hash'] or pool['split_hash']!=original['source_split_hash']:raise ValueError('Pool provenance')
    for n,h in pool['files'].items():
        if file_hash(a.pool/n)!=h:raise ValueError('Pool changed')
    first=np.load(base/'train.npy',mmap_mode='r');second=np.load(a.pool/'train.npy',mmap_mode='r')
    source=np.concatenate((first,second));rng=np.random.default_rng(192731)
    powers=rng.choice(np.array(list(range(-8,0))+list(range(1,9)),dtype=np.int32),len(source))
    synthetic=augment(source,powers)
    a.output.mkdir();combined=np.concatenate((second,synthetic));np.save(a.output/'train.npy',combined,allow_pickle=False)
    identities=json.loads((a.pool/'identities.json').read_text());base_sha=file_hash(base/'train.npy')
    for i,k in enumerate(powers):
        is_base=i<len(first)
        identities.append(dict(kind='synthetic_split_equivariant',source_dataset=str((base if is_base else a.pool)/'train.npy'),
                               source_sha=base_sha if is_base else pool['files']['train.npy'],source_row=i if is_base else i-len(first),price_power=int(k),sampling_weight=1.))
    write_json(a.output/'identities.json',identities)
    m=dict(source=a.revision,split_hash=pool['split_hash'],base_dataset_hash=original['hash'],real_pool_hash=pool['hash'],
           windows=len(combined),real_windows=len(second),synthetic_windows=len(synthetic),seed=192731,
           powers={str(k):int((powers==k).sum()) for k in np.unique(powers)},roundtrip='Exact equality on every source-valid augmented value; no clipping',
           files={n:file_hash(a.output/n) for n in ('train.npy','identities.json')},seconds=time.perf_counter()-started,
           augmentation='All12 prices multiplied by2**k; volume/bid-size/ask-size divided by2**k; counts/timestamps/validity unchanged. One synthetic copy per original training and real-supplement window.',
           weighting='Equal real and synthetic window sampling after adding original base train set. No validation or audit examples. Fixed latent calibration remains original; activity calibration training-only.')
    m['hash']=digest(m);write_json(a.output/'manifest.json',m);print(json.dumps({k:v for k,v in m.items() if k!='files'}),flush=True)


if __name__=='__main__':main()
