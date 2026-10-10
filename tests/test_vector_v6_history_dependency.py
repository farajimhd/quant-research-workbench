import json
import pytest
from research.vectorized_backtest.v6.torch_backtest.run_history import profile_dependency_released


def receipts(path, code=4294967295):
    (path/'exit.json').write_text(json.dumps({'exit_code':code}))
    (path/'active.json').write_text(json.dumps({'pid':12345,'creation_time':100.0}))
    (path/'user-stop.json').write_text(json.dumps({'pid':12345,'verified_creation_time':100.0,
        'reason':'User requested profiler stop; measurements partial'}))


def test_verified_user_stop_releases_dependency(tmp_path,monkeypatch):
    monkeypatch.setattr('psutil.pid_exists',lambda pid:False)
    assert not profile_dependency_released(tmp_path)
    receipts(tmp_path)
    stop=tmp_path/'user-stop.json'
    stop.write_text(stop.read_text(),encoding='utf-8-sig')
    assert profile_dependency_released(tmp_path)


def test_stop_conflict_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr('psutil.pid_exists',lambda pid:False)
    receipts(tmp_path)
    (tmp_path/'user-stop.json').write_text(json.dumps({'pid':54321,'verified_creation_time':100.0}))
    with pytest.raises(ValueError):profile_dependency_released(tmp_path)


def test_live_owner_rejected(tmp_path,monkeypatch):
    receipts(tmp_path,0)
    monkeypatch.setattr('psutil.pid_exists',lambda pid:True)
    class Owner:
        def create_time(self):return 100.0
    monkeypatch.setattr('psutil.Process',lambda pid:Owner())
    with pytest.raises(ValueError,match='live owner'):profile_dependency_released(tmp_path)


def test_existing_market_source_row():
    import polars as pl
    from research.vectorized_backtest.v6.torch_backtest.materialize_history import physical_source_rows
    frame=pl.DataFrame({'source_row':[0,1],'listing':[5,9]})
    assert physical_source_rows(frame).equals(frame)
    with pytest.raises(ValueError,match='physical row order'):
        physical_source_rows(frame.with_columns(pl.col('source_row')+1))
