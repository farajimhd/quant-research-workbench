from types import SimpleNamespace
import pytest
from ..v5.reporting_v1 import Reporter


@pytest.mark.parametrize('state,expected', [('complete', 0), ('qualification_complete', 0), ('failed', 1), ('stopped', 1)])
def test_wandb_exit_status_preserves_local_termination_reason(monkeypatch, tmp_path, state, expected):
    import wandb
    monkeypatch.setattr(wandb, 'Artifact', lambda *a, **kw: SimpleNamespace(add_file=lambda p: None))
    r = Reporter.__new__(Reporter)
    r.root = tmp_path; r.stop = SimpleNamespace(set=lambda: None)
    r.thread = SimpleNamespace(join=lambda **kw: None)
    r.journal = SimpleNamespace(closed=True); r.writer_lease = None
    recorded = {}
    r.update = lambda **kw: recorded.update(kw)
    r.publish = lambda: None
    r.run = SimpleNamespace(log_artifact=lambda a: None, finish=lambda **kw: recorded.update(kw))
    r.finish(state)
    assert recorded['state'] == state
    assert recorded['exit_code'] == expected
