"""Durable per-candidate investigation inventory; never closes untested work."""
import argparse
import json
from pathlib import Path
from .distributed_assets import sha
from .identity import digest
from .publication import write_json
from .review_contract import diagnose,CHECKS


def reconcile(packet,out,job):
    packet=Path(packet);out=Path(out);job=Path(job)
    source=json.loads((packet/'packet.json').read_bytes())
    parents=json.loads((packet/'parents.json').read_bytes())
    config=json.loads(job.read_bytes())
    if source['scope']!='training' or source['training_sha256']!=config['training_sha256']:
        raise ValueError('Training identity mismatch')
    out.mkdir(parents=True,exist_ok=True)
    previous=json.loads((out/'index.json').read_bytes())['tasks'] if (out/'index.json').exists() else []
    rows=[]
    for parent in sorted(parents,key=lambda p:(p['serial']!=17006,p['serial'])):
        review=diagnose(parent)
        identity=dict(parent_sha256=review['parent_sha256'],training_sha256=config['training_sha256'],
                      execution=config['execution'],objective=config['objective'],checks=list(CHECKS))
        key=digest(identity);folder=out/key
        folder.mkdir(parents=True,exist_ok=True)
        task=folder/'task.json'
        if task.exists():
            old=json.loads(task.read_bytes())
            if old['identity']!=identity:raise ValueError('Backlog identity changed')
        else:
            write_json(task,dict(identity=identity,serial=parent['serial'],payload=parent['payload'],
                endpoints=review['endpoints'],checks=review['checks'],status='investigation_required',
                completion_rule='All endpoints and ten checks have supported outcomes; full training comparison before admission.'))
        # Fresh source binding is separate; persistent evidence is never discarded per epoch.
        write_json(folder/'endpoint-contract.json',dict(parent_sha256=review['parent_sha256'],
            endpoints=review['endpoints'],instruction='Interpret catalog units/bounds and transforms, never infer raw prices from names.'))
        write_json(folder/'current-source.json',dict(serial=parent['serial'],generation=source['source_generation'],
            packet=str(packet),parents_sha256=sha(packet/'parents.json'),job=str(job),job_sha256=sha(job)))
        rows.append(dict(serial=parent['serial'],key=key,task=str(task),endpoints=len(review['endpoints'])))
    current={row['key'] for row in rows}
    rows.extend(row for row in previous if row['key'] not in current)
    result=dict(scope='training',source_generation=source['source_generation'],tasks=rows,
                rule='Resume existing unfinished tasks; never interpret inventory creation as completed investigation.')
    write_json(out/'index.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--packet',required=True);p.add_argument('--out',required=True);p.add_argument('--job',required=True)
    a=p.parse_args();result=reconcile(a.packet,a.out,a.job)
    print(json.dumps(dict(candidates=len(result['tasks']),endpoints=sum(r['endpoints'] for r in result['tasks']))))
