import json
from pathlib import Path
import numpy as np
from research.vectorized_backtest.v18 import prepare_enriched as preparation
from research.vectorized_backtest.v18.repartition import migrate_enriched
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def test_rebinding_preserves_values_and_original_receipt(tmp_path):
    source=tmp_path/'source';source.mkdir();np.save(source/'values.npy',np.array([[1.,np.nan]],dtype=np.float32))
    receipt=dict(status='complete',definition=preparation.definition(),input_sha256='old-input',validation_opened=False,files={'values.npy':file_hash(source/'values.npy')})
    (source/'complete.json').write_text(json.dumps(receipt));original=file_hash(source/'complete.json')
    prior=tmp_path/'prior';folder=prior/'research/vectorized_backtest/v18';folder.mkdir(parents=True)
    current=Path(preparation.__file__)
    for name in ('prepare_enriched.py','enriched_math.py'):(folder/name).write_bytes(current.with_name(name).read_bytes())
    target=tmp_path/'target';migrate_enriched(source,target,'new-input','validation',prior)
    result=preparation.verify(target,'new-input','validation')
    assert result['repartition_provenance']['source_receipt_sha256']==original
    assert result['repartition_provenance']['source_validation_opened'] is False
    assert file_hash(source/'complete.json')==original
    assert file_hash(source/'values.npy')==file_hash(target/'values.npy')


def test_24_12_binding_preserves_optional_validation_cache(tmp_path,monkeypatch):
    import shutil
    from datetime import date,timedelta
    from research.vectorized_backtest.v18 import repartition
    days=[str(date(2026,1,1)+timedelta(days=i)) for i in range(36)]
    manifests={}
    for role,selected in (('training',days[:30]),('validation',days[30:])):
        keys=('inputs','history','indicator_root','feature_cache') if role=='training' else ('inputs','history','indicator_root')
        roots={key:tmp_path/role/key for key in keys};record=dict(days=selected,receipts={},indicator_receipts={},**{key:str(root) for key,root in roots.items()})
        for day in selected:
            for key,root in roots.items():
                folder=root/day;folder.mkdir(parents=True);np.save(folder/'values.npy',np.array([1.],dtype=np.float32))
                certificate=dict(status='complete',validation_opened=role=='validation',files={'values.npy':file_hash(folder/'values.npy')},identity={'version':'fixture','source':{}})
                (folder/'complete.json').write_text(json.dumps(certificate))
            record['receipts'][day]={key:file_hash(roots[key]/day/'complete.json') for key in ('inputs','history')}
            record['indicator_receipts'][day]=file_hash(roots['indicator_root']/day/'complete.json')
        path=tmp_path/(role+'.json');path.write_text(json.dumps(record));manifests[role]=path
    def runtime(path):path=Path(path);path.mkdir();return path
    monkeypatch.setattr(repartition,'require_runtime',runtime)
    monkeypatch.setattr(repartition,'junction',lambda source,target:shutil.copytree(source,target))
    output=tmp_path/'binding';repartition.prepare(manifests['training'],manifests['validation'],tmp_path/'not-prepared',tmp_path/'prior',output)
    train=json.loads((output/'base-training.json').read_text());valid=json.loads((output/'base-validation.json').read_text())
    assert len(train['days'])==24 and len(valid['days'])==12
    assert 'feature_cache' in train and 'feature_cache' not in valid
    assert valid['split_provenance']['prior_training_exposure']==days[24:30]
    assert json.loads((output/'inputs'/days[24]/'complete.json').read_text())['validation_opened'] is True
