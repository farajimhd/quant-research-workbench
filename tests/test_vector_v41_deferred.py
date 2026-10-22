import copy,json,subprocess,sys
from pathlib import Path
import pytest
from research.vectorized_backtest.v41 import deferred_launch as queue


def fixture(tmp_path):
    root=tmp_path/'v41';root.mkdir();old=tmp_path/'v40';old.mkdir();campaign=old/'campaign';campaign.mkdir()
    owner=dict(pid=123,creation_time=456.,campaign=str(campaign),source_sha256='a'*64,commit='abc')
    (old/'active-campaign.json').write_text(json.dumps(owner))
    return dict(root=str(root),predecessor=dict(root=str(old),campaign=str(campaign),owner=owner))


def test_waiting_tracks_exact_identity_and_does_not_acquire_cuda(tmp_path,monkeypatch):
    job=fixture(tmp_path);monkeypatch.setattr(queue,'live',lambda r:True)
    monkeypatch.setattr(queue.psutil,'Process',lambda pid:type('P',(),{'children':lambda self,recursive:[]})())
    assert queue.predecessor(job) is None
    assert json.loads((Path(job['root'])/'diagnostics/predecessor-descendants.json').read_bytes())==[]


def test_changed_pointer_fails_closed(tmp_path):
    job=fixture(tmp_path);path=Path(job['predecessor']['root'])/'active-campaign.json';owner=json.loads(path.read_bytes());owner['creation_time']+=1;path.write_text(json.dumps(owner))
    with pytest.raises(ValueError,match='pointer changed'):queue.predecessor(job)


def test_exited_owner_without_sealed_success_does_not_launch(tmp_path,monkeypatch):
    job=fixture(tmp_path);monkeypatch.setattr(queue,'live',lambda r:False)
    with pytest.raises(RuntimeError,match='completion evidence'):queue.predecessor(job)
    campaign=Path(job['predecessor']['campaign']);(campaign/'exit.json').write_text('{"exit_code":130}')
    with pytest.raises(RuntimeError,match='successfully'):queue.predecessor(job)


def test_live_descendant_blocks_even_after_controller_exit(tmp_path,monkeypatch):
    job=fixture(tmp_path);folder=Path(job['root'])/'diagnostics';folder.mkdir();(folder/'predecessor-descendants.json').write_text('[{"pid":9,"creation_time":10}]')
    monkeypatch.setattr(queue,'live',lambda r:r['pid']==9)
    assert queue.predecessor(job) is None


def test_queue_import_does_not_import_torch():
    output=subprocess.check_output([sys.executable,'-B','-c',"import sys;import research.vectorized_backtest.v41.deferred_launch;assert 'torch' not in sys.modules;print('ok')"],text=True)
    assert output.strip()=='ok'
