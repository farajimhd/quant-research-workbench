"""Versioned 24/12 bindings; preserve original certified bytes and exposure."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,importlib.util,json,subprocess
from pathlib import Path
from .prepare_enriched import calculation_hash,definition
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash,write_json

read=lambda p:json.loads(Path(p).read_bytes())


def artifact(source,target,record):
    target.mkdir(parents=True,exist_ok=False)
    names=list(record.get('files',{})) or ['values.npy']
    for name in names:
        if Path(name).name!=name:raise ValueError('Artifact path escapes sealed session')
        os.link(source/name,target/name)
    write_json(target/'complete.json',record)


def junction(source,target):
    if target.exists():raise ValueError('Binding already exists')
    target.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['cmd','/c','mklink','/J',str(target),str(source)],check=True,capture_output=True)


def migrate_enriched(source,target,input_hash,role,prior_code):
    old_path=Path(prior_code)/'research/vectorized_backtest/v18/prepare_enriched.py'
    old_hash=calculation_hash(old_path)
    # Pinned identical prior calculation, serialized by the laptop/workstation
    # Python AST versions respectively. Original producer verification below
    # additionally binds its full certificate and unchanged math bytes.
    unique_scope_producers={'51ce9686b65b5a3bd0598ce5c706a295f316c497253be4ad027f1f97202cd62e',
        '9dcfecde08928ac400c581ef49540aa35c8a115cb94a84d70828184a06e4428a'}
    if old_hash not in {calculation_hash(),*unique_scope_producers}:raise ValueError('Cannot reuse changed feature calculations')
    if file_hash(old_path.with_name('enriched_math.py'))!=definition()['math_sha256']:raise ValueError('Feature math changed')
    spec=importlib.util.spec_from_file_location('research.vectorized_backtest.v22._repartition_prior',old_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    previous=read(source/'complete.json')
    module.verify(source,previous['input_sha256'],'validation' if previous['validation_opened'] else 'training')
    record=dict(previous,definition=definition(),input_sha256=input_hash,validation_opened=role=='validation',
        repartition_provenance=dict(version='v18-24-12-binding-v1',source_receipt_sha256=file_hash(source/'complete.json'),source_input_sha256=previous['input_sha256'],source_validation_opened=previous['validation_opened'],calculation_sha256=calculation_hash(),source_calculation_sha256=old_hash,
            compatibility='Identical calculations, or pinned prior producer that required unique snapshot listings: alias handling is inert on its sealed days'))
    artifact(source,target,record)


def prepare(training,validation,prepared_enriched,prior_code,output):
    output=require_runtime(output)
    if any(output.iterdir()):raise ValueError('Fresh repartition runtime directory required')
    originals={role:read(path) for role,path in (('training',training),('validation',validation))}
    days=originals['training']['days']+originals['validation']['days']
    if len(days)!=36 or days!=sorted(set(days)):raise ValueError('Exactly 36 chronological original sessions required')
    roots={key:output/key for key in ('inputs','history','feature_cache','indicator_root')}
    enriched=output/'enriched';enriched.mkdir()
    bindings={role:dict(version='v8-split-manifest-v1',role=role,days=days[:24] if role=='training' else days[24:],
        **{key:str(path) for key,path in roots.items()},receipts={},indicator_receipts={},
        split_provenance=dict(version='v18-24-12-binding-v1',prior_training_manifest_sha256=file_hash(training),prior_validation_manifest_sha256=file_hash(validation),prior_training_exposure=days[24:30] if role=='validation' else [])) for role in originals}
    if not originals['validation'].get('feature_cache'):bindings['validation'].pop('feature_cache')
    previous_indicator=None
    for day in days:
        role='training' if day in days[:24] else 'validation'
        prior_role='training' if day in originals['training']['days'] else 'validation'
        original=originals[prior_role];moved=role!=prior_role;input_hash=original['receipts'][day]['inputs']
        lineage=dict(version='v18-24-12-binding-v1',prior_role=prior_role,role=role,prior_training_exposure=prior_role=='training')
        for key in ('inputs','history','feature_cache'):
            if key=='feature_cache' and not original.get(key):continue
            source=Path(original[key])/day;target=roots[key]/day
            if not moved:junction(source,target);continue
            record=read(source/'complete.json');record['repartition_provenance']=dict(lineage,source_receipt_sha256=file_hash(source/'complete.json'));record['validation_opened']=True
            if key=='inputs':
                if file_hash(source/'complete.json')!=original['receipts'][day]['inputs']:raise ValueError('Input certificate changed')
                record['identity']=dict(record['identity'],version='v8-validation-sparse-compact-input-v1');record['split_role']='validation'
            elif key=='history':record['input_receipt_sha256']=file_hash(roots['inputs']/day/'complete.json')
            else:
                record['identity']=dict(record['identity'],source=dict(input_sha256=file_hash(roots['inputs']/day/'complete.json'),history_sha256=file_hash(roots['history']/day/'complete.json')))
            artifact(source,target,record)
        input_hash=file_hash(roots['inputs']/day/'complete.json')
        source=Path(original['indicator_root'])/day;target=roots['indicator_root']/day;record=read(source/'complete.json')
        if file_hash(source/'complete.json')!=original['indicator_receipts'][day]:raise ValueError('Indicator certificate changed')
        record.update(input_sha256=input_hash,validation_opened=role=='validation',predecessor_receipt_sha256=previous_indicator,
            repartition_provenance=dict(lineage,source_receipt_sha256=file_hash(source/'complete.json')))
        artifact(source,target,record);previous_indicator=file_hash(target/'complete.json')
        bindings[role]['receipts'][day]={key:file_hash(roots[key]/day/'complete.json') for key in ('inputs','history')}
        bindings[role]['indicator_receipts'][day]=previous_indicator
        source=Path(prepared_enriched)/day
        if (source/'complete.json').exists():migrate_enriched(source,enriched/day,input_hash,role,prior_code)
        write_json(output/'status.json',dict(status='binding',day=day,training_sessions=24,validation_sessions=12))
    for role,binding in bindings.items():write_json(output/('base-'+role+'.json'),binding)
    write_json(output/'complete.json',dict(status='complete',training_sessions=24,validation_sessions=12,
        training_sha256=file_hash(output/'base-training.json'),validation_sha256=file_hash(output/'base-validation.json'),prior_training_exposure=days[24:30]))
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('training','validation','prepared-enriched','prior-code','output'):p.add_argument('--'+key,type=Path,required=True)
    prepare(**vars(p.parse_args()))

if __name__=='__main__':main()
