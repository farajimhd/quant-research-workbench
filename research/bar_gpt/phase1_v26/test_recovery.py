import io,json
from types import SimpleNamespace
import pytest
from .reporting import Reporter,ReportingFailure,transport_failure
from . import supervisor


def test_transport_failure_preserves_journal_and_cause():
    reporter=Reporter.__new__(Reporter);reporter.global_step_offset=0;reporter.journal=io.StringIO()
    error=ConnectionResetError('local W&B service lost')
    def fail(_):raise error
    reporter.run=SimpleNamespace(log=fail)
    with pytest.raises(ReportingFailure) as caught:reporter.log({'train/loss':2.},59)
    assert caught.value.__cause__ is error
    assert json.loads(reporter.journal.getvalue())['step']==59
    assert not transport_failure(ValueError('configuration'))
    assert not transport_failure(OSError('unrelated IO'))
    reporter.run=SimpleNamespace(log=lambda _:(_ for _ in ()).throw(ValueError('bad metric')))
    with pytest.raises(ValueError):reporter.log({'train/loss':2.},60)


@pytest.mark.parametrize('exit_code,attempts',[(75,2),(1,1),(0,1)])
def test_supervisor_only_retries_owned_checkpointed_transport(monkeypatch,tmp_path,exit_code,attempts):
    commands=[]
    class Child:
        def __init__(self,command,**kwargs):
            commands.append(command.copy());self.pid=123;self.returncode=exit_code if len(commands)==1 else 0
            (tmp_path/'checkpoint_last.proof.json').write_text(json.dumps(dict(sha256='test-proof')))
            (tmp_path/'reporting-retry.json').write_text(json.dumps(dict(pid=self.pid,checkpoint_sha='test-proof')))
        def poll(self):return self.returncode
    monkeypatch.setattr(supervisor.subprocess,'Popen',Child)
    monkeypatch.setattr(supervisor.psutil,'Process',lambda pid:SimpleNamespace(create_time=lambda:1.))
    # Test runtime root is deliberately supplied beneath the required authority.
    monkeypatch.setattr(supervisor.Path,'is_relative_to',lambda *args:True)
    code=supervisor.supervise(['--output',str(tmp_path)],tmp_path/'console.log',delay=0)
    assert len(commands)==attempts
    assert code==(0 if exit_code==75 else exit_code)
    if attempts==2:assert commands[-1][-2:]==['--resume',str(tmp_path/'checkpoint_last.pt')]
    (tmp_path/'STOP').write_text('human stop')
    assert supervisor.supervise(['--output',str(tmp_path)],tmp_path/'console.log',delay=0)==0
    assert len(commands)==attempts
