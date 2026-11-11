"""Admission follows the current trainer and never writes its state."""

import json
import time

from research.bar_gpt.v5.phase2_shards.training_guard import TrainingGuard


def test_training_validation_and_stale_status_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(TrainingGuard, "activity_limit", lambda self: (4, "live trainer"))
    attempt = tmp_path / "train-03"
    attempt.mkdir()
    controller = tmp_path / "progress.json"
    controller.write_text(json.dumps(dict(child=dict(output=str(attempt)))))
    guard = TrainingGuard(tmp_path, 64, 4)
    assert guard.limit == 4  # Startup cannot admit full work before reading evidence.
    for stage, age, expected in (
        ("training", 0, 64),
        ("validation", 0, 4),
        ("learning probe", 0, 4),
        ("training", 60, 4),
    ):
        status = attempt / "progress.json"
        status.write_text(
            json.dumps(dict(stage=stage, state="running", updated=time.time() - age))
        )
        before = status.read_bytes(), controller.read_bytes()
        guard.checked = float("-inf")
        assert guard.refresh() == expected
        assert before == (status.read_bytes(), controller.read_bytes())
    controller.write_text(json.dumps(dict(state="complete")))
    guard.checked = float("-inf")
    assert guard.refresh() == 64


def test_terminal_trainer_without_live_child_releases_admission(tmp_path, monkeypatch):
    """An intentional learning stop must not cap the repair forever as stale."""
    import psutil

    controller = tmp_path / "progress.json"
    controller.write_text(
        json.dumps(dict(state="early_stopped", child=dict(pid=1234, created=100.0)))
    )
    monkeypatch.setattr(psutil, "pid_exists", lambda _: False)
    before = controller.read_bytes()
    guard = TrainingGuard(tmp_path, 16, 4)
    assert guard.refresh() == 16 and "verified inactive" in guard.reason
    assert controller.read_bytes() == before


def test_terminal_label_alone_does_not_prove_trainer_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(TrainingGuard, "activity_limit", lambda self: (4, "live trainer"))
    """Retain conservative admission if the same trainer still exists."""
    import psutil
    from types import SimpleNamespace

    attempt = tmp_path / "train-04"
    attempt.mkdir()
    (tmp_path / "progress.json").write_text(
        json.dumps(
            dict(
                state="early_stopped",
                child=dict(pid=1234, created=100.0, output=str(attempt)),
            )
        )
    )
    (attempt / "progress.json").write_text(
        json.dumps(dict(state="running", stage="validation", updated=time.time() - 100))
    )
    monkeypatch.setattr(psutil, "pid_exists", lambda _: True)
    monkeypatch.setattr(
        psutil, "Process", lambda _: SimpleNamespace(create_time=lambda: 100.0)
    )
    assert TrainingGuard(tmp_path, 16, 4).refresh() == 4


def test_stale_activity_and_pressure(tmp_path, monkeypatch):
    import psutil
    from types import SimpleNamespace
    path=tmp_path/'progress.json'
    path.write_text(json.dumps(dict(state='failed',stage='measuring',updated=0)))
    original=path.read_bytes()
    monkeypatch.setattr(psutil,'process_iter',lambda attrs: [])
    monkeypatch.setattr(psutil,'virtual_memory',lambda: SimpleNamespace(available=130*1024**3))
    monkeypatch.setattr(psutil,'cpu_percent',lambda interval=None,percpu=True: [25]*128)
    assert TrainingGuard(tmp_path,48,4).refresh()==48
    process=SimpleNamespace(info={'name':'python.exe'},pid=123,cmdline=lambda:['python','run_encoder_v3.py'],create_time=lambda:456)
    monkeypatch.setattr(psutil,'process_iter',lambda attrs: [process])
    assert TrainingGuard(tmp_path,48,4).refresh()==4
    replacement=tmp_path.parent/(tmp_path.name+'-replacement');replacement.mkdir()
    (replacement/'progress.json').write_text(json.dumps(dict(state='running',stage='training',updated=time.time())))
    process.cmdline=lambda:['python','run_encoder_v3.py','--output',str(replacement)]
    assert TrainingGuard(tmp_path,48,4).refresh()==48
    (replacement/'progress.json').write_text(json.dumps(dict(state='running',stage='validation',updated=time.time())))
    assert TrainingGuard(tmp_path,48,4).refresh()==4
    def denied():raise psutil.AccessDenied(123)
    process.cmdline=denied
    assert TrainingGuard(tmp_path,48,4).refresh()==4
    monkeypatch.setattr(psutil,'process_iter',lambda attrs: [])
    monkeypatch.setattr(psutil,'cpu_percent',lambda interval=None,percpu=True: [90]*128)
    assert TrainingGuard(tmp_path,48,4).refresh()==4
    monkeypatch.setattr(psutil,'cpu_percent',lambda interval=None,percpu=True: [25]*128)
    monkeypatch.setattr(psutil,'virtual_memory',lambda: SimpleNamespace(available=70*1024**3))
    assert TrainingGuard(tmp_path,48,4).refresh()==4
    assert path.read_bytes()==original


def test_shared_observer_reuses_decision(tmp_path,monkeypatch):
    monkeypatch.setenv('BARGPT_ADMISSION_CACHE',str(tmp_path/'observer.json'))
    calls=[]
    monkeypatch.setattr(TrainingGuard,'scan_activity',lambda self:(calls.append(1) or (48,'safe')))
    for _ in range(48):assert TrainingGuard(tmp_path,48,4).activity_limit()==(48,'safe')
    assert len(calls)==1
