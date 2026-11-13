"""Freeze verified coarse publications after an explicitly requested stop."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import numpy as np
import psutil
from .shards.contract import digest, file_hash, write_json
from .shards.phase1_contract import VERSION, SAMPLE_DTYPE
from .data_v3 import load_split, dataset_identity, read_receipt, validate_complete_market
from .cohort_v1 import members


def release(source, original_split, output, owner_ledger):
    source, output = Path(source).resolve(), Path(output).resolve()
    owner = json.loads(Path(owner_ledger).read_text())
    if Path(owner['output']).resolve() != source:
        raise ValueError('Source owner differs')
    if psutil.pid_exists(owner['pid']) and abs(psutil.Process(owner['pid']).create_time()-owner['created']) < .01:
        raise ValueError('Extraction must be fully stopped')
    if not (source/'STOP').is_file() or output.exists():
        raise ValueError('Explicit stopped source and fresh release root required')
    prior = load_split(Path(original_split))
    parent_root = Path(prior['dataset'])
    _, parents = members(parent_root, require_all=True)
    original_cohort = json.loads((parent_root/'cohort.json').read_text()) if (parent_root/'cohort.json').exists() else None
    base = json.loads((parents[0][1]/'contract.json').read_text())
    raw = json.loads((source/'contract.json').read_text())
    if digest(raw['contract']) != raw['binding'] or raw['contract']['cutoff_us'] != prior['cutoff_us']:
        raise ValueError('Source binding/cutoff differs')
    output.mkdir(); component = output.parent/(output.name+'-coarse')
    component.mkdir(exist_ok=False)
    contract = dict(version=VERSION, parent_binding=base['binding'], authority=base['contract']['authority'],
                    partial_source=str(source), partial_source_binding=raw['binding'])
    binding=digest(contract);write_json(component/'contract.json',dict(contract=contract,binding=binding))
    paths=sorted((source/'jobs').glob('*/*/receipt.json'))
    def audit(path):
        receipt=json.loads(path.read_text())
        if digest({k:v for k,v in receipt.items() if k!='receipt_hash'})!=receipt['receipt_hash'] or receipt['binding']!=raw['binding']:
            raise ValueError('Corrupt/foreign coarse receipt')
        split=receipt['split_name']; count=0
        for item in receipt['files']:
            p=path.parent/item['name']
            if p.parent!=path.parent or p.stat().st_size!=item['bytes'] or file_hash(p)!=item['sha256']:
                raise ValueError('Coarse payload changed')
            if not item['name'].startswith('samples-'):continue
            a=np.load(p,mmap_mode='r',allow_pickle=False)
            if a.dtype!=SAMPLE_DTYPE:raise ValueError('Coarse schema differs')
            validate_complete_market(a)
            good=a['end_us'].max(axis=1)<prior['cutoff_us'] if split=='train' else a['end_us'][:,0]-a['timeframe_us']>=prior['cutoff_us']
            if not good.all():raise ValueError('Coarse split crosses original cutoff')
            count+=len(a)
        if count!=receipt['samples']:raise ValueError('Coarse counts differ')
        return path,receipt
    entries={'train':[],'validation':[]}; inventory=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i,(path,receipt) in enumerate(pool.map(audit,paths)):
            target=component/'jobs'/path.parent.relative_to(source/'jobs');target.mkdir(parents=True)
            for item in receipt['files']:os.link(path.parent/item['name'],target/item['name'])
            new={k:v for k,v in receipt.items() if k!='receipt_hash'}
            new.update(binding=binding,day='coarse',source_receipt_hash=receipt['receipt_hash'])
            new['receipt_hash']=digest(new);write_json(target/'receipt.json',new)
            entries[receipt['split_name']].extend(read_receipt(target/'receipt.json',binding))
            inventory.append(dict(path=str(path),sha256=file_hash(path),samples=receipt['samples']))
            if i%500==0:print(json.dumps(dict(audited=i,total=len(paths))),flush=True)
    write_json(component/'result.json',dict(state='inventory_exhausted',failed=0,scope='closed verified partial publication inventory'))
    cohort=dict(version='bargpt-phase1-cohort-v1',schema=VERSION,members=[dict(name=n,path=os.path.relpath(p,output)) for n,p in parents]+[dict(name='coarse-supplement',path=os.path.relpath(component,output))])
    write_json(output/'cohort.json',dict(cohort=cohort,hash=digest(cohort)))
    snapshot=dict(state='released_partial',source=str(source),source_binding=raw['binding'],inventory=inventory,
                  stop_sha256=file_hash(source/'STOP'),counts={k:sum(e['rows'] for e in v) for k,v in entries.items()})
    snapshot['hash']=digest(snapshot);write_json(output/'supplement-snapshot.json',snapshot)
    manifest={k:v for k,v in prior.items() if k!='manifest_hash'}
    manifest.update(dataset=str(output),**dataset_identity(output))
    manifest['splits']={k:prior['splits'][k]+entries[k] for k in entries}
    manifest['counts']={k:sum(e['stop']-e['start'] for e in v) for k,v in manifest['splits'].items()}
    manifest['inventory_samples']=prior['inventory_samples']+sum(snapshot['counts'].values())
    manifest['train_fraction']=manifest['counts']['train']/sum(manifest['counts'].values())
    manifest['manifest_hash']=digest(manifest);write_json(output/'split.json',manifest)
    body=dict(version='bargpt-balanced-release-v4',training_ready=True,cohort_hash=digest(cohort),
              supplement_snapshot_sha256=file_hash(output/'supplement-snapshot.json'),
              split_sha256=file_hash(output/'split.json'),member_contracts={n:file_hash(p/'contract.json') for n,p in members(output,True)[1]})
    body['hash']=digest(body);write_json(output/'balanced-release.json',body)
    from .balanced_release_v4 import require_release
    require_release(output,output/'split.json');print(json.dumps(dict(released=True,new_counts=snapshot['counts'],counts=manifest['counts'])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--split',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--owner-ledger',type=Path,required=True);a=p.parse_args();release(a.source,a.split,a.output,a.owner_ledger)
