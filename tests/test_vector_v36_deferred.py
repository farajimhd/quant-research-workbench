import pytest
from research.vectorized_backtest.v36 import deferred_launch as gate


def fixture(tmp_path):
    root=tmp_path/'v35';campaign=root/'campaign';local=campaign/'local/2026-08-04'
    pointer=dict(pid=123,creation_time=12.,source_sha256='source',campaign=str(campaign),commit='commit')
    previous=dict(initial_pool='fixture');gate.write(root/'job.json',previous);gate.write(root/'active-campaign.json',pointer)
    old=dict(pointer,root=str(root),job_sha256=gate.digest(previous),descendants=[])
    job=dict(predecessor=old)
    gate.write(root/'launcher-exit.json',dict(exit_code=0));gate.write(campaign/'exit.json',dict(exit_code=0))
    seals={}
    for generation in range(1,51):
        folder=local/f'generation-{generation:04d}'
        gate.write(folder/'2026-08-04/receipt.json',dict(population_sha256='pop',validation_opened=False,full_session=True))
        gate.write(folder/'scores.json',{});gate.write(folder/'births.json',[])
        complete=dict(population_sha256='pop')
        for key,path in [('receipt',folder/'2026-08-04/receipt.json'),('scores',folder/'scores.json'),('births',folder/'births.json')]:complete[key+'_sha256']=gate.file_hash(path)
        gate.write(folder/'complete.json',complete);seals[str(generation)]=gate.file_hash(folder/'complete.json')
    state=dict(completed=50,seals=seals)
    gate.write(local/'checkpoint.json',dict(contract=dict(source_sha256='source'),state=state,state_sha256=gate.digest(state)))
    gate.write(local/'archive.json',[])
    gate.write(local/'complete.json',dict(status='complete',generations=50,validation_opened=False,checkpoint_sha256=gate.file_hash(local/'checkpoint.json'),archive_sha256=gate.file_hash(local/'archive.json')))
    return job,root,campaign,local


def test_waits_for_exact_owner_and_locks_before_any_compute(tmp_path,monkeypatch):
    job,root,campaign,local=fixture(tmp_path)
    monkeypatch.setattr(gate,'live',lambda _:True)
    assert gate.inspect_predecessor(job) is None
    monkeypatch.setattr(gate,'live',lambda _:False)
    (campaign/'owner.lock').touch()
    assert gate.inspect_predecessor(job) is None
    (campaign/'owner.lock').unlink()
    assert gate.inspect_predecessor(job)['completed']==50


def test_rejects_changed_pointer_and_preserves_failure(tmp_path,monkeypatch):
    job,root,campaign,local=fixture(tmp_path);monkeypatch.setattr(gate,'live',lambda _:False)
    gate.write(campaign/'failure.json',dict(error='fixture'))
    with pytest.raises(RuntimeError):gate.inspect_predecessor(job)
    assert (campaign/'failure.json').exists()
    (campaign/'failure.json').unlink()
    gate.write(root/'active-campaign.json',dict(job['predecessor'],pid=456))
    with pytest.raises(ValueError,match='pointer changed'):gate.inspect_predecessor(job)


def test_rejects_changed_sealed_receipt(tmp_path,monkeypatch):
    job,root,campaign,local=fixture(tmp_path);monkeypatch.setattr(gate,'live',lambda _:False)
    gate.write(local/'generation-0010/2026-08-04/receipt.json',{})
    with pytest.raises(ValueError,match='receipt changed'):gate.inspect_predecessor(job)
