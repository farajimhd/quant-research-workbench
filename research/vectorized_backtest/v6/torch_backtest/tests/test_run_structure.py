import json
from pathlib import Path
import pytest
from research.vectorized_backtest.v6.torch_backtest import run_structure as module


def inputs(root):
    root.mkdir()
    for i in range(30):
        day=f'2026-08-{i+1:02d}';folder=root/day;folder.mkdir()
        (folder/'complete.json').write_text(json.dumps(dict(ready_for_replay=True,validation_opened=False,
            identity=dict(session=dict(day=day)))))
    return root


def test_controller_seals_thirty_receipts_and_bounds_children(tmp_path,monkeypatch):
    source=inputs(tmp_path/'inputs');calls=[]
    class Child:
        def __init__(self,args,**kwargs):
            self.pid=100+len(calls);calls.append(args)
            folder=Path(args[args.index('--output')+1]);(folder/'targets.npy').write_bytes(b'exact-levels')
            (folder/'complete.json').write_text(json.dumps(dict(
                status='complete',day=folder.name,validation_opened=False,
                input_receipt_sha256=module.file_hash(source/folder.name/'complete.json'),
                target_projection_scope='ranked-top-n-only; full causal bar history retained',
                files={'targets.npy':module.file_hash(folder/'targets.npy')},listing_ids=[1,2])))
        def poll(self):return 0
    monkeypatch.setattr(module.subprocess,'Popen',Child)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    monkeypatch.setattr(module,'validate_workers',lambda *args:None)
    module.run(source,tmp_path/'run',session_workers=2,ticker_workers=4)
    receipt=json.loads((tmp_path/'run'/'complete.json').read_text())
    assert len(calls)==len(receipt['sessions'])==30
    assert all(args[-1]=='4' for args in calls)
    assert not receipt['validation_opened']
    (tmp_path/'run'/'complete.json').unlink()
    module.run(source,tmp_path/'run',session_workers=16,ticker_workers=8,resume=True)
    assert len(calls)==30  # No completed session was launched again.
    (tmp_path/'run'/'2026-08-01'/'targets.npy').write_bytes(b'changed')
    (tmp_path/'run'/'complete.json').unlink()
    with pytest.raises(ValueError,match='integrity'):
        module.run(source,tmp_path/'run',resume=True)


def test_controller_stops_dispatch_after_failed_child(tmp_path,monkeypatch):
    source=inputs(tmp_path/'inputs');calls=[]
    class Child:
        def __init__(self,args,**kwargs):self.pid=100+len(calls);calls.append(args)
        def poll(self):return 1
    monkeypatch.setattr(module.subprocess,'Popen',Child)
    monkeypatch.setattr(module,'validate_workers',lambda *args:None)
    with pytest.raises(RuntimeError,match='failed'):
        module.run(source,tmp_path/'run')
    assert len(calls)==2 and not (tmp_path/'run'/'complete.json').exists()
    assert len(json.loads((tmp_path/'run'/'failure.json').read_text())['queued'])==28
