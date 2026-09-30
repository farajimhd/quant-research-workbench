import pytest
from research.rl_trading.v6.telemetry import PeriodicProgress


def test_progress_emits_at_cadence_without_duplicate_upload_requests():
    now=[0.]
    emitted=[]
    callback=PeriodicProgress(emitted.append,seconds=60.,clock=lambda:now[0])
    callback({'step':1})
    now[0]=59.9
    callback({'step':2})
    assert not emitted
    now[0]=60.
    callback({'step':3})
    callback({'step':4})
    now[0]=120.
    callback({'step':5})
    assert emitted==[{'step':3},{'step':5}]
    with pytest.raises(ValueError):
        PeriodicProgress(emitted.append,seconds=0.)
