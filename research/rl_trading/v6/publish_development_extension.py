"""Compose audited immutable publications, admitting development dates only."""
import argparse
import copy
import json
from pathlib import Path
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.dataset_admission import VERSION, inventory, verify_market
from research.rl_trading.v6.validation_split import dataset_split

def read(path, sha=None):
    path=Path(path)
    if sha and file_hash(path)!=sha:raise ValueError('Pinned publication changed')
    data=json.loads(path.read_bytes())
    if 'hash' in data and data['hash']!=digest({k:v for k,v in data.items() if k!='hash'}):
        raise ValueError('Publication digest changed')
    return data

def write(path, data, hashed=False):
    if hashed:data['hash']=digest({k:v for k,v in data.items() if k!='hash'})
    path=Path(path)
    raw=(json.dumps(data,sort_keys=True)+'\n').encode()
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('Immutable output already differs')
    else:path.write_bytes(raw)
    return file_hash(path)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-root',type=Path,default=Path('D:/TradingML/runtimes'))
    p.add_argument('--extension',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-commit',required=True)
    p.add_argument('--publish',action='store_true')
    a=p.parse_args(argv);runtime=a.runtime_root.resolve();output=a.output.resolve();ext=a.extension.resolve()
    if not runtime.is_dir() or not all(x.is_relative_to(runtime) for x in (output,ext)):
        raise ValueError('Explicit runtime roots required')
    if len(a.source_commit)!=40:raise ValueError('Exact committed source required')
    output.mkdir(parents=True,exist_ok=True)
    # Save the prior registries before any mutation; repeated publication binds
    # to these immutable originals, never recursively composes its own output.
    for name in ('rl-v6-active-labels.json','rl-v6-active-market-teacher.json'):
        prior=output/('prior-'+name)
        if not prior.exists():prior.write_bytes((runtime/name).read_bytes())
    old_a=read(output/'prior-rl-v6-active-labels.json');old_b=read(output/'prior-rl-v6-active-market-teacher.json')
    one=read(old_a['dataset'],old_a['sha256']);two=read(old_b['dataset'],old_b['sha256'])
    completed=read(ext/'complete.json')
    if completed['status']!='complete' or completed['training_started'] is not False or completed['sealed_labels_exposed'] is not False:
        raise ValueError('Extension completion is unavailable')
    extra=read(ext/'labels-1a/dataset.json',completed['dataset_1a_sha256'])
    extra_b=read(ext/'labels-1b/dataset.json',completed['dataset_1b_sha256'])
    split=dataset_split(extra,runtime)
    if not split or extra_b['validation_split']!=extra['validation_split']:raise ValueError('Frozen split differs')
    if (any(one[k]!=extra[k] for k in ('version','algorithm','config','ranking','activity_source')) or
        any(two['binding'][k]!=extra_b['binding'][k] for k in ('version','grouping','config','grouping_sha256'))):
        raise ValueError('Extension changes approved feature or label math')
    sources=[];audits=[]
    for stage,dataset,sha in [('1a',one,old_a['sha256']),('1b',two,old_b['sha256']),
                              ('1a',extra,completed['dataset_1a_sha256']),('1b',extra_b,completed['dataset_1b_sha256'])]:
        audit=read(dataset['publication_audit'],dataset['publication_audit_sha256'])
        entries=([dataset['context']]+dataset['days']) if stage=='1a' else dataset['days']
        key='teacher_sha256' if stage=='1a' else 'sha256'
        if audit['status']!='passed' or audit['day_certificates']!={e['day']:e[key] for e in entries}:
            raise ValueError('Producer full-population audit mismatch')
        if stage=='1b' and audit['binding']!=dataset['binding']:raise ValueError('Producer 1b audit mismatch')
        sources.append(dict(stage=stage,dataset_sha256=sha,audit=dataset['publication_audit'],audit_sha256=dataset['publication_audit_sha256']))
        audits.append(audit)
    admitted=[d for d in split['days'] if split['roles'][d]=='development']
    merged=copy.deepcopy(one);merged.pop('hash',None)
    merged['days'] += [copy.deepcopy(e) for e in extra['days'] if e['day'] in admitted]
    for e in merged['days']:
        if e['day'] in admitted:e['split_manifest']=extra['validation_split']['path']
    admission=dict(version=VERSION,split=extra['validation_split'],admitted_days=[e['day'] for e in merged['days']],
        sealed_days=[d for d in split['days'] if split['roles'][d]=='sealed_test'],sources=sources,source_commit=a.source_commit)
    sha=write(output/'admission.json',admission,True)
    merged['development_admission']=dict(path=str(output/'admission.json'),sha256=sha)
    inventory(merged,runtime)
    all_a=[merged['context']]+merged['days']
    combined=copy.deepcopy(two);combined.pop('hash',None)
    combined['days']=[]
    for e in all_a:
        parent=extra_b if e['day'] in admitted else two
        item=copy.deepcopy(next(d for d in parent['days'] if d['day']==e['day']))
        item['producer_binding']=parent['binding'];combined['days'].append(item)
        if file_hash(Path(e['teacher_root'])/'complete.json')!=e['teacher_sha256']:
            raise ValueError('Original 1a day certificate changed')
        if file_hash(Path(e['bank_root'])/'complete.json')!=e['bank_certificate_sha256']:
            raise ValueError('Feature bank certificate changed')
        proof=read(Path(item['root'])/'complete.json',item['sha256'])
        if (proof['source_teacher_sha256']!=e['teacher_sha256'] or proof['binding']!=parent['binding']
            or item['rows']!=e['valid_rows'] or proof['rows']!=e['valid_rows']):
            raise ValueError('Copied 1b source identity changed')
        # Verify every admitted shard receipt; inherited producer audits cover
        # full parquet equality and sizing without recalculating any label.
        for shard in proof['shards']:
            read(Path(item['root'])/shard['path']/'complete.json',shard['sha256'])
    combined.pop('validation_split',None)
    combined['development_admission']=merged['development_admission']
    combined['source_dataset']=str(output/'dataset.json')
    combined['binding']=dict(two['binding'],source_binding_kind='day_certificates',
        source_sha256=digest({e['day']:e['teacher_sha256'] for e in all_a}),source_commit=a.source_commit)
    combined['rows']=sum(e['rows'] for e in combined['days'])
    totals=dict(rows=combined['rows'],shards=0,selected=0,groups=0,suppressed_rows=0)
    for e in combined['days']:
        proof=read(Path(e['root'])/'complete.json',e['sha256'])
        totals['shards']+=len(proof['shards']);totals['selected']+=proof['selected']
        totals['groups']+=len(proof['groups']);totals['suppressed_rows']+=proof['suppressed_rows']
    audit_b=dict(status='passed',version=combined['version'],binding=combined['binding'],
        day_certificates={e['day']:e['sha256'] for e in combined['days']},
        method='composition_of_pinned_full_population_audits',sources=sources,**totals)
    combined['publication_audit']=str(output/'publication-audit-1b.json')
    combined['publication_audit_sha256']=write(output/'publication-audit-1b.json',audit_b)
    market_sha=write(output/'market-teacher-dataset.json',combined,True)
    merged['market_teacher_dataset']=dict(path=str(output/'market-teacher-dataset.json'),sha256=market_sha)
    merged['teacher_stage']='1b'
    audit_a=dict(status='passed',algorithm=merged['algorithm'],ranking=merged['ranking'],
        day_certificates={e['day']:e['teacher_sha256'] for e in all_a},
        method='composition_of_pinned_full_population_audits',sources=sources)
    merged['publication_audit']=str(output/'publication-audit-1a.json')
    merged['publication_audit_sha256']=write(output/'publication-audit-1a.json',audit_a)
    dataset_sha=write(output/'dataset.json',merged,True)
    verify_market(merged,runtime)
    registries={'rl-v6-active-labels.json':dict(old_a,dataset=str(output/'dataset.json'),sha256=dataset_sha,
        publication_audit_sha256=merged['publication_audit_sha256']),
        'rl-v6-active-market-teacher.json':dict(old_b,dataset=str(output/'market-teacher-dataset.json'),sha256=market_sha)}
    if a.publish:
        for name,data in registries.items():
            existing=read(runtime/name)
            if existing not in (read(output/('prior-'+name)),data):raise ValueError('Concurrent registry update')
        for name,data in registries.items():
            temp=runtime/(name+'.pending');temp.write_text(json.dumps(data,sort_keys=True)+'\n',encoding='utf-8');temp.replace(runtime/name)
    write(output/'complete.json',dict(status='complete',published=a.publish,source_commit=a.source_commit,
        training_started=False,development_days=[e['day'] for e in merged['days'] if e['role']=='development'],
        train_days=[e['day'] for e in merged['days'] if e['role']=='train'],sealed_days=admission['sealed_days'],
        dataset_sha256=dataset_sha,market_dataset_sha256=market_sha,**totals))
    print(json.dumps(dict(status='complete',published=a.publish,days=len(merged['days']),**totals)))

if __name__=='__main__':main()
