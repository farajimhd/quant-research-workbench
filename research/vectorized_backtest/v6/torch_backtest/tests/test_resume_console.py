"""Display ownership must not alter immutable optimization state."""

import json
from pathlib import Path
from unittest.mock import Mock

from rich.console import Console
from io import StringIO
import pytest

from research.vectorized_backtest.v6.torch_backtest.optimization_ui import SearchPanel, render_search
from research.vectorized_backtest.v6.torch_backtest.resume_console import resume_arguments, verify_worker, read_snapshot


def test_live_handoff_stops_old_renderer_once(tmp_path):
    panel = SearchPanel(tmp_path, console=Console(file=StringIO()))
    live = Mock()
    panel.live = live
    panel.close_live()
    panel.__exit__(None, None, None)
    live.stop.assert_called_once()
    assert panel.live is None


def test_observer_permission_error_retains_worker_and_can_recover(tmp_path):
    path = Mock()
    path.read_text.side_effect = [PermissionError('sharing violation'), '{"status":"training"}']
    state, error = read_snapshot(path)
    assert state is None and 'PermissionError' in error
    assert read_snapshot(path) == ({'status': 'training'}, None)


def test_observer_missing_or_incomplete_snapshot_is_retryable(tmp_path):
    path = tmp_path / 'status.json'
    assert read_snapshot(path)[0] is None
    path.write_text('{')
    assert 'JSONDecodeError' in read_snapshot(path)[1]
    path.write_text('{"status":"training"}')
    assert read_snapshot(path) == ({'status': 'training'}, None)


def test_exact_resume_changes_only_output_and_plain(tmp_path):
    args = ['--execute', '--seed', '20261005', '--continue-training', 'old',
            '--output', 'existing/experiment', '--population', '128']
    (tmp_path / 'command.json').write_text(json.dumps(args))
    observed = resume_arguments(tmp_path)
    assert observed == args[:5] + args[7:] + ['--resume', str(tmp_path / 'experiment'), '--plain']
    assert json.loads((tmp_path / 'command.json').read_text()) == args


def test_resume_rejects_changed_immutable_worker(tmp_path, monkeypatch):
    from research.vectorized_backtest.v6.torch_backtest import resume_console
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash

    monkeypatch.setattr(resume_console, 'DEFAULT', tmp_path)
    checkout = tmp_path / 'worker'
    checkout.mkdir()
    source = checkout / 'runner.py'
    source.write_text('certified source')
    marker = tmp_path / 'deployments' / 'worker'
    marker.mkdir(parents=True)
    (marker / 'deployment.json').write_text(json.dumps(dict(
        v4_code_hash='sealed', files={'runner.py': file_hash(source)})))
    experiment = tmp_path / 'job' / 'experiment'
    experiment.mkdir(parents=True)
    (experiment / 'identity.json').write_text('{"code_hash":"sealed"}')
    (experiment / 'checkpoint.json').write_text('{}')
    assert verify_worker(checkout, experiment.parent)['code_hash'] == 'sealed'
    source.write_text('changed source')
    with pytest.raises(ValueError, match='Immutable worker source changed'):
        verify_worker(checkout, experiment.parent)


@pytest.mark.parametrize('width,height', [(140, 40), (80, 24), (60, 16)])
def test_single_renderer_shows_saved_checkpoint_in_normal_and_compact(width, height):
    stream = StringIO()
    console = Console(file=stream, width=width, height=height, color_system=None)
    console.print(render_search(dict(status='training', completed_generations=4,
                                    focus='Generation 5', checkpoint='saved/checkpoint.json'),
                                12, width=width, height=height))
    assert 'not yet saved' not in stream.getvalue()
    assert '\x1b' not in stream.getvalue()
