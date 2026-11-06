"""Hash-bound additive 1c sidecars; preserve all reviewed 1a and 1b values."""
from pathlib import Path
import json
import shutil
import polars as pl
from research.market_labels.v1.build import write_json
from research.market_labels.v3.build import verify_day as verify_parent
from research.market_shards.v1.contract import sha
from .contract import VERSION, SOURCE_SHA, SCOPE
from .trajectory import label

ALGORITHM='preserved-approved-execution-spread-1b'
GROUPING='preserved-1b-groups-single-entry-exit-1c'


def fingerprint():
    root=Path(__file__).resolve().parents[3]
    names=['research/market_labels/v4/'+n for n in ['build.py','run_build.py','contract.py','trajectory.py']]
    names+=['research/market_labels/v1/build.py','research/market_labels/v3/build.py','research/market_shards/v1/contract.py']
    return {n:sha(root/n) for n in names}


def verify_day(root,binding=None):
    root=Path(root);p=json.loads((root/'complete.json').read_bytes())
    if p['version']!=VERSION or (binding is not None and p['binding']!=binding):raise ValueError('1c binding changed')
    for name,record in p['files'].items():
        if sha(root/name)!=record['sha256'] or pl.scan_parquet(root/name).select(pl.len()).collect().item()!=record['rows']:
            raise ValueError('1c sealed file changed: '+name)
    if p['trajectory_audit']['entries']!=p['selected_pairs'] or p['trajectory_audit']['exits']!=p['selected_pairs']:
        raise ValueError('1c endpoint count differs')
    return p


def build_day(task):
    manifest,day,role,output,binding,parent_dataset=task
    root=Path(output)/day
    if (root/'complete.json').exists():return verify_day(root,binding)
    parent_dataset=Path(parent_dataset)
    if sha(parent_dataset)!=SOURCE_SHA or sha(parent_dataset)!=binding['input_labels_sha256']:raise ValueError('Accepted 1b identity changed')
    dataset=json.loads(parent_dataset.read_bytes());entry=next(d for d in dataset['days'] if d['day']==day and d['role']==role)
    source=parent_dataset.parent/day
    if sha(source/'complete.json')!=entry['certificate']:raise ValueError('1b day seal changed')
    parent=verify_parent(source,dataset['binding'])
    if dataset['binding']['source_manifests'][role]!=sha(manifest):raise ValueError('Market manifest changed')
    root.mkdir(parents=True,exist_ok=True)
    decisions=pl.read_parquet(source/'decisions.parquet');files={};records=[];actions={};total=0
    for i,record in enumerate(parent['listings']):
        record=dict(record);records.append(record)
        if record['valid_rows']:
            rows=pl.read_parquet(source/record['file'])
            result=label(rows,decisions.filter(pl.col('listing_id')==record['listing_id']))
            result.write_parquet(root/record['file'],compression='zstd')
            files[record['file']]=dict(rows=result.height,sha256=sha(root/record['file']))
            record['actions_1c']=result.group_by('action_1c').len().sort('action_1c').to_dicts()
            for a in record['actions_1c']:actions[a['action_1c']]=actions.get(a['action_1c'],0)+a['len']
            total+=result.height
        write_json(root/'progress.json',dict(status='derive 1c',day=day,completed=i+1,total=len(parent['listings'])))
    for name,record in parent['files'].items():
        if name not in files:
            shutil.copyfile(source/name,root/name)
            if sha(root/name)!=record['sha256']:raise ValueError('Parent artifact copy changed')
            files[name]=record
    if total!=parent['valid_rows']:raise ValueError('1c row census differs')
    audit=dict(rows=total,entries=actions.get('ENTRY',0),exits=actions.get('EXIT',0),actions=actions,
        parent_dataset_sha256=SOURCE_SHA,parent_day_sha256=entry['certificate'])
    p=dict(parent,version=VERSION,binding=binding,scope=SCOPE,files=files,listings=records,trajectory_audit=audit)
    write_json(root/'complete.json',p)
    return verify_day(root,binding)
