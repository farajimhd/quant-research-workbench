import json
from pathlib import Path
from research.vectorized_backtest.v18.run_train import predecessor_complete

def write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v))

def test_only_successful_complete_predecessor(tmp_path):
    active=dict(campaign=str(tmp_path),pid=99999999,creation_time=0.)
    write(tmp_path/'checkpoint.json',dict(contract={'generations':2},state={'completed':1}))
    assert not predecessor_complete(active)
    write(tmp_path/'checkpoint.json',dict(contract={'generations':2},state={'completed':2}))
    write(tmp_path/'exit.json',{'exit_code':130});assert not predecessor_complete(active)
    write(tmp_path/'exit.json',{'exit_code':0});assert not predecessor_complete(active)
    for role in ('training','validation'):write(tmp_path/role/'generation-0002/complete.json',{})
    assert predecessor_complete(active)
    (tmp_path/'owner.lock').touch();assert not predecessor_complete(active)

def test_enriched_integrity_resume(tmp_path):
    from research.vectorized_backtest.v18.prepare_enriched import verify,definition
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    import pytest
    (tmp_path/'values.npy').write_bytes(b'fixture')
    write(tmp_path/'complete.json',dict(status='complete',definition=definition(),input_sha256='source',validation_opened=False,files={'values.npy':file_hash(tmp_path/'values.npy')}))
    assert verify(tmp_path,'source','training')['status']=='complete'
    with pytest.raises(ValueError):verify(tmp_path,'changed','training')
    with pytest.raises(ValueError):verify(tmp_path,'source','validation')
    (tmp_path/'values.npy').write_bytes(b'changed')
    with pytest.raises(ValueError):verify(tmp_path,'source','training')
