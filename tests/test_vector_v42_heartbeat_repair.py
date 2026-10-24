import json
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch

import pytest
from research.vectorized_backtest.v42 import heartbeat_repair as repair
from research.vectorized_backtest.v27.progress_io import shared_binary


@pytest.fixture
def root():
    parent = Path('D:/TradingML/runtimes') if os.name == 'nt' else Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix='v42-heartbeat-test-', dir=parent) as directory:
        yield Path(directory)


def test_concurrent_reader_keeps_complete_snapshot_during_atomic_replacement(root):
    p = root / 'heartbeat.json'
    repair.publish(root, p, {'epoch': 1})
    errors = []

    def replace():
        try:
            repair.publish(root, p, {'epoch': 2})
        except BaseException as error:
            errors.append(error)

    writer = threading.Thread(target=replace)
    with shared_binary(p) as held:
        writer.start()
        assert json.load(held) == {'epoch': 1}
    writer.join(8)
    assert not writer.is_alive() and not errors
    assert repair.read(p) == {'epoch': 2}


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows file sharing')
def test_retry_outlasts_original_short_sharing_window(root):
    p = root / 'heartbeat.json'
    repair.publish(root, p, {'epoch': 1})
    held = p.open('rb')
    release = threading.Thread(target=lambda: (time.sleep(1.1), held.close()))
    release.start()
    try:
        repair.publish(root, p, {'epoch': 2})
    finally:
        release.join()
    assert repair.read(p) == {'epoch': 2}


def test_persistent_denial_is_bounded_and_preserves_old_file(root):
    p = root / 'heartbeat.json'
    repair.publish(root, p, {'epoch': 1})
    with patch.object(repair.os, 'replace', side_effect=PermissionError('denied')) as replace, \
            patch.object(repair.time, 'sleep'):
        with pytest.raises(PermissionError):
            repair.publish(root, p, {'epoch': 2})
        assert replace.call_count == 30
    assert repair.read(p) == {'epoch': 1}
    assert not list(root.glob('*.tmp'))


def test_invalid_json_and_escaped_paths_fail(root):
    p = root / 'bad.json'
    p.write_text('{')
    with pytest.raises(json.JSONDecodeError):
        repair.read(p)
    with pytest.raises(ValueError, match='escaped'):
        repair.publish(root, root.parent / 'outside.json', {})


def test_heartbeat_loop_retries_permission_without_faking_success():
    class Agent:
        node = 'workstation'

        def __init__(self):
            self.stop = threading.Event()
            self.calls = 0

        def progress(self):
            self.calls += 1
            if self.calls == 1:
                raise PermissionError('transient')
            self.stop.set()

    repair.install_progress(Agent)
    a = Agent()
    a.progress()
    assert a.calls == 2
