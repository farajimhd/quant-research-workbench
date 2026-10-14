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


def test_authorized_handoff_requires_matching_checkpoint_and_seals(tmp_path):
    from research.vectorized_backtest.v18.run_train import predecessor_ready
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
    active=dict(campaign=str(tmp_path),pid=99999999,creation_time=0.)
    write(tmp_path/'active.json',active)
    write(tmp_path/'checkpoint.json',dict(contract={'generations':50},state={'completed':3}))
    write(tmp_path/'exit.json',{'exit_code':130})
    write(tmp_path/'status.json',dict(status='interrupted',stage='Stopped at generation boundary'))
    for role in ('training','validation'):write(tmp_path/role/'generation-0003/complete.json',{})
    proof=dict(authorization='user-requested-v18-ready-handoff',campaign=str(tmp_path),generation=3,processes=[dict(pid=active['pid'],creation_time=0.)],active_sha256=file_hash(tmp_path/'active.json'),checkpoint_sha256=file_hash(tmp_path/'checkpoint.json'))
    for role in ('training','validation'):proof[role+'_seal_sha256']=file_hash(tmp_path/role/'generation-0003/complete.json')
    path=tmp_path/'stop-proof.json';write(path,proof)
    assert not predecessor_ready(active) and predecessor_ready(active,path)
    write(tmp_path/'checkpoint.json',dict(contract={'generations':50},state={'completed':4}))
    assert not predecessor_ready(active,path)


def test_first_generation_monitor_checks_risk_and_real_wall(tmp_path):
    from research.vectorized_backtest.v18.run_after_v17 import inspect_first_generation
    q=tmp_path/'qualification.json';write(q,dict(full_generation_seconds={'v17':[100.,90.,90.,90.]}))
    assert inspect_first_generation(tmp_path,q) is None
    for role in ('training','validation'):write(tmp_path/role/'generation-0001/complete.json',{})
    folder=tmp_path/'training/generation-0001'
    write(folder/'wall.json',dict(full_generation_seconds=95.))
    rows=[dict(valid=True,metrics=dict(total_pnl=1.,maximum_position_risk=200.,maximum_stop_distance=.15))]
    write(folder/'ranking.json',rows)
    assert inspect_first_generation(tmp_path,q)['status']=='passed'
    write(folder/'wall.json',dict(full_generation_seconds=101.))
    assert inspect_first_generation(tmp_path,q)['status']=='failed'
    rows[0]['metrics']['maximum_position_risk']=301.;write(folder/'ranking.json',rows)
    assert 'invariants' in inspect_first_generation(tmp_path,q)['reason']
