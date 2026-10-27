"""Self-contained sealed session evidence transferred before global promotion."""
import json
from pathlib import Path
from .identity import digest
from .lineage import program_key
from .genome import Individual
from research.vectorized_backtest.v42.distributed_assets import sha,copy_verified
from research.vectorized_backtest.v42.publication import write_json

def records(folder,job,day):
    folder=Path(folder);meta=json.loads((folder/'export.json').read_bytes())
    if meta['day']!=day or meta['job_sha256']!=digest(job):raise ValueError('Export campaign/day changed')
    for name,expected in meta['files'].items():
        if Path(name).name!=name or sha(folder/name)!=expected:raise ValueError('Export evidence changed: '+name)
    read=lambda name:json.loads((folder/name).read_bytes())
    cp=read('checkpoint.json');state=cp['state'];c=cp['contract'];seal=read('seal.json');training=read('training.json');population=read('population.json');final=read('final.json')
    if digest(state)!=cp['state_sha256'] or state['completed']!=job['phase1_generations'] or c['source_sha256']!=job['source_sha256'] or c['objective']!=job['objective'] or c['execution']!=job['execution'] or c['training_days']!=[day] or c['validation_days']:raise ValueError('Session checkpoint contract changed')
    if c.get('campaign_context',{}).get('parent_job_sha256')!=digest(job):raise ValueError('Session belongs to a different full campaign')
    if sha(folder/'checkpoint.json')!=final['checkpoint_sha256'] or final['completed']!=job['phase1_generations']:raise ValueError('Session final checkpoint changed')
    if state['seals'][str(state['completed'])]!=sha(folder/'seal.json'):raise ValueError('Session seal link changed')
    for name,key in (('population.json','population_sha256'),('training.json','training_sha256'),('pedigree.json','evaluated_pedigree_sha256'),('validation.json','validation_sha256')):
        if sha(folder/name)!=seal[key]:raise ValueError('Sealed export changed: '+name)
    if read('validation.json')['opened']:raise ValueError('Session export contains validation')
    token=digest(population['population'])
    if token!=population['population_sha256'] or token!=training['population_sha256'] or population['ids']!=training['ids'] or population['population']!=state['population'] or population['ids']!=state['ids']:raise ValueError('Session population alignment changed')
    receipt=read('receipt.json');ref=training['receipts'][day]
    if sha(folder/'receipt.json')!=ref['receipt_sha256'] or receipt['population_sha256']!=token or not receipt['full_session'] or receipt['split']!='training' or receipt['validation_opened'] or receipt['execution']!=job['execution']:raise ValueError('Session financial receipt changed')
    from .full_common import aggregate,serializable
    from .objectives import Objective
    if serializable(aggregate([receipt['metrics']],Objective(**job['objective'])))!=training['panel']:raise ValueError('Session source scores do not reconcile')
    panel=training['panel'];ids=training['ids'];pedigree={x['serial']:x for x in read('pedigree.json')};seen=set();out=[]
    for i in sorted(range(len(ids)),key=lambda i:(-panel['score'][i],ids[i])):
        if not panel['eligible'][i] or panel['closed_positions'][i]<=0:continue
        m=Individual.restore(population['population'][i]);key=program_key(m)
        if key in seen:continue
        if m.case_owner is not None or pedigree[ids[i]]['program_sha256']!=key:raise ValueError('Promoted pedigree mismatch')
        seen.add(key);out.append(dict(payload=m.payload(),program_sha256=key,serial=ids[i],generation=state['completed'],score=panel['score'][i],pedigree=pedigree[ids[i]]))
        if len(out)==job['expert_capacity_per_session']:break
    return dict(day=day,source_sha256=job['source_sha256'],objective=job['objective'],records=out,export_sha256=sha(folder/'export.json'),validation_opened=False)

def publish(source,target,job,day):
    source=Path(source)/'global';target=Path(target);target.mkdir(parents=True,exist_ok=True)
    f=source/f"generation-{job['phase1_generations']:04d}";training=json.loads((f/'training.json').read_bytes());ref=training['receipts'][day]
    files=dict(checkpoint=source/'checkpoint.json',final=source/'complete.json',seal=f/'complete.json',population=f/'population.json',training=f/'training.json',pedigree=f/'evaluated-pedigree.json',validation=f/'validation.json',receipt=Path(ref['receipt']))
    hashes={}
    for name,p in files.items():
        name+='.json';hashes[name]=sha(p);copy_verified(p,target/name,hashes[name])
    meta=dict(day=day,job_sha256=digest(job),files=hashes)
    if (target/'export.json').exists():
        if json.loads((target/'export.json').read_bytes())!=meta:raise ValueError('Existing export differs')
    else:
        from research.vectorized_backtest.v42.distributed_storage import publish as publish_transport
        publish_transport(target,target/'export.json',meta)
    return records(target,job,day)
