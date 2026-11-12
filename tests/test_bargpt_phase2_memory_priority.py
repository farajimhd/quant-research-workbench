import pytest
from types import SimpleNamespace
from research.bar_gpt.v5 import run_phase2_memory_priority as module


def test_coordinator_residency_respects_ram_and_query_reservations():
    gib=1024**3
    assert module.reserve_ok(128*gib,5*gib,48*gib,64*gib)
    assert not module.reserve_ok(110*gib,5*gib,48*gib,64*gib)


def test_pid_reuse_and_training_process_are_rejected_before_any_mutation(monkeypatch):
    process=SimpleNamespace(create_time=lambda:100.,cmdline=lambda:['python','run_encoder_v3.py'])
    monkeypatch.setattr(module.psutil,'Process',lambda pid:process)
    with pytest.raises(ValueError,match='identity changed'):
        module.set_memory_priority(1,99,1000,0,0,apply=True)
    with pytest.raises(ValueError,match='not the phase-two'):
        module.set_memory_priority(1,100,1000,0,0,apply=True)


def test_low_headroom_rejected_before_openprocess(monkeypatch):
    process=SimpleNamespace(create_time=lambda:100.,cmdline=lambda:['python','-m','research.bar_gpt.v5.phase2_ar_shards.run'],
        nice=lambda:16384,memory_info=lambda:SimpleNamespace(private=500,rss=100))
    monkeypatch.setattr(module.psutil,'Process',lambda pid:process)
    with pytest.raises(MemoryError):module.set_memory_priority(1,100,1000,400,200,apply=True)
