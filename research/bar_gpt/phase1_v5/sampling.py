"""Bounded row-weighted plus rare-frame coverage sampling; no source mutations."""
from pathlib import Path
import numpy as np
from ..v5.loader_index_v1 import Entries
from ..v5.data_v3 import validate_complete_market
from ..v5.shards.contract import FRAMES,file_hash


def owners_for_sample(sizes):
    sizes=np.asarray(sizes,dtype=np.int64)
    if sizes.ndim!=1 or not len(sizes) or np.any(sizes<=0):raise ValueError('Invalid source exposure counts')
    cumulative=sizes.cumsum()
    weighted=np.searchsorted(cumulative,np.linspace(0,int(cumulative[-1])-1,min(48,len(sizes)),dtype=np.int64),side='right')
    coverage=np.linspace(0,len(sizes)-1,min(24,len(sizes)),dtype=np.int64)
    return np.unique(np.concatenate((weighted,coverage)))


def sample_records(manifest,split='train',per_frame=256):
    if split not in ('train','validation') or per_frame<1:raise ValueError('Invalid bounded sampling request')
    entries=Entries(manifest['loader_index'][split]);metadata=entries._open()
    owners=owners_for_sample(metadata['stop']-metadata['start']);candidates=[];proofs=[];checked={};seen=set()
    for owner in owners:
        item=entries[int(owner)];path=Path(item['path'])
        signature=(item['sha256'],item['bytes'])
        if str(path) in checked and checked[str(path)]!=signature:raise ValueError('Conflicting source identities')
        if str(path) not in checked:
            if path.stat().st_size!=item['bytes'] or file_hash(path)!=item['sha256']:
                raise ValueError('Bounded sample source SHA/size changed')
            checked[str(path)]=signature
        pool=np.load(path,mmap_mode='r',allow_pickle=False)
        ids=np.linspace(item['start'],item['stop']-1,min(64,item['stop']-item['start']),dtype=np.int64)
        ids=np.array([i for i in ids if (str(path),int(i)) not in seen],dtype=np.int64)
        if not len(ids):continue
        seen.update((str(path),int(i)) for i in ids)
        rows=pool[ids].copy();validate_complete_market(rows)
        candidates.append(rows);proofs.append(dict(split=split,path=str(path),sha256=item['sha256'],indices=ids.tolist()))
    combined=np.concatenate(candidates);counts={f:int((combined['timeframe_us']==f).sum()) for f in FRAMES}
    if min(counts.values())<min(64,per_frame):
        raise ValueError(f'Bounded source sample lacks six-frame coverage: {counts}; do not fit partial-frame scales')
    n=min(per_frame,min(counts.values()));selected=[]
    for frame in FRAMES:
        ids=np.flatnonzero(combined['timeframe_us']==frame)
        selected.extend(ids[np.linspace(0,len(ids)-1,n,dtype=np.int64)].tolist())
    selected=np.array(sorted(selected),dtype=np.int64);evidence=[];chosen=[];offset=0
    for rows,proof in zip(candidates,proofs):
        local=selected[(selected>=offset)&(selected<offset+len(rows))]-offset;offset+=len(rows)
        if not len(local):continue
        chosen.append(rows[local]);evidence.append(dict(**(proof|{'indices':[proof['indices'][int(i)] for i in local]}),
            frames_us=np.unique(rows['timeframe_us'][local]).tolist()))
    return np.concatenate(chosen),evidence
