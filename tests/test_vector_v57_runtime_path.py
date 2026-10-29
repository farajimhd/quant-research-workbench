from pathlib import Path
import pytest
from research.vectorized_backtest.v57 import runtime_path


def test_transient_probe_rechecks_original_guard(tmp_path,monkeypatch,caplog):
    monkeypatch.setattr(runtime_path.runtime,'ROOT',tmp_path)
    monkeypatch.setattr(runtime_path,'sleep',lambda _:None)
    original=runtime_path.runtime.require_runtime
    calls=[]
    def probe(path):
        calls.append(path)
        if len(calls)==1:raise ValueError('transient probe failure')
        return original(path)
    monkeypatch.setattr(runtime_path.runtime,'require_runtime',probe)
    target=tmp_path/'generation-0006'/'2026-09-02'
    assert runtime_path.require_runtime(target)==target.resolve()
    assert target.is_dir() and len(calls)==2
    assert 'transient probe failure' in caplog.text


@pytest.mark.parametrize('target_kind',['root','outside'])
def test_persistent_invalid_path_never_created(tmp_path,monkeypatch,target_kind):
    root=tmp_path/'authority';root.mkdir()
    monkeypatch.setattr(runtime_path.runtime,'ROOT',root)
    monkeypatch.setattr(runtime_path,'sleep',lambda _:None)
    target=root if target_kind=='root' else tmp_path/'outside'
    with pytest.raises(RuntimeError,match='outside authority'):
        runtime_path.require_runtime(target)
    if target_kind=='outside':assert not target.exists()


def test_missing_root_does_not_create_fallback(tmp_path,monkeypatch):
    root=tmp_path/'missing'
    monkeypatch.setattr(runtime_path.runtime,'ROOT',root)
    monkeypatch.setattr(runtime_path,'sleep',lambda _:None)
    with pytest.raises(RuntimeError):runtime_path.require_runtime(root/'child')
    assert not root.exists()
