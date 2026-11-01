import json
from pathlib import Path
from unittest.mock import Mock


def test_full_entry_dispatches_v62_global(monkeypatch,tmp_path):
    from research.vectorized_backtest.v62 import run_train,global_runner
    receipt=tmp_path/'deployment.json';receipt.write_text(json.dumps(dict(commit='test',files={})))
    job=tmp_path/'job.json';job.write_text(json.dumps(dict(version='v62-global-v1',code=str(tmp_path),campaign=str(tmp_path),deployment=str(receipt),commit='test',phase2_generations=200,training_days=['day'])))
    runner=Mock();monkeypatch.setattr(global_runner,'run',runner)
    monkeypatch.setattr('sys.argv',['run_train','--job',str(job)])
    run_train.main();runner.assert_called_once()
    assert runner.call_args.args[0]['version']=='v62-global-v1'


def test_global_check_delegates_for_v62(monkeypatch):
    from research.vectorized_backtest.v62 import full_common,global_contract
    check=Mock();monkeypatch.setattr(global_contract,'check',check)
    job=dict(version='v62-global-v1');full_common.check_job(job);check.assert_called_once_with(job)


def test_validation_flow_accepts_v62(monkeypatch):
    from types import SimpleNamespace
    from research.vectorized_backtest.v62 import generation_flow
    class ReachedPublication(Exception):pass
    def lane():raise ReachedPublication()
    monkeypatch.setattr(generation_flow,'Lane',lane)
    import pytest
    with pytest.raises(ReachedPublication):
        generation_flow.evolve(SimpleNamespace(job={'version':'v62-global-v1'}),None,[],['val'],None,None,None,None,None,None,None,None,None)
