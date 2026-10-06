"""Create an explicit V4 file-input plan; never load validation features."""
import argparse,json
from pathlib import Path
from .runtime import require_runtime,write_json,file_hash

def identity_projection(root,output):
    root=Path(root);manifest=json.loads((root/'bank'/'complete.json').read_text())
    mapping={}
    for path in sorted((root/'fragments').glob('*/complete.json')):
        metadata=json.loads(path.read_text())
        identity=metadata['listing_id'];ticker=metadata['report']['ticker']
        if identity in mapping:raise ValueError('Duplicate listing identity fragment')
        mapping[identity]=ticker
    if set(mapping)!=set(manifest['offsets']) or len(set(mapping.values()))!=len(mapping):raise ValueError('Feature identity fragments do not cover bank')
    value=dict(bank_certificate_sha256=file_hash(root/'complete.json'),listing_to_ticker=mapping,source='producer feature fragment identity metadata only; no labels loaded')
    path=output/(root.name+'-identity.json')
    if path.exists() and json.loads(path.read_text())!=value:raise ValueError('Identity projection changed')
    write_json(path,value);return path

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--v3-job',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv)
    output=require_runtime(args.output);runtime=Path('D:/TradingML/runtimes')
    original=json.loads((args.v3_job/'sessions.json').read_text());spec=dict(version='v4-offline-inputs-v1',training=[],validation=[],origin_sessions_sha256=file_hash(args.v3_job/'sessions.json'),feature_schema='rl-trading-actual-candles-features-v6',v6_labels_or_models_used=False)
    producer=runtime/'rl-v6-reporting-repair-20261002'/'banks-us-listed-v1';extension=runtime/'rl-v6-validation-extension-20261005'/'banks';missing=[];previous=None;previous_split=None
    for role in ('training','validation'):
        for i,s in enumerate(original[role]):
            day=s['day'];root=producer/day if (producer/day/'complete.json').exists() else extension/day if (extension/day/'complete.json').exists() else output/'features'/day
            mapping=root/'identity_map.json'
            if role=='training' and (root/'complete.json').exists() and not mapping.exists():mapping=identity_projection(root,require_runtime(output/'identity_maps'))
            entry=dict(day=day,feature_root=str(root),identity_map=str(mapping),previous_feature_root=str(previous) if previous else None,
                split_certificate=str(output/'split_evidence'/f'{day}.json'),
                previous_split_certificate=previous_split,
                execution_root=str(args.v3_job/'experiment'/'inputs'/f'{role}_{i:03d}'),source_manifest=s['manifest'],source_ledger=s['ledger'])
            spec[role].append(entry)
            if role=='training' and not (root/'complete.json').exists():missing.append(dict(day=day,feature_root=str(root)))
            previous=root
            previous_split=entry['split_certificate']
    write_json(output/'sessions.json',spec);write_json(output/'coverage.json',dict(training_sessions=30,validation_sessions=6,missing_training_features=missing,validation_opened=False,notes='V4 owns its split; V6 labels and model seals untouched'))
    print(json.dumps(dict(sessions=str(output/'sessions.json'),missing_training_features=missing,validation_opened=False)))
    return 0

if __name__=='__main__':raise SystemExit(main())
