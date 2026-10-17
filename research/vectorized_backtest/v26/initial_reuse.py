"""Explicit, sealed V21 generation-one import. Never import old fitness or offspring."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
from .run_search import digest
from .objectives import local_panel, Objective
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash, write_json, require_runtime

VERSION='v26-v21-initial-reuse-v1'
SOURCES={
    '4f3050128423b96b1509046795fb2d31cd799035155d0ba4f26e341cad3aafdb',
    '668f44021b9075ef5b17e3071af5d802f42143263b3849f4f2ccaabf3a51e650',
}


def physical(path):
    """Resolve retained Windows generation junctions without changing them."""
    path=Path(path)
    if path.is_junction():
        return Path(os.readlink(path).removeprefix('\\\\?\\'))
    return path


def prepare(local_root, output):
    """Pin existing raw evidence. Payload and current input checks occur at import."""
    sessions={}
    for path in sorted(Path(local_root).iterdir()):
        if not path.is_dir() or not (path/'checkpoint.json').exists():continue
        saved=json.loads((path/'checkpoint.json').read_bytes())
        if digest(saved['state'])!=saved['state_sha256']:raise ValueError('V21 checkpoint integrity changed')
        folder=physical(path/'generation-0001')
        origin=json.loads((folder.parent/'checkpoint.json').read_bytes())
        if digest(origin['state'])!=origin['state_sha256']:raise ValueError('Original checkpoint integrity changed')
        contract=origin['contract']
        seal=json.loads((folder/'complete.json').read_bytes())
        if saved['state']['seals']['1']!=file_hash(folder/'complete.json'):raise ValueError('V21 initial seal changed')
        if origin['state']['seals']['1']!=file_hash(folder/'complete.json'):raise ValueError('Original initial seal changed')
        for name,key in [('scores.json','scores_sha256'),('births.json','births_sha256'),(path.name+'/receipt.json','receipt_sha256')]:
            if file_hash(folder/name)!=seal[key]:raise ValueError('V21 initial evidence changed')
        if contract['version']!='v21-local-session-v1' or contract['source_sha256'] not in SOURCES:raise ValueError('Unsupported initial source')
        if contract['population']!=1024 or contract['initial_multiplier']!=6 or contract['seed']!=2020:raise ValueError('Original sixfold pool required')
        receipt=json.loads((folder/path.name/'receipt.json').read_bytes())
        if receipt['validation_opened'] is not False or receipt['split']!='training' or not receipt['full_session']:raise ValueError('Training-only full session required')
        if len(receipt['metrics']['net_pnl'])!=6144:raise ValueError('Original sixfold pool required')
        local_panel([receipt['metrics']],Objective())
        sessions[path.name]=dict(contract=contract,receipt=str(folder/path.name/'receipt.json'),receipt_sha256=seal['receipt_sha256'],
                                 seal=str(folder/'complete.json'),seal_sha256=file_hash(folder/'complete.json'),population_sha256=seal['population_sha256'])
    if len(sessions)!=24:raise ValueError('All 24 original training pools required')
    manifest=dict(version=VERSION,sessions=sessions,validation_opened=False)
    write_json(require_runtime(Path(output).parent)/Path(output).name,manifest)
    return manifest


def import_receipt(manifest,day,contract,token,size,output,identity):
    if manifest.get('version')!=VERSION or manifest.get('validation_opened') is not False:raise ValueError('Invalid reuse manifest')
    entry=manifest['sessions'][day];prior=entry['contract']
    if prior['source_sha256'] not in SOURCES or prior['version']!='v21-local-session-v1':raise ValueError('Unsupported initial source')
    for key in ('day','seed','population','initial_multiplier','dataset','execution','feature_semantics','backend','device','batch_size'):
        if prior[key]!=contract[key]:raise ValueError('Initial reuse contract changed: '+key)
    if size!=6144 or contract['population']!=1024 or contract['initial_multiplier']!=6:raise ValueError('Original sixfold pool required')
    if entry['population_sha256']!=token:raise ValueError('Reconstructed initial population changed')
    if file_hash(entry['seal'])!=entry['seal_sha256'] or file_hash(entry['receipt'])!=entry['receipt_sha256']:raise ValueError('Initial reuse evidence changed')
    seal=json.loads(Path(entry['seal']).read_bytes());record=json.loads(Path(entry['receipt']).read_bytes())
    if seal['receipt_sha256']!=entry['receipt_sha256'] or seal['population_sha256']!=token:raise ValueError('Initial seal identity changed')
    if record['population_sha256']!=token or record['identity']!=identity or record['execution']!=contract['execution']:raise ValueError('Initial receipt identity changed')
    if record['day']!=day or record['split']!='training' or record['validation_opened'] is not False or not record['full_session']:raise ValueError('Training-only full session required')
    if len(record['metrics']['net_pnl'])!=size:raise ValueError('Initial candidate alignment changed')
    local_panel([record['metrics']],Objective())
    target=Path(output)/'generation-0001'/day/'receipt.json'
    record=dict(record,reuse_provenance=dict(version=VERSION,source_receipt=entry['receipt'],source_receipt_sha256=entry['receipt_sha256'],
                 source_seal_sha256=entry['seal_sha256'],source_contract=prior,old_scores_imported=False))
    write_json(target,record)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.local_root,args.output)


if __name__=='__main__':main()
