"""CPU-only, training-authority supplement; never reads validation examples."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,bisect,collections,json,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from ..v5.shards.phase1_contract import file_hash,write_json,digest,FRAMES


def sample_rows(rows,seed):
    rng=np.random.default_rng(seed);selected={}
    prices=rows['values'][...,[3,9,15]];mask=rows['valid'][...,[3,9,15]].astype(bool)
    minimum=np.where(mask,prices,np.inf).min((1,2))
    for band,(lo,hi) in enumerate(zip((0,.01,.1,1,5,20,100),(.01,.1,1,5,20,100,np.inf))):
        ids=np.flatnonzero((minimum>=lo)&(minimum<hi))
        for i in rng.choice(ids,min(8,len(ids)),replace=False):selected[int(i)]=(0 if band<2 else 2,f'price_band_{band}')
    activity=rows['values'][...,[4,5,10,11,16,17]];v=rows['valid'][...,[4,5,10,11,16,17]].astype(bool)
    peak=np.where(v,activity,0).max((1,2))
    for i in np.argsort(peak,kind='stable')[-8:]:selected.setdefault(int(i),(1,'activity_tail'))
    for i in rng.choice(len(rows),min(8,len(rows)),replace=False):selected.setdefault(int(i),(3,'uniform'))
    return selected


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--files-per-frame',type=int,default=1024);p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    if a.output.exists() or not 1<=a.workers<=4 or not 1<=a.files_per_frame<=2048:raise ValueError('New identity and bounded CPU work required')
    root=Path('D:/TradingML/runtimes/bar_gpt')
    if not a.output.resolve().is_relative_to(root.resolve()):raise ValueError('Runtime authority required')
    a.output.mkdir();began=time.perf_counter();seed=190731
    splitpath=root/'phase1-v6-cohort-balanced-v4/split.json';split=json.loads(splitpath.read_text())
    indexroot=splitpath.parent/('loader-index-v1-'+split['manifest_hash'][:20]);seal=json.loads((indexroot/'index.json').read_text())
    if digest({k:v for k,v in seal.items() if k!='hash'})!=seal['hash'] or seal['split_hash']!=split['manifest_hash']:raise ValueError('Index seal')
    indexpath=indexroot/seal['splits']['train']['file']
    if file_hash(indexpath)!=seal['splits']['train']['sha256']:raise ValueError('Training index changed')
    index=np.load(indexpath,mmap_mode='r');groups=collections.defaultdict(list)
    for i,e in enumerate(index):groups[int(Path(e['path'].decode()).parent.name.rsplit('-',1)[1])].append(i)
    rng=np.random.default_rng(seed);chosen=[]
    for frame in FRAMES:
        ids=groups[frame];chosen.extend(rng.choice(ids,min(a.files_per_frame,len(ids)),replace=False).tolist())
    chosen.sort();prior=json.loads((root/'phase1-v8-learned-mae-20261117/dataset/train-identities.json').read_text())
    existing={(r['path'],r['index']) for r in prior}
    def work(i):
        e=index[i];path=Path(e['path'].decode())
        if file_hash(path)!=e['sha256'].decode():raise ValueError('Shard hash changed: '+str(path))
        receipt=json.loads((path.parent/'receipt.json').read_text())
        if digest({k:v for k,v in receipt.items() if k!='receipt_hash'})!=receipt['receipt_hash']:raise ValueError('Receipt seal')
        rows=np.load(path,mmap_mode='r')[int(e['start']):int(e['stop'])]
        if not (rows['split']==0).all() or not (rows['end_us'][:,-1]<split['cutoff_us']).all():raise ValueError('Training boundary')
        selected=sample_rows(rows,seed+i);result=[]
        for j,(priority,reason) in selected.items():
            original=int(e['start'])+j
            if (str(path),original) in existing:continue
            row=rows[j].copy();frame=int(row['timeframe_us'])
            identity=dict(path=str(path),index=original,shard_sha=e['sha256'].decode(),receipt_hash=receipt['receipt_hash'],ticker=receipt['ticker'],
                          frame=frame,start_us=int(row['end_us'][0])-frame,end_us=int(row['end_us'][-1]),reason=reason,priority=priority,sampling_weight=1.)
            result.append((identity,row))
        return result,len(selected)-len(result)
    candidates=[];existing_rejections=0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for start in range(0,len(chosen),64):
            if time.perf_counter()-began>1200 or (a.output/'STOP').exists():raise InterruptedError('Preparation deadline/STOP')
            for batch,excluded in pool.map(work,chosen[start:start+64]):candidates.extend(batch);existing_rejections+=excluded
            write_json(a.output/'progress.json',dict(files_done=min(start+64,len(chosen)),files_total=len(chosen),candidate_windows=len(candidates),seconds=time.perf_counter()-began))
    # Prefer rare bands/tails, then reject overlapping windows within each
    # security/timeframe. This sampling rejection never removes source data.
    candidates.sort(key=lambda x:(x[0]['priority'],x[0]['path'],x[0]['index']))
    intervals=collections.defaultdict(list);kept=[];identities=[];rejected=0
    for identity,row in candidates:
        key=(identity['ticker'],identity['frame']);spans=intervals[key];start,end=identity['start_us'],identity['end_us']
        pos=bisect.bisect_left(spans,(start,end))
        if (pos and spans[pos-1][1]>start) or (pos<len(spans) and spans[pos][0]<end):rejected+=1;continue
        spans.insert(pos,(start,end));kept.append(row);identities.append(identity)
    if not kept:raise ValueError('Empty supplement')
    data=np.array(kept,dtype=kept[0].dtype);np.save(a.output/'train.npy',data,allow_pickle=False)
    write_json(a.output/'identities.json',identities)
    coverage={}
    for frame in FRAMES:
        r=data[data['timeframe_us']==frame];v=r['valid'][...,9].astype(bool);x=r['values'][...,9]
        coverage[str(frame)]=dict(windows=len(r),bid_below_cent_windows=int(((x<.01)&v).any(1).sum()),bid_1_to_10cent_windows=int(((x>=.01)&(x<.1)&v).any(1).sum()),bid_below_dollar_windows=int(((x<1)&v).any(1).sum()))
    m=dict(source=a.revision,split_hash=split['manifest_hash'],index_sha=file_hash(indexpath),seed=seed,files_per_frame=a.files_per_frame,
           selected_entries=chosen,selected_files=len(chosen),candidates=len(candidates),overlap_rejections=rejected,existing_identity_rejections=existing_rejections,windows=len(data),coverage=coverage,
           frame_entry_probabilities={str(f):min(a.files_per_frame,len(groups[f]))/len(groups[f]) for f in FRAMES},
           files={n:file_hash(a.output/n) for n in ('train.npy','identities.json')},seconds=time.perf_counter()-began,
           sampling='Uniform index entries within timeframe, then8 per observed price band plus8 activity-tail and8 uniform rows; rare-first nonoverlap selection. Excludes exact original training identities. All partition0, before cutoff.',
           weighting='Equal selected-window training weights; deliberately not population-unbiased. Original latent calibration unchanged; no validation/calibration from audit cases.')
    m['hash']=digest(m);write_json(a.output/'manifest.json',m);print(json.dumps(dict(windows=len(data),seconds=m['seconds'],coverage=coverage)),flush=True)


if __name__=='__main__':main()
