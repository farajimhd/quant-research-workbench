"""Reuse frozen training/development; reserve new audit groups for learned-only MAE."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,shutil,time
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
from ..phase1_v6.data import resolve
from ..v5.shards.phase1_contract import FRAMES,digest,file_hash,write_json
from ..v5.data_v3 import validate_complete_market,dataset_identity


def prepare(old,split,output,root,previous):
    started=time.time();output.mkdir(parents=True,exist_ok=False)
    prior=json.loads((old/'manifest.json').read_text());authority=json.loads(split.read_text())
    assert digest({k:v for k,v in prior.items() if k!='hash'})==prior['hash']
    assert digest({k:v for k,v in authority.items() if k!='manifest_hash'})==authority['manifest_hash']
    assert authority['manifest_hash']==prior['source_split_hash']
    dependency=dataset_identity(resolve(authority['dataset'],root))
    for key in ('dataset_binding','dataset_version','cohort_hash','market_channels'):
        assert dependency[key]==authority[key],key
    assert [v['binding'] for v in dependency['dataset_members']]==[v['binding'] for v in authority['dataset_members']]
    excluded=set()
    for folder in [old,*previous]:
        for name in ('audit-identities.json','development-identities.json'):
            excluded.update(r['group'] for r in json.loads((folder/name).read_text()))
    for name in ('train.npy','development.npy','tiny.npy','train-identities.json','development-identities.json'):
        assert file_hash(old/name)==prior['files'][name]
        shutil.copyfile(old/name,output/name)
    for name in ('train','development'):
        records=json.loads((output/(name+'-identities.json')).read_text())
        assert all(r['end_us']<authority['cutoff_us'] for r in records) if name=='train' else all(
            r['start_us']>=authority['cutoff_us'] and r['end_us']<prior['audit_cutoff_us'] for r in records)
    rng=np.random.default_rng(2026111708);entries=authority['splits']['validation'];eligible=[]
    for e in entries:
        group=e['path'].replace('\\','/').split('/jobs/')[1].split('/')[0]
        if group not in excluded:eligible.append((e,group))
    owners=set()
    for frame in FRAMES:
        choices=[i for i,(e,g) in enumerate(eligible) if e['frames'].get(str(frame),0)]
        for chunk in np.array_split(choices,min(160,len(choices))):owners.add(int(rng.choice(chunk)))
    selected=[];identities=[];spans=defaultdict(list);reasons=Counter();checked=0
    for ix,owner in enumerate(sorted(owners)):
        if time.time()-started>600:raise TimeoutError('Dataset preparation exceeded ten-minute budget')
        e,group=eligible[owner];path=resolve(e['path'],root)
        assert path.stat().st_size==e['bytes'] and file_hash(path)==e['sha256']
        receipt=json.loads((path.parent/'receipt.json').read_text())
        assert digest({k:v for k,v in receipt.items() if k!='receipt_hash'})==e['receipt_hash']
        assert any(v['name']==path.name and v['sha256']==e['sha256'] for v in receipt['files'])
        checked+=1;source=np.load(path,mmap_mode='r',allow_pickle=False)
        for index in np.linspace(e['start'],e['stop']-1,min(64,e['stop']-e['start']),dtype=np.int64):
            row=source[index].copy();start=int(row['end_us'][0]-row['timeframe_us']);end=int(row['end_us'][-1])
            if start<prior['audit_cutoff_us']:reasons['before_frozen_audit_boundary']+=1;continue
            key=(group,int(row['timeframe_us']))
            if any(start<=b and end>=a for a,b in spans[key]):reasons['overlapping_context']+=1;continue
            spans[key].append((start,end));selected.append(row)
            identities.append(dict(path=e['path'],sha256=e['sha256'],receipt_hash=e['receipt_hash'],group=group,
                index=int(index),start_us=start,end_us=end,sampling_weight=1.))
        if ix%20==0:write_json(output/'progress.json',dict(checked=checked,total=len(owners),selected=len(selected),seconds=time.time()-started))
    rows=np.array(selected,dtype=source.dtype);validate_complete_market(rows)
    assert set(rows['timeframe_us'])==set(FRAMES)
    np.save(output/'audit.npy',rows,allow_pickle=False);write_json(output/'audit-identities.json',identities)
    coverage={}
    for f in FRAMES:
        r=rows[rows['timeframe_us']==f];prices=r['values'][:,-1,3]
        coverage[str(f)]=dict(count=len(r),price_bands=np.bincount(np.digitize(prices,[.1,1,5,20,100]),minlength=6).tolist(),
            valid_counts=r['valid'].sum((0,1)).tolist(),min_price=float(prices.min()),max_price=float(prices.max()))
    result=dict(version='v8-learned-only-new-audit-groups',parent_hash=prior['hash'],source_split_hash=authority['manifest_hash'],
        verified_dependency=dependency,source_split_file_sha256=file_hash(split),
        seed=2026111708,audit_cutoff_us=prior['audit_cutoff_us'],excluded_exposed_groups=len(excluded),
        policy='Exact prior train/development/tiny; fresh audit excludes both previous campaigns audit and development groups, after frozen chronological boundary. Original full validation was previously aggregate-evaluated by V7: this is a newly sealed subset, not a pristine external test. Uniform selected weights; no population claim.',
        coverage=dict(train=prior['coverage']['train'],development=prior['coverage']['development'],audit=coverage),
        rejections=dict(reasons),verified_source_files=checked,elapsed_seconds=time.time()-started,
        files={p.name:file_hash(p) for p in output.iterdir() if p.suffix=='.npy' or p.name.endswith('-identities.json')})
    result['hash']=digest(result);write_json(output/'manifest.json',result)
    return dict(hash=result['hash'],audit_rows=len(rows),seconds=result['elapsed_seconds'],coverage=coverage)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--old',type=Path,required=True);p.add_argument('--split',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--previous',type=Path,nargs='+',required=True);a=p.parse_args()
    print(json.dumps(prepare(a.old,a.split,a.output,a.root,a.previous)))

