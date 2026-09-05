import json
from types import SimpleNamespace
from scripts import profile_warm_structure_replay as warm


def test_hard_deadline_kills_only_owned_child_and_keeps_diagnostics(tmp_path, monkeypatch):
    import psutil
    class Child:
        pid=76543
        returncode=None
        killed=False
        def poll(self): return self.returncode
        def kill(self): self.killed=True;self.returncode=-9
        def wait(self, timeout): return self.returncode
    child=Child()
    monkeypatch.setattr(warm.subprocess,'Popen',lambda *a,**k:child)
    monkeypatch.setattr(psutil,'Process',lambda _:SimpleNamespace())
    (tmp_path/'SDOT.status.json').write_text(json.dumps({'phase':'refresh_unified_tracks','events':20}))
    result=warm.run_probe('unused.exe','SDOT','2026-05-05','set',tmp_path,{},10,0,grace=0)
    assert child.killed
    assert (tmp_path/'SDOT.stop').exists()
    assert result['status']=='failed'
    assert result['launcher_stop_reason']=='hard_deadline_single_event_or_io'
    assert result['last_status']['phase']=='refresh_unified_tracks'
    assert (tmp_path/'SDOT-result.json').exists()


def test_success_requires_final_report_and_preserves_parity(tmp_path, monkeypatch):
    import psutil
    child=SimpleNamespace(pid=76543,returncode=0,poll=lambda:0)
    monkeypatch.setattr(warm.subprocess,'Popen',lambda *a,**k:child)
    monkeypatch.setattr(psutil,'Process',lambda _:SimpleNamespace())
    missing=warm.run_probe('unused.exe','SDOT','2026-05-05','set',tmp_path,{},10,10)
    assert missing['status']=='failed'
    (tmp_path/'SDOT.json').write_text(json.dumps({'status':'completed','prefix_parity':True,'processed_events':10}))
    result=warm.run_probe('unused.exe','SDOT','2026-05-05','set',tmp_path,{},10,10)
    assert result['status']=='completed'
    assert result['prefix_parity'] is True
