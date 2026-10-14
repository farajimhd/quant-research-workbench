"""Bounded shutdown tests; never invoke an acquisition provider."""
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import acquisition


def test_forced_windows_cleanup_targets_tree_and_waits():
    child=Mock(pid=12345)
    child.poll.return_value=None
    child.wait.side_effect=[subprocess.TimeoutExpired('fixture',10),1]
    with patch.object(acquisition.os,'name','nt'),patch.object(acquisition.subprocess,'run') as kill:
        acquisition.stop_owned_ingestion(child)
    assert kill.call_args.args[0]==['taskkill','/PID','12345','/T','/F']
    assert kill.call_args.kwargs['check'] is True
    assert child.wait.call_count==2
    child.terminate.assert_not_called()


@pytest.mark.skipif(os.name!='nt',reason='Windows process-tree integration')
def test_real_owned_parent_and_grandchild_are_drained_when_signal_unavailable():
    psutil=pytest.importorskip('psutil')
    code="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    parent=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.PIPE,
        text=True,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    grandchild=None
    try:
        grandchild=int(parent.stdout.readline())
        assert psutil.pid_exists(grandchild)
        owned=SimpleNamespace(pid=parent.pid,poll=parent.poll,wait=parent.wait,
            send_signal=Mock(side_effect=OSError('no console available')))
        acquisition.stop_owned_ingestion(owned)
        assert parent.poll() is not None
        assert not psutil.pid_exists(grandchild)
    finally:
        if parent.poll() is None:
            subprocess.run(['taskkill','/PID',str(parent.pid),'/T','/F'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
            parent.wait(timeout=10)
        if grandchild is not None and psutil.pid_exists(grandchild):
            process=psutil.Process(grandchild);process.kill();process.wait(timeout=10)
        parent.stdout.close()
