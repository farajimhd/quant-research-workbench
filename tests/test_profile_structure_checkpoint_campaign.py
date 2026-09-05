import json
import os
import sys
from types import SimpleNamespace

import pytest

from scripts import profile_structure_checkpoint_campaign as profile


def test_owned_subprocess_completes_without_poll_interval_bias(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, 'campaign_processes', lambda _: [])
    result = profile.run_owned([sys.executable, '-c', 'print("profile child completed")'], dict(os.environ), tmp_path,
                               tmp_path / 'child.log', timeout=10, grace=10)
    assert result['wall_seconds'] < 5
    assert 'profile child completed' in (tmp_path / 'child.log').read_text()


def test_process_scan_includes_orphan_workers_and_excludes_neighbor_runtime(tmp_path, monkeypatch):
    import psutil
    class Process:
        def __init__(self, pid, args):
            self.pid = pid
            self.info = {'name': 'structure_checkpoint_campaign_v18.exe'}
            self.args = args
        def cmdline(self):
            return self.args
    own = str(tmp_path.resolve())
    monkeypatch.setattr(psutil, 'process_iter', lambda _: [Process(123, ['--runtime-dir', own+'\\workers\\worker-01']), Process(124, ['--runtime-dir', own+'-other'])])
    assert profile.campaign_processes(tmp_path) == [123]


def test_graceful_stop_waits_for_processes_not_status(tmp_path, monkeypatch):
    (tmp_path / 'campaign-manifest.json').write_text(json.dumps({'checkpoint_set_id': 'production'}))
    states = iter([[7, 8], [8], []])
    monkeypatch.setattr(profile, 'campaign_processes', lambda path: next(states))
    monkeypatch.setattr(profile.time, 'sleep', lambda _: None)
    profile.graceful_stop(tmp_path, 60)
    control = json.loads((tmp_path / 'campaign-control.json').read_text())
    assert control['action'] == 'stop_graceful'
    assert control['checkpoint_set_id'] == 'production'


def test_graceful_deadline_leaves_request_without_force(tmp_path, monkeypatch):
    (tmp_path / 'campaign-manifest.json').write_text(json.dumps({'checkpoint_set_id': 'production'}))
    monkeypatch.setattr(profile, 'campaign_processes', lambda path: [9])
    with pytest.raises(RuntimeError, match='no workers killed'):
        profile.graceful_stop(tmp_path, -1)
    assert json.loads((tmp_path / 'campaign-control.json').read_text())['action'] == 'stop_graceful'


def test_profile_requires_complete_unique_hash_coverage(tmp_path):
    worker = tmp_path / 'workers/worker-000'
    worker.mkdir(parents=True)
    row = dict(event='campaign_day_profile', ticker='JUNS', session_date='2026-08-21', checkpoint_sha256='abc', events=25,
               fetch_ms=1, apply_ms=2, prepare_ms=3, certify_ms=4, persist_ms=5, total_ms=15)
    log = worker / 'worker.log'
    log.write_text('informational line\n'+json.dumps(row)+'\n')
    result = profile.summarize_profiles(tmp_path, {'JUNS'})
    assert result['events'] == 25
    assert result['checkpoint_hashes'] == {'JUNS:2026-08-21': 'abc'}
    with pytest.raises(RuntimeError, match='incomplete'):
        profile.summarize_profiles(tmp_path, {'JUNS', 'SUGP'})
    log.write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n')
    with pytest.raises(RuntimeError, match='Duplicate'):
        profile.summarize_profiles(tmp_path, {'JUNS'})


def test_database_isolation_never_accepts_production_name(monkeypatch):
    calls = []
    monkeypatch.setattr(profile, 'clickhouse_request', lambda env, sql: calls.append(sql) or SimpleNamespace(text=''))
    env = {}
    with pytest.raises(RuntimeError, match='Invalid isolated'):
        profile.create_profile_database(env, 'q_live')
    assert not calls
    profile.create_profile_database(env, 'qmd_structure_profile_abc')
    assert env['QMD_CLICKHOUSE_DATABASE'] == 'qmd_structure_profile_abc'
    assert env['QMD_HISTORY_STRUCTURE_DATABASE'] == 'qmd_structure_profile_abc'


def test_http_success_with_error_body_does_not_configure_database(monkeypatch):
    monkeypatch.setattr(profile, 'clickhouse_request', lambda env, sql: SimpleNamespace(text='Code: 241 memory limit'))
    env = {}
    with pytest.raises(RuntimeError, match='error body'):
        profile.create_profile_database(env, 'qmd_structure_profile_abc')
    assert not env
