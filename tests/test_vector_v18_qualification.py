import json
from hashlib import sha256
from pathlib import Path
import pytest
from research.vectorized_backtest.v18.qualify_v17 import validated_baseline
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash


def save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


def test_reuse_requires_identical_code_data_and_execution_contract(tmp_path):
    previous=tmp_path/'previous';code=tmp_path/'code';root=code/'research/vectorized_backtest';h=sha256()
    for version in ('v6','v7','v17'):
        p=root/version/'module.py';p.parent.mkdir(parents=True);p.write_bytes(b'x=1\n')
        h.update((version+'/module.py').encode());h.update(p.read_bytes())
    training=tmp_path/'training.json';validation=tmp_path/'validation.json';training.write_bytes(b'training');validation.write_bytes(b'validation')
    predecessor=tmp_path/'predecessor';save(predecessor/'checkpoint.json',{'sealed':True})
    times=[100.,90.,91.,92.];accounting=[10.,9.,9.,9.]
    q=dict(baseline_source_sha256=h.hexdigest(),full_generation_with_validation_qualified=True,
        training_sha256=file_hash(training),validation_sha256=file_hash(validation),predecessor_checkpoint_sha256=file_hash(predecessor/'checkpoint.json'),full_generation_seconds={'v17':times},matched_accounting_seconds={'v17':accounting})
    save(previous/'qualification.json',q)
    save(previous/'v17-accounting/profile.json',dict(source_sha256=h.hexdigest(),result_reuse=False,wall_seconds=accounting))
    contract=dict(population=1024,batch_size=512,session_workers=2,seed=182237,generations=4,backend='compile',device='cuda')
    checkpoint=dict(state={'completed':4},contract=contract);save(previous/'v17-generation/checkpoint.json',checkpoint)
    for g,time in enumerate(times,1):
        for role in ('training','validation'):save(previous/'v17-generation'/role/f'generation-{g:04d}'/'complete.json',{})
        save(previous/'v17-generation/training'/f'generation-{g:04d}'/'wall.json',dict(includes_selection_validation_publication=True,full_generation_seconds=time))
    args=(previous,training,validation,predecessor,code)
    assert validated_baseline(*args)[1]==times
    contract['seed']=1;save(previous/'v17-generation/checkpoint.json',checkpoint)
    with pytest.raises(ValueError,match='execution contract'):validated_baseline(*args)
    contract['seed']=182237;save(previous/'v17-generation/checkpoint.json',checkpoint)
    validation.write_bytes(b'changed')
    with pytest.raises(ValueError,match='dataset/checkpoint'):validated_baseline(*args)
    validation.write_bytes(b'validation');(root/'v17/module.py').write_bytes(b'x=2\n')
    with pytest.raises(ValueError,match='source'):validated_baseline(*args)
