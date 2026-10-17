from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v26.host_memory import wait_for_headroom


def test_admission_waits_until_headroom_recovers(tmp_path):
    available=iter([100,100,200])
    events=[];waits=[]
    wait_for_headroom(tmp_path,lambda **v:events.append(v),reserve_gib=128,admission_gib=32,
        snapshot=lambda:SimpleNamespace(available=next(available)*1024**3),sleep=waits.append)
    assert waits==[2.]
    assert events[0]['stage']=='Waiting for RAM headroom'


def test_admission_honors_campaign_stop(tmp_path):
    (tmp_path/'STOP').touch()
    with pytest.raises(InterruptedError):
        wait_for_headroom(tmp_path/'training',lambda **v:None,
            snapshot=lambda:SimpleNamespace(available=0),sleep=lambda _:pytest.fail('must stop'))


def test_cpu_disabled_guard_does_not_probe(tmp_path):
    wait_for_headroom(tmp_path,lambda **v:None,reserve_gib=0,
        snapshot=lambda:pytest.fail('disabled guard'))
