"""Frozen failed-unit selection; completed source receipts are never rewritten."""
import json,re
from pathlib import Path
from pipelines.certified_research.contracts import digest


def select(manifest, *, population_hash, start, end, runtime):
    body=dict(manifest);sealed=body.pop('manifest_hash',None)
    if sealed!=digest(body) or body.get('version')!='qualified-blocked-retry-v1':
        raise ValueError('Invalid sealed blocked retry manifest')
    source=Path(body['source_run_dir']).resolve()
    if not source.is_relative_to(Path(runtime).resolve()):raise ValueError('Retry source outside runtime root')
    request=json.loads((source/'request.json').read_text())
    if (request['population_hash']!=population_hash or request['start']!=str(start) or request['end']!=str(end)
            or body['source_request_hash']!=digest(request)):
        raise ValueError('Retry source request changed')
    census=json.loads((source/'census.json').read_text())
    if census['census_hash']!=digest(census['documents']) or census['census_hash']!=body['source_census_hash']:
        raise ValueError('Retry source census changed')
    known={digest(row):row for row in census['documents']}
    targets=body['documents']
    if not 0<=len(targets)<=min(250000,len(known)) or len({digest(row) for row in targets})!=len(targets):
        raise ValueError('Invalid bounded retry targets')
    selected=[];completed=[]
    for row in targets:
        token=digest(row)
        if not re.fullmatch('[0-9a-f]{64}',token) or known.get(token)!=row:
            raise ValueError('Retry target not in original census')
        receipt=source/'receipts'/(token+'.json')
        if receipt.exists():
            saved=json.loads(receipt.read_text())
            if saved['document']!=row or saved['publication']['state'] not in ('published','reused') or saved.get('reuse_verified') is not True:
                raise ValueError('Invalid completed source receipt')
            completed.append(dict(document=row,receipt_hash=digest(saved)))
            continue
        blocked=json.loads((source/'blocked'/(token+'.json')).read_text())
        if blocked['document']!=row:raise ValueError('Blocked source identity changed')
        selected.append(row)
    return selected,completed


def refresh(manifest):
    """After drain, seal all current blocked census units; no source mutation."""
    source=Path(manifest['source_run_dir'])
    request=json.loads((source/'request.json').read_text());census=json.loads((source/'census.json').read_text())
    if digest(request)!=manifest['source_request_hash'] or census['census_hash']!=manifest['source_census_hash'] or digest(census['documents'])!=census['census_hash']:
        raise ValueError('Cannot expand retries across changed source identities')
    if len(census['documents'])>250000:raise ValueError('Source census exceeds bounded retry metadata')
    documents=[]
    for row in census['documents']:
        name=digest(row)+'.json'
        if not (source/'receipts'/name).exists() and (source/'blocked'/name).exists():documents.append(row)
    body={k:v for k,v in manifest.items() if k not in ('manifest_hash','documents')}
    body.update(documents=documents,parent_manifest_hash=manifest['manifest_hash'])
    return dict(body,manifest_hash=digest(body))
